from dataclasses import dataclass
from os import getenv


def _csv_env(name: str, default: str) -> tuple[str, ...]:
    raw_value = getenv(name, default)
    return tuple(item.strip() for item in raw_value.split(",") if item.strip())


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
    session_expire_minutes: int = int(getenv("SESSION_EXPIRE_MINUTES", "60"))
    rag_service_url: str | None = getenv("RAG_SERVICE_URL") or None
    rag_request_timeout_seconds: float = float(
        getenv("RAG_REQUEST_TIMEOUT_SECONDS", "180")
    )
    openai_api_key: str = getenv("OPENAI_API_KEY", "")
    openai_model: str = getenv("OPENAI_MODEL", "gpt-4o-mini")
    llm_base_url: str | None = getenv("LLM_BASE_URL") or None
    llm_analyzer_model: str = getenv(
        "LLM_ANALYZER_MODEL", getenv("OPENAI_MODEL", "gpt-4o-mini")
    )
    llm_answer_model: str = getenv(
        "LLM_ANSWER_MODEL", getenv("OPENAI_MODEL", "gpt-4o-mini")
    )
    openai_timeout_seconds: float = float(getenv("OPENAI_TIMEOUT_SECONDS", "30"))
    openai_max_output_tokens: int = int(
        getenv("OPENAI_MAX_OUTPUT_TOKENS", "1200")
    )
    llm_analyzer_max_output_tokens: int = int(
        getenv("LLM_ANALYZER_MAX_OUTPUT_TOKENS", "500")
    )
    allow_external_llm: bool = _bool_env("ALLOW_EXTERNAL_LLM", False)
    document_storage_dir: str = getenv("DOCUMENT_STORAGE_DIR", "/data/documents")
    document_max_upload_bytes: int = int(
        getenv("DOCUMENT_MAX_UPLOAD_BYTES", str(25 * 1024 * 1024))
    )
    package_storage_dir: str = getenv("PACKAGE_STORAGE_DIR", "/data/packages")
    public_package_max_bytes: int = int(
        getenv("PUBLIC_PACKAGE_MAX_BYTES", str(1024 * 1024 * 1024))
    )
    public_package_public_key: str = getenv("PUBLIC_PACKAGE_PUBLIC_KEY", "")
    public_package_private_key: str = getenv("PUBLIC_PACKAGE_PRIVATE_KEY", "")
    rag_model: str = getenv("RAG_MODEL", "BAAI/bge-m3")
    rag_embedding_dimension: int = int(getenv("RAG_EMBEDDING_DIMENSION", "1024"))
    tts_voice: str = getenv("TTS_VOICE", "F1")
    tts_language: str = getenv("TTS_LANGUAGE", "ko")
    tts_steps: int = int(getenv("TTS_STEPS", "8"))


settings = Settings()
