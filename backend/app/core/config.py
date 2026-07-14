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
    database_url: str = getenv(
        "DATABASE_URL",
        "postgresql+psycopg://safemaint:change-me@localhost:5432/safemaint",
    )
    db_pool_size: int = int(getenv("DB_POOL_SIZE", "5"))
    db_max_overflow: int = int(getenv("DB_MAX_OVERFLOW", "10"))


settings = Settings()
