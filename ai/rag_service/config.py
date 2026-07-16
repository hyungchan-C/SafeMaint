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
    min_similarity: float = float(getenv("RAG_MIN_SIMILARITY", "0.25"))
    source_types: tuple[str, ...] | None = _optional_csv_env("RAG_SOURCE_TYPES")


settings = Settings()
