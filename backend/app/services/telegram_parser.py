import ijson
import hashlib
import orjson
from datetime import datetime

from app.services.text_cleaner import prepare_text


def parse_date(date_str):
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str)
    except Exception:
        return None


def hash_user(user_id):
    if not user_id:
        return None
    return hashlib.sha256(str(user_id).encode()).hexdigest()


def extract_reactions(msg: dict) -> dict:
    reactions_raw = msg.get("reactions") or []
    emo = {}
    for r in reactions_raw:
        if not isinstance(r, dict):
            continue
        emoticon = r.get("emoji")
        count = r.get("count", 1)
        if emoticon:
            emo[emoticon] = emo.get(emoticon, 0) + int(count)
    return emo


def iter_telegram_messages(file_path: str):
    with open(file_path, "rb") as f:
        for msg in ijson.items(f, "messages.item"):
            raw_text = msg.get("text", "")
            text_clean = prepare_text(raw_text)
            reply_id = msg.get("reply_to_message_id")
            emo = extract_reactions(msg)
            yield {
                "telegram_id": msg.get("id"),
                "date": parse_date(msg.get("date")),
                "author_hash": hash_user(msg.get("from_id") or msg.get("from")),
                "text_clean": text_clean,
                "char_len": len(text_clean),
                "reply_to_message_id": reply_id,
                "thread_root_id": None,
                "reactions_json": orjson.dumps(emo).decode() if emo else None,
            }