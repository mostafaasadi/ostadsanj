import re
import time
import requests
import orjson

from app.config import ARVAN_API_BASE, ARVAN_API_KEY, ARVAN_MODEL
from app.services.analysis_logger import log


def get_session():
    session = requests.Session()
    session.trust_env = False
    return session


def check_health() -> bool:
    if not ARVAN_API_KEY or not ARVAN_API_BASE:
        return False
    try:
        session = get_session()
        response = session.get(
            f"{ARVAN_API_BASE}/models",
            headers={"Authorization": f"apikey {ARVAN_API_KEY}"},
            timeout=10
        )
        return response.status_code in (200, 401, 404)
    except Exception as e:
        log("error", f"Arvan health check failed: {e}", "arvan")
        return False


def _extract_json(content: str) -> dict:
    if not content or not content.strip():
        return {"error": "empty_response"}
    try:
        return orjson.loads(content)
    except Exception:
        pass
    json_match = re.search(r'{[\s\S]*}', content)
    if json_match:
        try:
            return orjson.loads(json_match.group())
        except Exception:
            pass
    return {"error": "invalid_json", "raw": content[:300]}


def chat(prompt: str, system_prompt: str = "", model: str = None, temperature: float = 0.3) -> dict:
    if not ARVAN_API_KEY or not ARVAN_API_BASE:
        return {"error": "no_api_key", "message": "ArvanCloud not configured"}
    target_model = model or ARVAN_MODEL
    url = f"{ARVAN_API_BASE}/chat/completions"
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    payload = {
        "model": target_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 8192,
        "response_format": {"type": "json_object"}
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"apikey {ARVAN_API_KEY}"
    }
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            session = get_session()
            response = session.post(url, json=payload, headers=headers, timeout=120)
            if response.status_code == 429:
                wait_time = 30 * (attempt + 1)
                log("warn", f"Arvan 429, waiting {wait_time}s (attempt {attempt+1})", "arvan")
                time.sleep(wait_time)
                continue
            if response.status_code in (500, 502, 503) and attempt < max_attempts - 1:
                wait_time = 5 * (attempt + 1)
                log("warn", f"Arvan {response.status_code}, retrying in {wait_time}s", "arvan")
                time.sleep(wait_time)
                continue
            if response.status_code != 200:
                try:
                    error_data = response.json()
                    error_msg = error_data.get("error", {}).get("message", str(response.status_code))
                except Exception:
                    error_msg = response.text[:300]
                log("error", f"Arvan error {response.status_code}: {error_msg[:200]}", "arvan")
                return {"error": f"http_{response.status_code}", "message": error_msg}
            result = response.json()
            content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
            return _extract_json(content)
        except requests.exceptions.Timeout:
            if attempt < max_attempts - 1:
                time.sleep(5)
                continue
            return {"error": "timeout"}
        except requests.exceptions.ConnectionError:
            if attempt < max_attempts - 1:
                time.sleep(5)
                continue
            return {"error": "connection_error"}
        except Exception as e:
            log("error", f"Arvan exception: {e}", "arvan")
            return {"error": str(e)}
    return {"error": "max_retries_exceeded"}
