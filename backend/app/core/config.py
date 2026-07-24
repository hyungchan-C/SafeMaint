from dataclasses import dataclass
from os import getenv
from urllib.parse import urlparse


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


def _choice_env(name: str, default: str, choices: set[str]) -> str:
    value = getenv(name, default).strip().lower()
    if value not in choices:
        expected = ", ".join(sorted(choices))
        raise ValueError(f"{name} must be one of: {expected}")
    return value


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
    llm_provider_scope: str = _choice_env(
        "LLM_PROVIDER_SCOPE", "external", {"external", "local"}
    )
    llm_local_hosts: tuple[str, ...] = _csv_env(
        "LLM_LOCAL_HOSTS",
        "localhost,127.0.0.1,::1,host.docker.internal,ollama",
    )
    llm_analyzer_model: str = getenv(
        "LLM_ANALYZER_MODEL", getenv("OPENAI_MODEL", "gpt-4o-mini")
    )
    llm_answer_model: str = getenv(
        "LLM_ANSWER_MODEL", getenv("OPENAI_MODEL", "gpt-4o-mini")
    )
    llm_reasoning_effort: str = _choice_env(
        "LLM_REASONING_EFFORT", "none", {"none", "low", "medium", "high"}
    )
    vision_service_url: str = getenv("VISION_SERVICE_URL", "http://vision:8020")
    vision_image_max_upload_bytes: int = int(
        getenv("VISION_IMAGE_MAX_UPLOAD_BYTES", str(10 * 1024 * 1024))
    )
    openai_timeout_seconds: float = float(getenv("OPENAI_TIMEOUT_SECONDS", "30"))
    openai_max_output_tokens: int = int(
        getenv("OPENAI_MAX_OUTPUT_TOKENS", "1200")
    )
    llm_analyzer_max_output_tokens: int = int(
        getenv("LLM_ANALYZER_MAX_OUTPUT_TOKENS", "500")
    )
    allow_external_llm: bool = _bool_env("ALLOW_EXTERNAL_LLM", False)
    qwen_enabled: bool = _bool_env("QWEN_ENABLED", False)
    qwen_provider: str = getenv("QWEN_PROVIDER", "local")
    qwen_service_url: str | None = getenv("QWEN_SERVICE_URL", "http://qwen:8020") or None
    qwen_api_key: str = getenv("QWEN_API_KEY", "")
    qwen_timeout_seconds: float = float(getenv("QWEN_TIMEOUT_SECONDS", "600"))
    qwen_allow_company_context: bool = _bool_env("QWEN_ALLOW_COMPANY_CONTEXT", False)
    question_intent_confidence_threshold: float = float(
        getenv("QUESTION_INTENT_CONFIDENCE_THRESHOLD", "0.8")
    )
    qwen_source_excerpt_chars: int = int(
        getenv("QWEN_SOURCE_EXCERPT_CHARS", "900")
    )
    qwen_document_source_limit: int = int(
        getenv("QWEN_DOCUMENT_SOURCE_LIMIT", "6")
    )
    qwen_component_source_limit: int = int(
        getenv("QWEN_COMPONENT_SOURCE_LIMIT", "5")
    )
    qwen_maintenance_source_limit: int = int(
        getenv("QWEN_MAINTENANCE_SOURCE_LIMIT", "8")
    )
    qwen_classifier_enabled: bool = _bool_env(
        "QWEN_CLASSIFIER_ENABLED", False
    )
    qwen_classifier_url: str | None = getenv("QWEN_CLASSIFIER_URL") or None
    qwen_classifier_timeout_seconds: float = float(
        getenv("QWEN_CLASSIFIER_TIMEOUT_SECONDS", "300")
    )
    document_storage_dir: str = getenv("DOCUMENT_STORAGE_DIR", "/data/documents")
    document_max_upload_bytes: int = int(
        getenv("DOCUMENT_MAX_UPLOAD_BYTES", str(200 * 1024 * 1024))
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
    tts_num_threads: int = int(getenv("TTS_NUM_THREADS", "4"))
    stt_model: str = getenv("STT_MODEL", "small")
    stt_language: str = getenv("STT_LANGUAGE", "ko")
    stt_device: str = getenv("STT_DEVICE", "cpu")
    stt_compute_type: str = getenv("STT_COMPUTE_TYPE", "int8")
    gps_proximity_radius_m: float = float(getenv("GPS_PROXIMITY_RADIUS_M", "30"))

    @property
    def llm_is_local(self) -> bool:
        return self.llm_provider_scope == "local"

    def local_llm_url_is_trusted(self) -> bool:
        if not self.llm_base_url:
            return False
        hostname = (urlparse(self.llm_base_url).hostname or "").casefold()
        return hostname in {host.casefold() for host in self.llm_local_hosts}


settings = Settings()
