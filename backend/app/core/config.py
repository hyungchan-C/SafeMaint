from dataclasses import dataclass
from os import getenv


def _csv_env(name: str, default: str) -> tuple[str, ...]:
    raw_value = getenv(name, default)
    return tuple(item.strip() for item in raw_value.split(",") if item.strip())


@dataclass(frozen=True, slots=True)
class Settings:
    app_name: str = "SafeMaint AI"
    app_env: str = getenv("APP_ENV", "development")
    api_prefix: str = "/api/v1"
    cors_origins: tuple[str, ...] = _csv_env(
        "CORS_ORIGINS", "http://localhost:3000"
    )
    cors_origin_regex: str = getenv(
        "CORS_ORIGIN_REGEX",
        r"^https?://(localhost|127\.0\.0\.1|192\.168\.\d{1,3}\.\d{1,3}):3000$",
    )
    database_url: str = getenv(
        "DATABASE_URL",
        "postgresql+psycopg://safemaint:change-me@localhost:5432/safemaint",
    )
    db_pool_size: int = int(getenv("DB_POOL_SIZE", "5"))
    db_max_overflow: int = int(getenv("DB_MAX_OVERFLOW", "10"))
    tts_voice: str = getenv("TTS_VOICE", "F1")
    tts_language: str = getenv("TTS_LANGUAGE", "ko")
    tts_steps: int = int(getenv("TTS_STEPS", "8"))


settings = Settings()
