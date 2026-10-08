from collections import deque
from datetime import datetime
import threading

_lock = threading.Lock()
_logs = deque(maxlen=500)


def log(level: str, message: str, source: str = "analysis"):
    with _lock:
        _logs.append({
            "time": datetime.now().strftime("%H:%M:%S"),
            "level": level,
            "source": source,
            "message": message
        })
    icon = {"info": "ℹ️", "success": "✅", "error": "❌", "warn": "⚠️", "debug": "🔍"}.get(level, "")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {icon} [{source}] {message}", flush=True)


def get_logs(limit: int = 100):
    with _lock:
        return list(_logs)[-limit:]


def clear():
    with _lock:
        _logs.clear()
