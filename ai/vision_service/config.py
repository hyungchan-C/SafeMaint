from dataclasses import dataclass
from os import getenv


def _as_bool(name: str, default: bool) -> bool:
    return getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    qwen_model: str = getenv("VISION_QWEN_MODEL", "Qwen/Qwen3-VL-4B-Instruct")
    paddle_model: str = getenv("VISION_PADDLE_MODEL", "PaddlePaddle/PaddleOCR-VL")
    model_cache_dir: str = getenv("VISION_MODEL_CACHE_DIR", "/models")
    device: str = getenv("VISION_DEVICE", "cuda")
    paddle_device: str = getenv("VISION_PADDLE_DEVICE", "cpu")
    load_in_4bit: bool = _as_bool("VISION_LOAD_IN_4BIT", True)
    enable_paddle: bool = _as_bool("VISION_ENABLE_PADDLE", True)
    enable_qwen: bool = _as_bool("VISION_ENABLE_QWEN", True)
    max_new_tokens: int = int(getenv("VISION_MAX_NEW_TOKENS", "192"))
    catalog_index_dir: str = getenv("VISION_CATALOG_INDEX_DIR", "/tmp/safemaint-catalogs")
    catalog_match_threshold: float = float(getenv("VISION_CATALOG_MATCH_THRESHOLD", "0.55"))


settings = Settings()
