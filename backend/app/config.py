import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

DB_PATH = Path(__file__).parent.parent / "prof_dashboard.db"
DB_URL = f"sqlite:///{DB_PATH}"

MIN_MENTIONS_FOR_SCORE = 5
MIN_UNIQUE_USERS_FOR_SCORE = 3
IMPORT_BATCH_SIZE = 500

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_CHAT_MODEL = os.getenv("OLLAMA_CHAT_MODEL", "gemma4:e4b-it-qat")

OLLAMA_CONTEXT = 4096
MAX_OUTPUT_TOKENS = 2048
OLLAMA_KEEP_ALIVE = "5m"
OLLAMA_UNLOAD_TIMEOUT = 10

ARVAN_API_BASE = os.getenv("ARVAN_API_BASE", "https://api.arvancloudai.ir/v1")
ARVAN_API_KEY = os.getenv("ARVAN_API_KEY", "")
ARVAN_MODEL = os.getenv("ARVAN_MODEL", "DeepSeek-V4-Flash")

DEFAULT_PROVIDER = os.getenv("DEFAULT_PROVIDER", "arvan")

PROVIDER_LABELS = {
    "ollama": "🖥️ Ollama محلی",
    "arvan": "☁️ آروان‌کلاد"
}
