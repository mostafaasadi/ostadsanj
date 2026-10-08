import re
import requests
import orjson

from app.config import (
    OLLAMA_BASE_URL,
    OLLAMA_CHAT_MODEL,
    OLLAMA_CONTEXT,
    MAX_OUTPUT_TOKENS,
    OLLAMA_KEEP_ALIVE,
    OLLAMA_UNLOAD_TIMEOUT
)
from app.services.analysis_logger import log


def get_session():
    session = requests.Session()
    session.trust_env = False
    return session


def check_health() -> bool:
    try:
        session = get_session()
        response = session.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=10)
        return response.status_code == 200
    except Exception as e:
        log("error", f"ollama health check failed: {e}", "ollama")
        return False


def get_available_models() -> list[str]:
    try:
        session = get_session()
        response = session.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=10)
        response.raise_for_status()
        data = response.json()
        return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []


def load_model(model_name: str, keep_alive: str = None) -> bool:
    try:
        session = get_session()
        session.post(f"{OLLAMA_BASE_URL}/api/generate", json={
            "model": model_name,
            "keep_alive": keep_alive or OLLAMA_KEEP_ALIVE,
            "prompt": ""
        }, timeout=60)
        return True
    except Exception:
        return False


def ensure_model_loaded(target_model: str):
    load_model(target_model)


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
    target_model = model or OLLAMA_CHAT_MODEL
    ensure_model_loaded(target_model)
    url = f"{OLLAMA_BASE_URL}/api/chat"
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    payload = {
        "model": target_model,
        "messages": messages,
        "stream": False,
        "format": "json",
        "keep_alive": OLLAMA_KEEP_ALIVE,
        "options": {
            "temperature": temperature,
            "num_ctx": OLLAMA_CONTEXT,
            "num_predict": MAX_OUTPUT_TOKENS
        }
    }
    try:
        session = get_session()
        response = session.post(url, json=payload, timeout=180)
        if response.status_code != 200:
            log("error", f"ollama http {response.status_code}: {response.text[:200]}", "ollama")
            return {"error": f"http_{response.status_code}", "message": response.text[:300]}
        result = response.json()
        content = result.get("message", {}).get("content", "")
        return _extract_json(content)
    except requests.exceptions.Timeout:
        log("error", "ollama timeout", "ollama")
        return {"error": "timeout"}
    except requests.exceptions.ConnectionError:
        log("error", "ollama connection error", "ollama")
        return {"error": "connection_error"}
    except Exception as e:
        log("error", f"ollama exception: {e}", "ollama")
        return {"error": str(e)}
