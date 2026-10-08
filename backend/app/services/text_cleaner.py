import re


def flatten_text(text):
    if text is None:
        return ""

    if isinstance(text, str):
        return text

    if isinstance(text, list):
        parts = []
        for item in text:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(item.get("text", ""))
        return "".join(parts)

    return str(text)


def normalize_fa(text: str) -> str:
    text = text.replace("ي", "ی")
    text = text.replace("ك", "ک")
    text = text.replace("ة", "ه")
    text = text.replace("‌", " ")  # نیم‌فاصله
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def redact_sensitive(text: str) -> str:
    text = re.sub(r"09\d{9}", "[PHONE]", text)
    text = re.sub(r"\d{8,10}", "[NUMBER]", text)
    text = re.sub(r"[\w\.-]+@[\w\.-]+", "[EMAIL]", text)
    text = re.sub(r"https?://\S+", "[LINK]", text)
    return text


def prepare_text(raw_text) -> str:
    text = flatten_text(raw_text)
    text = normalize_fa(text)
    text = redact_sensitive(text)
    return text.strip()