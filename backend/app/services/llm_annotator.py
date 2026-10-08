import re
import time
import orjson
from concurrent.futures import ThreadPoolExecutor, as_completed
from sqlalchemy.orm import Session
from app.models import Message, MessageProfessor, Annotation, Professor, Job
from app.services import ollama_client, arvan_client
from app.services.professor_analytics import generate_professor_summary
from app.services.analysis_logger import log
from app.services.text_cleaner import normalize_fa
from app.services.prompts import (
    ANALYSIS_SYSTEM_PROMPT as SYSTEM_PROMPT,
    build_analysis_user_prompt as build_analysis_prompt,
)

AMBIGUOUS_SURNAMES = {
    "سروری", "حیاتی", "غافلی", "منادی", "زمانی", "صادقی", "کریمی", "امینی",
    "محمدی", "حسینی", "رضایی", "اکبری", "قاسمی", "عباسی", "کاظمی", "هاشمی",
}

_ROSTER = None
_ROSTER_AT = 0.0
_ROSTER_TTL = 600.0

_BOUND = r"(?:^|[\s،,.!?؟؛;:()«»\-–—_])"
_TRAIL = r"(?:$|[\s،,.!?؟؛;:()«»\-–—_])"

REVIEW_USER_PROMPT_TEMPLATE = (
    "استاد: {professor_name}\n\n"
    "پیام والد:\n{parent_text}\n\n"
    "پیام فعلی (ریپلای):\n{message_text}\n\n"
    "آیا این ریپلای دربارهٔ {professor_name} است؟ "
    "فقط خروجی JSON با کلیدهای is_relevant (bool) و confidence (float 0-1) بده."
)


def _match_norm(s: str) -> str:
    s = normalize_fa(s or "").lower()
    for a, b in (("ئ", "ی"), ("ؤ", "و"), ("أ", "ا"), ("إ", "ا"), ("ۀ", "ه")):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip()


def _candidates(name: str, aliases: list, allow_ambiguous_surname: bool) -> list:
    outs = []
    full = _match_norm(name)
    if len(full) >= 4:
        outs.append(full)
    for al in aliases or []:
        aln = _match_norm(al)
        if len(aln) >= 4:
            outs.append(aln)
    parts = full.split(" ")
    if len(parts) >= 2:
        sur = parts[-1]
        if len(sur) >= 4 and (allow_ambiguous_surname or sur not in AMBIGUOUS_SURNAMES):
            outs.append(sur)
    return outs


def _roster(db: Session):
    global _ROSTER, _ROSTER_AT
    now = time.time()
    if _ROSTER is not None and (now - _ROSTER_AT) < _ROSTER_TTL:
        return _ROSTER
    rows = db.query(Professor.id, Professor.name, Professor.aliases_json).all()
    data = []
    for pid, pname, aliases_json in rows:
        try:
            aliases = orjson.loads(aliases_json or "[]")
        except Exception:
            aliases = []
        data.append((pid, _candidates(pname, aliases, True), _candidates(pname, aliases, False)))
    _ROSTER = data
    _ROSTER_AT = now
    return data


def _has_name(text_norm: str, cand: str) -> bool:
    return re.search(_BOUND + re.escape(cand) + _TRAIL, text_norm) is not None


def _misattributed_to_other(db: Session, professor_id: int, text_norm: str) -> bool:
    own = None
    others = []
    for pid, own_cands, other_cands in _roster(db):
        if pid == professor_id:
            own = own_cands
        else:
            others.append(other_cands)
    if not own:
        return False
    if any(_has_name(text_norm, c) for c in own):
        return False
    for cands in others:
        for c in cands:
            if _has_name(text_norm, c):
                return True
    return False


def analyze_message(message: Message, professor_name: str, parent_text: str = "", provider: str = "ollama", model: str = None, reactions: dict = None) -> dict:
    prompt = build_analysis_prompt(message.text_clean, professor_name, parent_text, reactions)
    if provider == "arvan":
        result = arvan_client.chat(prompt, SYSTEM_PROMPT, model=model)
    else:
        result = ollama_client.chat(prompt, SYSTEM_PROMPT)
    if "error" in result:
        return result
    message_type = result.get("message_type", "evaluation")
    default_opinion = message_type in ("evaluation", "complaint", "suggestion")
    raw_score = result.get("sentiment_score", 0)
    raw_conf = result.get("confidence", 0.5)
    try:
        sentiment_score = float(raw_score) if raw_score is not None else 0.0
    except (TypeError, ValueError):
        sentiment_score = 0.0
    try:
        confidence = float(raw_conf) if raw_conf is not None else 0.5
    except (TypeError, ValueError):
        confidence = 0.5
    sentiment_score = max(-1.0, min(1.0, sentiment_score))
    confidence = max(0.0, min(1.0, confidence))
    sentiment = result.get("sentiment", "neutral")
    if sentiment not in ("positive", "negative", "neutral", "mixed"):
        sentiment = "neutral"
    severity = result.get("severity", "none")
    if severity not in ("none", "low", "medium", "high"):
        severity = "none"
    aspects = result.get("aspects", [])
    if not isinstance(aspects, list):
        aspects = []
    topics = result.get("topics", [])
    if not isinstance(topics, list):
        topics = []
    return {
        "is_relevant": bool(result.get("is_relevant", True)),
        "message_type": message_type,
        "contains_opinion": bool(result.get("contains_opinion", default_opinion)),
        "sentiment": sentiment,
        "sentiment_score": sentiment_score,
        "aspects": aspects,
        "topics": topics,
        "key_point": str(result.get("key_point", "") or "")[:300],
        "severity": severity,
        "confidence": confidence,
    }


def save_annotation(db: Session, message: Message, professor_id: int, analysis: dict, provider: str = "ollama", model: str = None):
    existing = db.query(Annotation).filter(
        Annotation.message_id == message.id,
        Annotation.professor_id == professor_id,
        Annotation.analyzer_name == "llm_basic"
    ).first()
    if existing:
        db.delete(existing)
        db.commit()
    model_name = model or "default"
    annotation = Annotation(
        message_id=message.id,
        professor_id=professor_id,
        analyzer_name="llm_basic",
        analyzer_version="v4",
        model_name=f"{provider}:{model_name}",
        prompt_version="v4",
        is_relevant=analysis.get("is_relevant", True),
        message_type=analysis.get("message_type", "evaluation"),
        contains_opinion=bool(analysis.get("contains_opinion", True)),
        sentiment=analysis.get("sentiment", "neutral"),
        sentiment_score=analysis.get("sentiment_score", 0),
        aspects_json=orjson.dumps(analysis.get("aspects", [])).decode(),
        topics_json=orjson.dumps(analysis.get("topics", [])).decode(),
        key_point=analysis.get("key_point", ""),
        severity=analysis.get("severity", "none"),
        confidence=analysis.get("confidence", 0.5),
        raw_json=orjson.dumps(analysis).decode()
    )
    db.add(annotation)
    db.commit()


def get_parent_text(db: Session, message: Message) -> str:
    if message.reply_to_message_id is None:
        return ""
    parent = db.query(Message).filter(Message.telegram_id == message.reply_to_message_id).first()
    if parent:
        return parent.text_clean
    return ""


def _check_mention_without_opinion(db: Session, message: Message, professor_id: int) -> bool:
    other_links = (
        db.query(MessageProfessor, Professor.name)
        .join(Professor, MessageProfessor.professor_id == Professor.id)
        .filter(MessageProfessor.message_id == message.id)
        .filter(MessageProfessor.professor_id != professor_id)
        .all()
    )
    if not other_links:
        return True
    text_lower = message.text_clean.lower()
    prof = db.get(Professor, professor_id)
    if not prof:
        return True
    prof_name_lower = prof.name.lower()
    patterns_no_opinion = [
        f"با {prof_name_lower} نداشتم",
        f"با {prof_name_lower} نگرفتم",
        f"با {prof_name_lower} برنداشتم",
        f"{prof_name_lower} رو نداشتم",
        f"از {prof_name_lower} خبر ندارم",
        f"از {prof_name_lower} نمی‌دونم",
        f"{prof_name_lower} رو نمی‌شناسم",
    ]
    for pattern in patterns_no_opinion:
        if pattern in text_lower:
            return False
    return True


def auto_review_single(db: Session, message_id: int, professor_id: int, professor_name: str, provider: str = "arvan", model: str = None) -> dict:
    message = db.get(Message, message_id)
    if not message:
        return {"error": "not_found"}
    parent_text = get_parent_text(db, message)
    prompt = REVIEW_USER_PROMPT_TEMPLATE.format(
        professor_name=professor_name,
        parent_text=parent_text[:300] if parent_text else "(بدون والد)",
        message_text=message.text_clean[:300]
    )
    if provider == "arvan":
        result = arvan_client.chat(prompt, SYSTEM_PROMPT, model=model, temperature=0.1)
    else:
        result = ollama_client.chat(prompt, SYSTEM_PROMPT, temperature=0.1)
    if "error" in result:
        return result
    relevant = bool(result.get("is_relevant", False))
    confidence = float(result.get("confidence", 0.5))
    return {
        "relevant": relevant,
        "confidence": confidence,
        "raw": result
    }


def run_auto_review(db: Session, job_id: int = None, provider: str = "arvan", model: str = None, threshold: float = 0.85):
    pending = (
        db.query(MessageProfessor, Message, Professor)
        .join(Message, Message.id == MessageProfessor.message_id)
        .join(Professor, Professor.id == MessageProfessor.professor_id)
        .filter(MessageProfessor.needs_review == True)
        .all()
    )
    total = len(pending)
    auto_approved = 0
    auto_rejected = 0
    manual_remaining = 0
    for mp, msg, prof in pending:
        result = auto_review_single(db, msg.id, prof.id, prof.name, provider, model)
        if "error" in result:
            manual_remaining += 1
            continue
        confidence = result.get("confidence", 0)
        relevant = result.get("relevant", False)
        if confidence >= threshold:
            mp.approved = relevant
            mp.needs_review = False
            if relevant:
                auto_approved += 1
            else:
                auto_rejected += 1
                db.query(Annotation).filter(
                    Annotation.message_id == msg.id,
                    Annotation.professor_id == prof.id
                ).delete()
        else:
            manual_remaining += 1
        if job_id and (auto_approved + auto_rejected + manual_remaining) % 10 == 0:
            db.commit()
            job = db.get(Job, job_id)
            if job:
                job.done = auto_approved + auto_rejected + manual_remaining
                job.error = f"auto_ok:{auto_approved} auto_no:{auto_rejected} manual:{manual_remaining}"
                db.commit()
    db.commit()
    if job_id:
        job = db.get(Job, job_id)
        if job:
            job.status = "done"
            job.done = total
            job.error = f"auto_ok:{auto_approved} auto_no:{auto_rejected} manual:{manual_remaining}"
            db.commit()
    return {
        "total": total,
        "auto_approved": auto_approved,
        "auto_rejected": auto_rejected,
        "manual_remaining": manual_remaining
    }


def _analyze_single(db_session_factory, message_id: int, professor_id: int, professor_name: str, provider: str = "ollama", model: str = None):
    db = db_session_factory()
    try:
        message = db.get(Message, message_id)
        if not message:
            log("warn", f"پیام {message_id} یافت نشد", "worker")
            return message_id, professor_id, "not_found"
        text_norm = _match_norm(message.text_clean)
        if _misattributed_to_other(db, professor_id, text_norm):
            message.processed_llm = True
            message.llm_error_count = 0
            db.commit()
            log("info", f"پیام {message_id} نظر دربارهٔ استاد دیگری است، رد شد", "worker")
            return message_id, professor_id, "skipped"
        parent_text = get_parent_text(db, message)
        reactions = None
        raw = getattr(message, "reactions_json", None)
        if raw:
            try:
                reactions = orjson.loads(raw)
            except Exception:
                reactions = None
        analysis = analyze_message(message, professor_name, parent_text, provider=provider, model=model, reactions=reactions)
        if "error" in analysis:
            message.llm_error_count = (message.llm_error_count or 0) + 1
            if message.llm_error_count >= 3:
                message.processed_llm = True
                log("error", f"پیام {message_id} بعد از {message.llm_error_count} تلاش رها شد: {analysis.get('error')}", "worker")
            else:
                log("warn", f"پیام {message_id} خطا (تلاش {message.llm_error_count}): {analysis.get('error')} | بعداً دوباره", "worker")
            db.commit()
            return message_id, professor_id, "error"
        if not analysis.get("is_relevant", True):
            message.processed_llm = True
            message.llm_error_count = 0
            db.commit()
            log("info", f"پیام {message_id} غیرمرتبط بود، رد شد", "worker")
            return message_id, professor_id, "skipped"
        if not _check_mention_without_opinion(db, message, professor_id):
            message.processed_llm = True
            message.llm_error_count = 0
            db.commit()
            log("info", f"پیام {message_id} فقط ذکر نام استاد بود، رد شد", "worker")
            return message_id, professor_id, "skipped"
        save_annotation(db, message, professor_id, analysis, provider=provider, model=model)
        message.processed_llm = True
        message.llm_error_count = 0
        db.commit()
        tag = "نظر" if analysis.get("contains_opinion") else "سوال"
        log("success", f"پیام {message_id} [{tag}] → {analysis.get('sentiment')}", "worker")
        return message_id, professor_id, "success"
    except Exception as e:
        log("error", f"پیام {message_id} exception: {e}", "worker")
        try:
            message = db.get(Message, message_id)
            if message:
                message.llm_error_count = (message.llm_error_count or 0) + 1
                if message.llm_error_count >= 3:
                    message.processed_llm = True
                db.commit()
        except Exception:
            pass
        return message_id, professor_id, "exception"
    finally:
        db.close()


def run_llm_analysis(
    db: Session,
    batch_size: int = 50,
    continuous: bool = False,
    job_id: int = None,
    workers: int = 3,
    provider: str = "ollama",
    model: str = None
):
    from app.db import SessionLocal
    log("info", f"شروع تحلیل | سرویس: {provider} | مدل: {model or 'پیش‌فرض'} | batch={batch_size} | workers={workers}", "runner")
    if provider == "ollama":
        ollama_client.ensure_model_loaded(ollama_client.OLLAMA_CHAT_MODEL)
    total_analyzed = 0
    total_errors = 0
    total_skipped = 0
    summaries_generated = 0
    current_batch = 0
    touched_professors = set()
    workers = max(1, min(workers, 8))
    while True:
        current_batch += 1
        messages_to_analyze = (
            db.query(Message, MessageProfessor.professor_id, Professor.name)
            .join(MessageProfessor, Message.id == MessageProfessor.message_id)
            .join(Professor, MessageProfessor.professor_id == Professor.id)
            .filter(Message.processed_llm == False)
            .filter(Message.char_len > 5)
            .order_by(Message.id)
            .limit(batch_size)
            .all()
        )
        if not messages_to_analyze:
            log("info", "هیچ پیامی برای تحلیل باقی نمانده", "runner")
            break
        seen_messages = {}
        for msg, prof_id, prof_name in messages_to_analyze:
            if msg.id not in seen_messages:
                seen_messages[msg.id] = (msg.id, prof_id, prof_name)
        tasks = list(seen_messages.values())
        batch_analyzed = 0
        batch_errors = 0
        batch_skipped = 0
        batch_processed = 0
        log("info", f"Batch {current_batch}: پردازش {len(tasks)} پیام با {workers} کارگر", "runner")
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_task = {
                executor.submit(_analyze_single, SessionLocal, msg_id, prof_id, prof_name, provider, model): (msg_id, prof_id)
                for msg_id, prof_id, prof_name in tasks
            }
            for future in as_completed(future_to_task):
                if job_id:
                    job = db.get(Job, job_id)
                    if job and job.status == "cancelled":
                        log("warn", "تحلیل توسط کاربر متوقف شد", "runner")
                        executor.shutdown(wait=False, cancel_futures=True)
                        return {
                            "analyzed": total_analyzed,
                            "errors": total_errors,
                            "skipped": total_skipped,
                            "provider": provider,
                            "summaries_generated": summaries_generated,
                            "message": "متوقف شد"
                        }
                try:
                    msg_id, prof_id, status = future.result(timeout=300)
                    batch_processed += 1
                    if status == "success":
                        batch_analyzed += 1
                        touched_professors.add(prof_id)
                    elif status == "skipped":
                        batch_skipped += 1
                    else:
                        batch_errors += 1
                    if job_id and batch_processed % 5 == 0:
                        job = db.get(Job, job_id)
                        if job:
                            job.done = total_analyzed + batch_analyzed + total_skipped + batch_skipped
                            job.error = f"ok:{total_analyzed + batch_analyzed} err:{total_errors + batch_errors} skip:{total_skipped + batch_skipped}"
                            db.commit()
                except Exception as e:
                    batch_errors += 1
                    log("error", f"خطا در پردازش: {e}", "runner")
        total_analyzed += batch_analyzed
        total_errors += batch_errors
        total_skipped += batch_skipped
        log("info", f"Batch {current_batch} تمام شد: ok={batch_analyzed} err={batch_errors} skip={batch_skipped}", "runner")
        if job_id:
            job = db.get(Job, job_id)
            if job:
                job.done = total_analyzed + total_skipped
                job.error = f"ok:{total_analyzed} err:{total_errors} skip:{total_skipped}"
                db.commit()
        if not continuous:
            break
    if touched_professors:
        touched_list = sorted(touched_professors)
        log("info", f"تولید خلاصه هوشمند برای {len(touched_list)} استاد لمس‌شده", "summary")
        for idx, pid in enumerate(touched_list):
            if job_id:
                job = db.get(Job, job_id)
                if job and job.status == "cancelled":
                    log("warn", "تحلیل قبل از پایان خلاصه‌ها متوقف شد", "summary")
                    break
            try:
                result = generate_professor_summary(db, pid, provider=provider, model=model)
                if result:
                    summaries_generated += 1
            except Exception as e:
                log("error", f"خلاصه استاد {pid} ناموفق: {e}", "summary")
            if job_id:
                job = db.get(Job, job_id)
                if job:
                    job.done = (job.done or 0) + 1
                    job.error = f"تولید خلاصه هوشمند: {idx + 1} از {len(touched_list)}"
                    db.commit()
    log("info", f"پایان تحلیل | ok={total_analyzed} err={total_errors} skip={total_skipped} summaries={summaries_generated} در {current_batch} batch", "runner")
    return {
        "analyzed": total_analyzed,
        "errors": total_errors,
        "skipped": total_skipped,
        "batches": current_batch,
        "workers": workers,
        "provider": provider,
        "model": model,
        "summaries_generated": summaries_generated
    }
