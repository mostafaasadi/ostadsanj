import json
import time
from pathlib import Path

from app.services.analysis_logger import log

BASE_DIR = Path(__file__).resolve().parent.parent.parent
MODELS_FILE = BASE_DIR / "models.json"
MEM_TTL = 60 * 60 * 12
RIAL_TO_TOMAN = 10


def _extract_prices(model: dict) -> dict:
    in_rial = out_rial = None
    for p in model.get("prices") or []:
        metric = p.get("metricKey") or ""
        ups = p.get("unitPrices") or []
        amount = ups[0].get("amount") if ups else None
        used = (ups[0].get("used") or 1_000_000) if ups else 1_000_000
        if amount is None:
            amount = p.get("totalPrice")
        if amount is None:
            continue
        try:
            rial = float(amount) * (1_000_000 / used)
        except (TypeError, ValueError):
            continue
        if metric.endswith("_in_token"):
            in_rial = rial
        elif metric.endswith("_out_token"):
            out_rial = rial
    return {
        "input_per_1m": (in_rial / RIAL_TO_TOMAN) if in_rial is not None else None,
        "output_per_1m": (out_rial / RIAL_TO_TOMAN) if out_rial is not None else None,
    }


def _normalize(model: dict) -> dict:
    extra = model.get("extraArgs") or {}
    return {
        "id": model.get("name"),
        "provider": model.get("provider"),
        "context": model.get("contextLength"),
        "tags": model.get("tags") or [],
        "weight": model.get("weight", 0),
        "disabled": bool(model.get("disabled")),
        "deprecated": bool(model.get("deprecated")),
        "down": extra.get("status") == "down",
        "has_chat": bool(extra.get("chatOptions")),
        "dimension": extra.get("dimension"),
        **_extract_prices(model),
    }


def _usable_chat(m: dict) -> bool:
    return (
        not m["disabled"] and not m["deprecated"] and not m["down"]
        and m["input_per_1m"] is not None and m["output_per_1m"] is not None
        and ("text" in m["tags"] or m["has_chat"])
    )


def _usable_embed(m: dict) -> bool:
    return (
        not m["disabled"] and not m["deprecated"] and not m["down"]
        and m["input_per_1m"] is not None and "embedding" in m["tags"]
    )


def _load_from_file():
    if not MODELS_FILE.exists():
        log("error", f"model_registry: models.json not found: {MODELS_FILE}", "registry")
        return None
    try:
        data = json.loads(MODELS_FILE.read_text(encoding="utf-8"))
        return [_normalize(m) for m in data.get("data", [])]
    except Exception as e:
        log("error", f"model_registry: parse failed: {e}", "registry")
        return None


_MEM = None
_MEM_AT = 0.0


def get_models(force: bool = False) -> list:
    global _MEM, _MEM_AT
    if _MEM is not None and not force and (time.time() - _MEM_AT) < MEM_TTL:
        return _MEM
    models = _load_from_file()
    if not models:
        return _MEM or []
    models.sort(key=lambda m: m["weight"], reverse=True)
    _MEM = models
    _MEM_AT = time.time()
    log("info", f"model_registry: loaded {len(models)} models from models.json", "registry")
    return models


def get_chat_models(force: bool = False) -> list:
    return [m for m in get_models(force) if _usable_chat(m)]


def get_embedding_models(force: bool = False) -> list:
    return [m for m in get_models(force) if _usable_embed(m)]