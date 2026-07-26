from dataclasses import dataclass
from os import getenv


def _optional_csv_env(name: str) -> tuple[str, ...] | None:
    values = tuple(
        dict.fromkeys(
            item.strip()
            for item in getenv(name, "").split(",")
            if item.strip()
        )
    )
    return values or None


def _bool_env(name: str, default: bool = False) -> bool:
    raw_value = getenv(name)
    if raw_value is None:
        return default
    normalized = raw_value.strip().lower()
    if not normalized:
        return default
    return normalized in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str = getenv(
        "DATABASE_URL",
        "postgresql://safemaint:change-me@localhost:5432/safemaint_rag_test",
    )
    model_name: str = getenv("RAG_MODEL", "BAAI/bge-m3")
    model_cache_dir: str = getenv("RAG_MODEL_CACHE_DIR", "/models")
    device: str = getenv("RAG_DEVICE", "cpu")
    top_k: int = int(getenv("RAG_TOP_K", "5"))
    document_top_k: int = int(getenv("RAG_DOCUMENT_TOP_K", "6"))
    document_neighbor_window: int = int(
        getenv("RAG_DOCUMENT_NEIGHBOR_WINDOW", "1")
    )
    component_top_k: int = int(getenv("RAG_COMPONENT_TOP_K", "5"))
    maintenance_top_k: int = int(getenv("RAG_MAINTENANCE_TOP_K", "16"))
    candidate_k: int = int(getenv("RAG_CANDIDATE_K", "30"))
    max_chunks_per_document: int = int(
        getenv("RAG_MAX_CHUNKS_PER_DOCUMENT", "2")
    )
    min_similarity: float = float(getenv("RAG_MIN_SIMILARITY", "0.25"))
    min_keyword_score: float = float(getenv("RAG_MIN_KEYWORD_SCORE", "0.08"))
    source_types: tuple[str, ...] | None = _optional_csv_env("RAG_SOURCE_TYPES")
    maintenance_manual_quota: int = int(
        getenv("RAG_MAINTENANCE_MANUAL_QUOTA", "4")
    )
    maintenance_company_policy_quota: int = int(
        getenv("RAG_MAINTENANCE_COMPANY_POLICY_QUOTA", "2")
    )
    maintenance_law_quota: int = int(
        getenv("RAG_MAINTENANCE_LAW_QUOTA", "2")
    )
    maintenance_guide_quota: int = int(
        getenv("RAG_MAINTENANCE_GUIDE_QUOTA", "3")
    )
    maintenance_incident_quota: int = int(
        getenv("RAG_MAINTENANCE_INCIDENT_QUOTA", "2")
    )
    worker_poll_seconds: float = float(getenv("DOCUMENT_WORKER_POLL_SECONDS", "2"))
    worker_chunk_characters: int = int(
        getenv("DOCUMENT_WORKER_CHUNK_CHARACTERS", "1200")
    )
    worker_chunk_overlap: int = int(
        getenv("DOCUMENT_WORKER_CHUNK_OVERLAP", "150")
    )
    worker_embedding_batch_size: int = int(
        getenv("DOCUMENT_WORKER_EMBEDDING_BATCH_SIZE", "16")
    )
    worker_max_attempts: int = int(
        getenv("DOCUMENT_WORKER_MAX_ATTEMPTS", "3")
    )
    worker_retry_delay_seconds: float = float(
        getenv("DOCUMENT_WORKER_RETRY_DELAY_SECONDS", "10")
    )
    worker_stale_after_seconds: float = float(
        getenv("DOCUMENT_WORKER_STALE_AFTER_SECONDS", "300")
    )
    worker_heartbeat_seconds: float = float(
        getenv("DOCUMENT_WORKER_HEARTBEAT_SECONDS", "30")
    )
    qwen_enabled: bool = _bool_env("QWEN_ENABLED", False)
    document_profile_extraction_enabled: bool = _bool_env(
        "DOCUMENT_PROFILE_EXTRACTION_ENABLED",
        qwen_enabled,
    )
    qwen_service_url: str = getenv("QWEN_SERVICE_URL", "").rstrip("/")
    qwen_api_key: str = getenv("QWEN_API_KEY", "")
    qwen_timeout_seconds: float = float(getenv("QWEN_TIMEOUT_SECONDS", "600"))


settings = Settings()
