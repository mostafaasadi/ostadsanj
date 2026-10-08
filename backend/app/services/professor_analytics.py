import orjson
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models import Message, MessageProfessor, Annotation, Professor, ProfessorSummary
from app.config import MIN_MENTIONS_FOR_SCORE
from app.services.analysis_logger import log
from app.services.prompts import SUMMARY_SYSTEM_PROMPT, build_summary_user_prompt

ASPECT_FA = {
    "teaching_quality": "کیفیت تدریس",
    "knowledge": "دانش و تسلط",
    "grading": "نمره‌دهی",
    "fairness": "عدالت و انصاف",
    "responsiveness": "پاسخگویی",
    "respect": "اخلاق و احترام",
    "materials": "جزوه و منابع",
    "workload": "حجم تکالیف",
    "attendance": "حضور و غیاب",
    "exam_conduct": "برگزاری امتحان",
    "flexibility": "انعطاف‌پذیری",
    "discrimination": "تبعیض",
    "teaching": "تدریس",
    "communication": "ارتباط",
    "ethics": "اخلاق",
    "other": "سایر"
}

MIN_REPLY_CHARS = 25
BAYESIAN_M = 8


def _quality_filter():
    exact_ok = (MessageProfessor.match_method != "reply_context") & (MessageProfessor.needs_review == False)
    reply_ok = (
        (MessageProfessor.match_method == "reply_context")
        & (MessageProfessor.approved == True)
        & (Message.char_len >= MIN_REPLY_CHARS)
    )
    return exact_ok | reply_ok


def _ann_base(db: Session, professor_id: int):
    return (
        db.query(Annotation)
        .join(
            MessageProfessor,
            (MessageProfessor.message_id == Annotation.message_id)
            & (MessageProfessor.professor_id == Annotation.professor_id),
        )
        .join(Message, Message.id == Annotation.message_id)
        .filter(Annotation.professor_id == professor_id)
        .filter(_quality_filter())
    )


def _ann_msg_base(db: Session, professor_id: int):
    return (
        db.query(Annotation, Message)
        .join(
            MessageProfessor,
            (MessageProfessor.message_id == Annotation.message_id)
            & (MessageProfessor.professor_id == Annotation.professor_id),
        )
        .join(Message, Message.id == Annotation.message_id)
        .filter(Annotation.professor_id == professor_id)
        .filter(_quality_filter())
    )


def _reactions_total(msg) -> int:
    raw = getattr(msg, "reactions_json", None)
    if not raw:
        return 0
    try:
        data = orjson.loads(raw)
        return sum(int(v) for v in data.values())
    except Exception:
        return 0


def _confidence_level(count: int) -> str:
    if count >= 15:
        return "high"
    if count >= 5:
        return "medium"
    return "low"


def calculate_professor_score(db: Session, professor_id: int) -> dict:
    annotations = (
        _ann_base(db, professor_id)
        .filter(Annotation.is_relevant == True)
        .filter(Annotation.contains_opinion == True)
        .filter(Annotation.confidence >= 0.5)
        .all()
    )
    total_messages = (
        _ann_base(db, professor_id)
        .filter(Annotation.is_relevant == True)
        .count()
    )
    if not annotations:
        return {
            "overall_score": None,
            "message_count": 0,
            "total_messages": total_messages,
            "positive_count": 0,
            "negative_count": 0,
            "neutral_count": 0,
            "mixed_count": 0,
            "avg_confidence": 0,
            "has_enough_data": False
        }
    total_weighted_score = 0
    total_weight = 0
    positive_count = 0
    negative_count = 0
    neutral_count = 0
    mixed_count = 0
    total_confidence = 0
    for ann in annotations:
        weight = ann.confidence or 0.5
        total_weighted_score += (ann.sentiment_score or 0) * weight
        total_weight += weight
        total_confidence += weight
        if ann.sentiment == "positive":
            positive_count += 1
        elif ann.sentiment == "negative":
            negative_count += 1
        elif ann.sentiment == "neutral":
            neutral_count += 1
        elif ann.sentiment == "mixed":
            mixed_count += 1
    avg_score = total_weighted_score / total_weight if total_weight > 0 else 0
    avg_confidence = total_confidence / len(annotations) if annotations else 0
    message_count = len(annotations)
    has_enough_data = message_count >= MIN_MENTIONS_FOR_SCORE
    return {
        "overall_score": round(avg_score * 100, 1),
        "message_count": message_count,
        "total_messages": total_messages,
        "positive_count": positive_count,
        "negative_count": negative_count,
        "neutral_count": neutral_count,
        "mixed_count": mixed_count,
        "avg_confidence": round(avg_confidence, 2),
        "has_enough_data": has_enough_data
    }


def get_engagement(db: Session, professor_id: int) -> dict:
    rows = (
        _ann_msg_base(db, professor_id)
        .filter(Annotation.contains_opinion == True)
        .all()
    )
    total = 0
    with_reactions = 0
    for ann, msg in rows:
        n = _reactions_total(msg)
        total += n
        if n > 0:
            with_reactions += 1
    return {"total_reactions": total, "messages_with_reactions": with_reactions}


def get_professor_aspects(db: Session, professor_id: int) -> list:
    annotations = (
        _ann_base(db, professor_id)
        .filter(Annotation.is_relevant == True)
        .filter(Annotation.contains_opinion == True)
        .all()
    )
    aspect_data = {}
    for ann in annotations:
        try:
            aspects = orjson.loads(ann.aspects_json) if ann.aspects_json else []
        except Exception:
            aspects = []
        for aspect in aspects:
            aspect_name = aspect.get("aspect", "other")
            score = aspect.get("score", 0)
            sentiment = aspect.get("sentiment", "neutral")
            if aspect_name not in aspect_data:
                aspect_data[aspect_name] = {
                    "aspect": aspect_name,
                    "total_score": 0,
                    "total_weight": 0,
                    "count": 0,
                    "positive": 0,
                    "negative": 0,
                    "neutral": 0,
                    "evidences": []
                }
            aspect_data[aspect_name]["total_score"] += score
            aspect_data[aspect_name]["total_weight"] += 1
            aspect_data[aspect_name]["count"] += 1
            if sentiment == "positive":
                aspect_data[aspect_name]["positive"] += 1
            elif sentiment == "negative":
                aspect_data[aspect_name]["negative"] += 1
            else:
                aspect_data[aspect_name]["neutral"] += 1
            evidence = aspect.get("evidence", "")
            if evidence and len(aspect_data[aspect_name]["evidences"]) < 3:
                aspect_data[aspect_name]["evidences"].append(evidence[:100])
    result = []
    for name, data in aspect_data.items():
        avg_score = data["total_score"] / data["total_weight"] if data["total_weight"] > 0 else 0
        result.append({
            "aspect": name,
            "avg_score": round(avg_score, 2),
            "count": data["count"],
            "positive": data["positive"],
            "negative": data["negative"],
            "neutral": data["neutral"],
            "evidences": data["evidences"]
        })
    result.sort(key=lambda x: x["count"], reverse=True)
    return result


def get_professor_topics(db: Session, professor_id: int, limit: int = 20) -> list:
    professor = db.get(Professor, professor_id)
    if not professor:
        return []
    name_parts = set(professor.name.lower().replace("‌", " ").split())
    alias_parts = set()
    try:
        aliases = orjson.loads(professor.aliases_json) if professor.aliases_json else []
        for alias in aliases:
            alias_parts.update(alias.lower().replace("‌", " ").split())
    except Exception:
        pass
    all_name_parts = name_parts | alias_parts
    stop_words = {
        "استاد", "دکتر", "آقای", "خانم", "professor", "dr",
        "درس", "کلاس", "واحد", "ترم", "امسال", "پارسال"
    }
    annotations = (
        _ann_base(db, professor_id)
        .filter(Annotation.is_relevant == True)
        .all()
    )
    topic_counts = {}
    for ann in annotations:
        try:
            topics = orjson.loads(ann.topics_json) if ann.topics_json else []
        except Exception:
            topics = []
        for topic in topics:
            if not topic or len(topic.strip()) < 3:
                continue
            topic_clean = topic.strip()
            topic_lower = topic_clean.lower().replace("‌", " ")
            is_name = any(
                part in topic_lower
                for part in all_name_parts
                if len(part) > 2
            )
            if is_name:
                continue
            words = set(topic_lower.split())
            if words.issubset(stop_words):
                continue
            normalized = topic_clean.replace("‌", " ").strip()
            topic_counts[normalized] = topic_counts.get(normalized, 0) + 1
    sorted_topics = sorted(topic_counts.items(), key=lambda x: x[1], reverse=True)
    return [{"topic": t, "count": c} for t, c in sorted_topics[:limit]]


def get_professor_timeline(db: Session, professor_id: int) -> list:
    from app.utils import to_jalali_year_month
    annotations = (
        _ann_msg_base(db, professor_id)
        .filter(Annotation.is_relevant == True)
        .filter(Message.date.isnot(None))
        .all()
    )
    monthly_data = {}
    for ann, msg in annotations:
        date = msg.date
        month_key = date.strftime("%Y-%m")
        jalali_month = to_jalali_year_month(date)
        if month_key not in monthly_data:
            monthly_data[month_key] = {
                "month": month_key,
                "jalali_month": jalali_month,
                "total": 0,
                "positive": 0,
                "negative": 0,
                "neutral": 0,
                "mixed": 0,
                "score_sum": 0,
                "score_weight": 0
            }
        monthly_data[month_key]["total"] += 1
        if ann.sentiment == "positive":
            monthly_data[month_key]["positive"] += 1
        elif ann.sentiment == "negative":
            monthly_data[month_key]["negative"] += 1
        elif ann.sentiment == "neutral":
            monthly_data[month_key]["neutral"] += 1
        elif ann.sentiment == "mixed":
            monthly_data[month_key]["mixed"] += 1
        weight = ann.confidence or 0.5
        monthly_data[month_key]["score_sum"] += (ann.sentiment_score or 0) * weight
        monthly_data[month_key]["score_weight"] += weight
    result = []
    for month_key in sorted(monthly_data.keys()):
        data = monthly_data[month_key]
        avg_score = data["score_sum"] / data["score_weight"] if data["score_weight"] > 0 else 0
        result.append({
            "month": data["jalali_month"],
            "total": data["total"],
            "positive": data["positive"],
            "negative": data["negative"],
            "neutral": data["neutral"],
            "mixed": data["mixed"],
            "avg_score": round(avg_score * 100, 1)
        })
    return result


def get_top_messages(db: Session, professor_id: int, sentiment: str = "positive", limit: int = 5) -> list:
    query = (
        _ann_msg_base(db, professor_id)
        .filter(Annotation.is_relevant == True)
        .filter(Annotation.contains_opinion == True)
        .filter(Annotation.sentiment == sentiment)
    )
    if sentiment == "positive":
        query = query.order_by(Annotation.sentiment_score.desc())
    elif sentiment == "negative":
        query = query.order_by(Annotation.sentiment_score.asc())
    else:
        query = query.order_by(Annotation.confidence.desc())
    results = query.limit(limit).all()
    messages = []
    for ann, msg in results:
        messages.append({
            "message_id": msg.id,
            "text": msg.text_clean[:300],
            "date": msg.date.strftime("%Y-%m-%d") if msg.date else None,
            "sentiment_score": ann.sentiment_score,
            "confidence": ann.confidence,
            "severity": ann.severity,
            "key_point": ann.key_point or "",
            "topics": orjson.loads(ann.topics_json) if ann.topics_json else [],
            "reactions": _reactions_total(msg)
        })
    return messages


def get_high_severity_messages(db: Session, professor_id: int, limit: int = 10) -> list:
    results = (
        _ann_msg_base(db, professor_id)
        .filter(Annotation.is_relevant == True)
        .filter(Annotation.severity.in_(["medium", "high"]))
        .order_by(Annotation.severity.desc())
        .limit(limit)
        .all()
    )
    messages = []
    for ann, msg in results:
        messages.append({
            "message_id": msg.id,
            "text": msg.text_clean[:300],
            "date": msg.date.strftime("%Y-%m-%d") if msg.date else None,
            "sentiment": ann.sentiment,
            "severity": ann.severity,
            "confidence": ann.confidence,
            "key_point": ann.key_point or "",
            "reactions": _reactions_total(msg)
        })
    return messages


def get_key_points(db: Session, professor_id: int, limit: int = 100) -> list:
    rows = (
        _ann_base(db, professor_id)
        .filter(Annotation.contains_opinion == True)
        .filter(Annotation.key_point.isnot(None))
        .filter(Annotation.key_point != "")
        .limit(limit)
        .all()
    )
    return [r.key_point for r in rows]


def get_global_mean_score(db: Session) -> float:
    row = (
        db.query(
            func.sum(Annotation.sentiment_score * Annotation.confidence),
            func.sum(Annotation.confidence)
        )
        .join(
            MessageProfessor,
            (MessageProfessor.message_id == Annotation.message_id)
            & (MessageProfessor.professor_id == Annotation.professor_id),
        )
        .join(Message, Message.id == Annotation.message_id)
        .filter(Annotation.is_relevant == True)
        .filter(Annotation.contains_opinion == True)
        .filter(Annotation.confidence >= 0.5)
        .filter(_quality_filter())
        .first()
    )
    if row and row[1] and row[1] > 0:
        return row[0] / row[1]
    return 0.0


def get_all_professors_ranking(db: Session) -> list:
    professors = db.query(Professor).all()
    C = get_global_mean_score(db)
    ranking = []
    for prof in professors:
        score_data = calculate_professor_score(db, prof.id)
        total_matched = (
            db.query(func.count(MessageProfessor.id))
            .filter(MessageProfessor.professor_id == prof.id)
            .scalar() or 0
        )
        unique_users = (
            db.query(func.count(func.distinct(Message.author_hash)))
            .join(MessageProfessor, Message.id == MessageProfessor.message_id)
            .filter(MessageProfessor.professor_id == prof.id)
            .filter(Message.author_hash.isnot(None))
            .scalar() or 0
        )
        overall = score_data["overall_score"]
        v = score_data["message_count"]
        if overall is not None and v > 0:
            R = overall / 100.0
            rank_score = round(
                ((v / (v + BAYESIAN_M)) * R + (BAYESIAN_M / (v + BAYESIAN_M)) * C) * 100,
                1
            )
        else:
            rank_score = None
        ranking.append({
            "id": prof.id,
            "name": prof.name,
            "department": prof.department,
            "overall_score": overall,
            "rank_score": rank_score,
            "confidence_level": _confidence_level(v),
            "message_count": v,
            "total_matched": total_matched,
            "positive_count": score_data["positive_count"],
            "negative_count": score_data["negative_count"],
            "neutral_count": score_data["neutral_count"],
            "avg_confidence": score_data["avg_confidence"],
            "unique_users": unique_users,
            "has_enough_data": score_data["has_enough_data"]
        })

    def sort_key(x):
        rank = x.get("rank_score")
        if rank is None:
            return (1, 0)
        return (0, -rank)

    ranking.sort(key=sort_key)
    return ranking


def get_professor_full_profile(db: Session, professor_id: int) -> dict:
    professor = db.get(Professor, professor_id)
    if not professor:
        return None
    try:
        aliases = orjson.loads(professor.aliases_json)
    except Exception:
        aliases = []
    score_data = calculate_professor_score(db, professor_id)
    aspects = get_professor_aspects(db, professor_id)
    topics = get_professor_topics(db, professor_id)
    timeline = get_professor_timeline(db, professor_id)
    top_positive = get_top_messages(db, professor_id, "positive", 5)
    top_negative = get_top_messages(db, professor_id, "negative", 5)
    high_severity = get_high_severity_messages(db, professor_id, 10)
    key_points = get_key_points(db, professor_id, 50)
    engagement = get_engagement(db, professor_id)
    mp_links = db.query(MessageProfessor).filter(MessageProfessor.professor_id == professor_id).all()
    message_ids = [mp.message_id for mp in mp_links]
    first_date = None
    last_date = None
    unique_users = 0
    if message_ids:
        messages = db.query(Message).filter(Message.id.in_(message_ids)).all()
        dates = [m.date for m in messages if m.date]
        first_date = min(dates) if dates else None
        last_date = max(dates) if dates else None
        unique_users = len(set(m.author_hash for m in messages if m.author_hash))
    summary_row = db.query(ProfessorSummary).filter(ProfessorSummary.professor_id == professor_id).first()
    summary = None
    if summary_row:
        summary = {
            "summary_text": summary_row.summary_text,
            "strengths": orjson.loads(summary_row.strengths_json or "[]"),
            "weaknesses": orjson.loads(summary_row.weaknesses_json or "[]"),
            "warnings": orjson.loads(summary_row.warnings_json or "[]"),
            "model_name": summary_row.model_name,
            "generated_at": summary_row.generated_at.isoformat() if summary_row.generated_at else None
        }
    return {
        "id": professor.id,
        "name": professor.name,
        "department": professor.department,
        "aliases": aliases,
        "stats": {
            "total_messages": len(message_ids),
            "analyzed_messages": score_data["total_messages"],
            "opinion_messages": score_data["message_count"],
            "unique_users": unique_users,
            "first_date": first_date.strftime("%Y-%m-%d") if first_date else None,
            "last_date": last_date.strftime("%Y-%m-%d") if last_date else None,
            "total_reactions": engagement["total_reactions"],
            "messages_with_reactions": engagement["messages_with_reactions"]
        },
        "score": score_data,
        "aspects": aspects,
        "topics": topics,
        "timeline": timeline,
        "top_positive_messages": top_positive,
        "top_negative_messages": top_negative,
        "high_severity_messages": high_severity,
        "key_points": key_points,
        "summary": summary
    }


def generate_professor_summary(db: Session, professor_id: int, provider: str = "arvan", model: str = None):
    from app.services import ollama_client, arvan_client
    profile = get_professor_full_profile(db, professor_id)
    if not profile:
        return None
    score = profile["score"]
    if score["message_count"] < MIN_MENTIONS_FOR_SCORE:
        log("info", f"استاد {professor_id}: داده کافی برای خلاصه نیست", "summary")
        return None
    stats = profile["stats"]
    payload = {
        "name": profile["name"],
        "overall_score": score["overall_score"],
        "stats": {
            "total": score["message_count"],
            "positive": score["positive_count"],
            "negative": score["negative_count"],
            "mixed": score["mixed_count"],
            "unique_users": stats["unique_users"]
        },
        "date_from": stats["first_date"],
        "date_to": stats["last_date"],
        "aspects": [
            {"label": ASPECT_FA.get(a["aspect"], a["aspect"]), "avg": a["avg_score"], "count": a["count"]}
            for a in profile["aspects"][:8]
        ],
        "topics": [(t["topic"], t["count"]) for t in profile["topics"][:8]],
        "severity_count": len(profile["high_severity_messages"]),
        "reactions_total": stats.get("total_reactions", 0),
        "key_points": profile["key_points"][:40]
    }
    prompt = build_summary_user_prompt(payload)
    if provider == "arvan":
        result = arvan_client.chat(prompt, SUMMARY_SYSTEM_PROMPT, model=model)
    else:
        result = ollama_client.chat(prompt, SUMMARY_SYSTEM_PROMPT)
    if "error" in result:
        log("error", f"خلاصه استاد {professor_id} ناموفق: {result.get('error')}", "summary")
        return None
    row = db.query(ProfessorSummary).filter(ProfessorSummary.professor_id == professor_id).first()
    if not row:
        row = ProfessorSummary(professor_id=professor_id)
        db.add(row)
    summary_text = str(result.get("summary_text") or result.get("summary") or "")[:2000]
    row.summary_text = summary_text
    row.strengths_json = orjson.dumps(result.get("strengths", [])[:6]).decode()
    row.weaknesses_json = orjson.dumps(result.get("weaknesses", [])[:6]).decode()
    row.warnings_json = orjson.dumps(result.get("warnings", [])[:6]).decode()
    row.model_name = f"{provider}:{model or 'default'}"
    row.generated_at = datetime.utcnow()
    db.commit()
    log("success", f"خلاصه استاد {professor_id} ({profile['name']}) ذخیره شد", "summary")
    return {
        "professor_id": professor_id,
        "summary_text": row.summary_text,
        "strengths": result.get("strengths", []),
        "weaknesses": result.get("weaknesses", []),
        "warnings": result.get("warnings", [])
    }
