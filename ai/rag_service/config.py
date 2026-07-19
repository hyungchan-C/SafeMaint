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
    candidate_k: int = int(getenv("RAG_CANDIDATE_K", "30"))
    max_chunks_per_document: int = int(
        getenv("RAG_MAX_CHUNKS_PER_DOCUMENT", "2")
    )
    min_similarity: float = float(getenv("RAG_MIN_SIMILARITY", "0.25"))
    min_keyword_score: float = float(getenv("RAG_MIN_KEYWORD_SCORE", "0.08"))
    source_types: tuple[str, ...] | None = _optional_csv_env("RAG_SOURCE_TYPES")
    worker_poll_seconds: float = float(getenv("DOCUMENT_WORKER_POLL_SECONDS", "2"))
    worker_chunk_characters: int = int(
        getenv("DOCUMENT_WORKER_CHUNK_CHARACTERS", "1200")
    )
    worker_chunk_overlap: int = int(
        getenv("DOCUMENT_WORKER_CHUNK_OVERLAP", "150")
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


settings = Settings()
