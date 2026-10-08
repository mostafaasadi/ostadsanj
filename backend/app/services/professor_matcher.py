import orjson
from sqlalchemy.orm import Session

from app.models import Message, Professor, MessageProfessor


def normalize_text(text: str) -> str:
    text = text.replace("ي", "ی").replace("ك", "ک").replace("ة", "ه")
    text = text.replace("‌", " ").replace("\u200c", " ")
    text = text.lower().strip()
    return text


def get_professor_aliases(professor: Professor) -> list[str]:
    try:
        aliases = orjson.loads(professor.aliases_json)
    except Exception:
        aliases = []
    all_names = [professor.name] + aliases
    normalized = []
    for name in all_names:
        if name and name.strip():
            normalized.append(name.strip())
    return normalized


def build_alias_index(professors: list[Professor]) -> dict:
    index = {}
    for prof in professors:
        aliases = get_professor_aliases(prof)
        for alias in aliases:
            normalized_alias = normalize_text(alias)
            if normalized_alias not in index:
                index[normalized_alias] = {
                    "professor_id": prof.id,
                    "matched_alias": alias,
                    "match_method": "hashtag" if alias.startswith("#") else "exact"
                }
    return index


def match_message_fast(text: str, alias_index: dict) -> list:
    normalized_text = normalize_text(text)
    words = normalized_text.split()
    raw = []
    for i in range(len(words)):
        for length in range(1, 5):
            if i + length > len(words):
                break
            phrase = " ".join(words[i:i + length])
            if phrase in alias_index:
                prof_info = alias_index[phrase]
                raw.append({
                    "professor_id": prof_info["professor_id"],
                    "matched_alias": prof_info["matched_alias"],
                    "match_method": prof_info["match_method"],
                    "confidence": 0.95,
                    "span": (i, i + length),
                })
    matches = []
    found_professors = set()
    for idx, cand in enumerate(raw):
        cs, ce = cand["span"]
        contained = False
        for jdx, other in enumerate(raw):
            if jdx == idx or other["professor_id"] == cand["professor_id"]:
                continue
            os_, oe = other["span"]
            if os_ <= cs and ce <= oe and (os_ < cs or ce < oe):
                contained = True
                break
        if contained:
            continue
        prof_id = cand["professor_id"]
        if prof_id not in found_professors:
            found_professors.add(prof_id)
            matches.append({
                "professor_id": cand["professor_id"],
                "matched_alias": cand["matched_alias"],
                "match_method": cand["match_method"],
                "confidence": cand["confidence"],
            })
    return matches


def save_message_professor_matches(db: Session, message: Message, matches: list[dict]):
    for match in matches:
        existing = db.query(MessageProfessor).filter(
            MessageProfessor.message_id == message.id,
            MessageProfessor.professor_id == match["professor_id"],
            MessageProfessor.match_method == match["match_method"]
        ).first()
        if not existing:
            mp = MessageProfessor(
                message_id=message.id,
                professor_id=match["professor_id"],
                match_method=match["match_method"],
                matched_alias=match["matched_alias"],
                confidence=match["confidence"],
                needs_review=(match["match_method"] == "reply_context"),
                approved=True if match["match_method"] != "reply_context" else None
            )
            db.add(mp)
    message.processed_matching = True


def get_matched_professors_for_message(db: Session, message_id: int) -> list[int]:
    links = db.query(MessageProfessor).filter(
        MessageProfessor.message_id == message_id
    ).all()
    return [link.professor_id for link in links]


def match_replies_to_professors(db: Session, alias_index: dict, job_id: int = None, offset: int = 0) -> int:
    from app.models import Job

    matched_links = (
        db.query(MessageProfessor.message_id)
        .filter(MessageProfessor.match_method != "reply_context")
        .distinct()
        .all()
    )
    matched_message_ids = [m[0] for m in matched_links]
    if not matched_message_ids:
        return 0

    messages_with_matches = db.query(Message).filter(Message.id.in_(matched_message_ids)).all()
    telegram_id_to_professors = {}
    for msg in messages_with_matches:
        if msg.telegram_id:
            prof_ids = get_matched_professors_for_message(db, msg.id)
            if prof_ids:
                telegram_id_to_professors[msg.telegram_id] = prof_ids

    reply_messages = db.query(Message).filter(
        Message.reply_to_message_id.isnot(None),
        Message.processed_matching == False,
        Message.char_len > 0
    ).all()

    matched_count = 0
    junk_first_chars = {".", ":", "-", "!", "؟", "?", "🙏", "👍", "", "❤", "😂", "✨", "🤍", "🌷", "", "💚", "😅"}

    for i, reply_msg in enumerate(reply_messages):
        reply_text = (reply_msg.text_clean or "").strip()
        is_junk = (
            (reply_msg.char_len is not None and reply_msg.char_len < 25)
            or (reply_text[:1] in junk_first_chars)
        )
        parent_telegram_id = reply_msg.reply_to_message_id

        if is_junk:
            reply_msg.processed_matching = True
        elif parent_telegram_id in telegram_id_to_professors:
            prof_ids = telegram_id_to_professors[parent_telegram_id]
            direct_matches = match_message_fast(reply_msg.text_clean, alias_index)
            if direct_matches:
                save_message_professor_matches(db, reply_msg, direct_matches)
                matched_count += 1
            else:
                for prof_id in prof_ids:
                    existing = db.query(MessageProfessor).filter(
                        MessageProfessor.message_id == reply_msg.id,
                        MessageProfessor.professor_id == prof_id,
                        MessageProfessor.match_method == "reply_context"
                    ).first()
                    if not existing:
                        mp = MessageProfessor(
                            message_id=reply_msg.id,
                            professor_id=prof_id,
                            match_method="reply_context",
                            matched_alias=None,
                            confidence=0.6,
                            needs_review=True,
                            approved=None
                        )
                        db.add(mp)
                reply_msg.processed_matching = True
                matched_count += 1
        else:
            reply_msg.processed_matching = True

        if (i + 1) % 200 == 0:
            db.commit()
            if job_id:
                job = db.get(Job, job_id)
                if job:
                    job.done = max(job.done or 0, offset + i + 1)
                    db.commit()

    db.commit()
    return matched_count


def run_matching(db: Session, job_id: int = None):
    from app.models import Job

    professors = db.query(Professor).filter(Professor.is_active == True).all()
    if not professors:
        return {"matched": 0, "total": 0, "reply_matched": 0}

    alias_index = build_alias_index(professors)
    unprocessed = db.query(Message).filter(
        Message.processed_matching == False,
        Message.char_len > 0
    ).all()
    total = len(unprocessed)
    matched_count = 0
    processed_direct = 0
    pending_replies = 0

    if job_id:
        job = db.get(Job, job_id)
        if job:
            job.total = total
            db.commit()

    for i, message in enumerate(unprocessed):
        matches = match_message_fast(message.text_clean, alias_index)
        if matches:
            save_message_professor_matches(db, message, matches)
            matched_count += 1
            message.processed_matching = True
            processed_direct += 1
        else:
            if message.reply_to_message_id is not None:
                pending_replies += 1
                continue
            message.processed_matching = True
            processed_direct += 1
        if (i + 1) % 200 == 0:
            db.commit()
            if job_id:
                job = db.get(Job, job_id)
                if job:
                    job.done = processed_direct
                    db.commit()

    db.commit()
    reply_matched = match_replies_to_professors(db, alias_index, job_id, offset=processed_direct)

    if job_id:
        job = db.get(Job, job_id)
        if job:
            job.done = total
            db.commit()

    return {
        "matched": matched_count,
        "total": total,
        "reply_matched": reply_matched
    }
