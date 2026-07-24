from __future__ import annotations

from dataclasses import dataclass
from os import getenv


def _bool_env(name: str, default: bool = False) -> bool:
    raw_value = getenv(name)
    if raw_value is None:
        return default
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")


def _csv_env(name: str, default: str) -> tuple[str, ...]:
    raw_value = getenv(name, default)
    return tuple(item.strip() for item in raw_value.split(",") if item.strip())


def _choice_env(name: str, default: str, choices: set[str]) -> str:
    value = getenv(name, default).strip().lower()
    if value not in choices:
        expected = ", ".join(sorted(choices))
        raise ValueError(f"{name} must be one of: {expected}")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    base_model: str = getenv("QWEN_BASE_MODEL", "Qwen/Qwen3.5-9B")
    lora_adapter: str = getenv("QWEN_LORA_ADAPTER", "")
    api_key: str = getenv("QWEN_API_KEY", "")
    device: str = getenv("QWEN_DEVICE", "cuda")
    load_in_4bit: bool = _bool_env("QWEN_LOAD_IN_4BIT", True)
    answer_mode: str = _choice_env(
        "QWEN_ANSWER_MODE", "structured", {"text", "structured"}
    )
    repair_enabled: bool = _bool_env("QWEN_REPAIR_ENABLED", True)
    max_new_tokens: int = int(getenv("QWEN_MAX_NEW_TOKENS", "768"))
    classify_max_new_tokens: int = int(
        getenv("QWEN_CLASSIFY_MAX_NEW_TOKENS", "64")
    )
    occurrence_labels: tuple[str, ...] = _csv_env(
        "QWEN_OCCURRENCE_LABELS",
        "끼임,떨어짐,넘어짐,맞음,부딪힘,깔림,감전,화재,폭발,질식,중독,베임,찔림,기타",
    )


settings = Settings()
