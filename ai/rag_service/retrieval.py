from __future__ import annotations

from collections.abc import Sequence
from threading import Lock

import numpy as np
import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row

from rag_service.config import Settings
from rag_service.schemas import ChatRequest, ChatSource


class RetrievalError(RuntimeError):
    """Raised when the model or vector database cannot produce grounded results."""


def normalize_text(value: str) -> str:
    return "\n".join(line.strip() for line in value.strip().splitlines() if line.strip())


def psycopg_database_url(value: str) -> str:
    if value.startswith("postgresql+psycopg://"):
        return "postgresql://" + value.removeprefix("postgresql+psycopg://")
    return value


class BgeM3Embedder:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._model = None
        self._load_lock = Lock()
        self._encode_lock = Lock()

    def _get_model(self):
        if self._model is None:
            with self._load_lock:
                if self._model is None:
                    from sentence_transformers import SentenceTransformer

                    self._model = SentenceTransformer(
                        self.settings.model_name,
                        device=self.settings.device,
                        cache_folder=self.settings.model_cache_dir,
                    )
        return self._model

    def encode(self, text: str) -> np.ndarray:
        normalized = normalize_text(text)
        if not normalized:
            raise RetrievalError("검색 질문이 비어 있습니다.")
        with self._encode_lock:
            vector = self._get_model().encode(
                [normalized],
                batch_size=1,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )[0]
        return np.asarray(vector, dtype=np.float32)


class PgvectorRetriever:
    def __init__(self, settings: Settings, embedder: BgeM3Embedder | None = None) -> None:
        self.settings = settings
        self.embedder = embedder or BgeM3Embedder(settings)

    def build_search_query(self, request: ChatRequest) -> str:
        values: Sequence[str | None] = (
            request.context.equipment_name,
            request.context.manufacturer,
            request.context.model_number,
            request.context.component_name,
            request.context.task_type,
            request.question,
        )
        return " ".join(value.strip() for value in values if value and value.strip())

    def ready_chunk_count(self) -> int:
        query = """
            SELECT COUNT(*)
            FROM document_chunks dc
            JOIN documents d ON d.id = dc.document_id
            WHERE d.source_type = 'incident'
              AND d.access_level <> 'private'
              AND dc.embedding_status = 'ready'
              AND dc.embedding IS NOT NULL
              AND dc.embedding_model = %s
        """
        with psycopg.connect(
            psycopg_database_url(self.settings.database_url),
            connect_timeout=5,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, (self.settings.model_name,))
                return int(cursor.fetchone()[0])

    def search(self, request: ChatRequest) -> list[ChatSource]:
        vector = self.embedder.encode(self.build_search_query(request))
        query = """
            WITH best_per_document AS (
                SELECT DISTINCT ON (d.id)
                    d.id::text AS document_id,
                    dc.id::text AS chunk_id,
                    d.title,
                    CONCAT(
                        'incident:',
                        COALESCE(d.metadata->>'dataset_type', 'unknown')
                    ) AS source_type,
                    LEFT(dc.content, 420) AS excerpt,
                    COALESCE(dc.page_number, dc.page_start) AS page,
                    d.source_url AS url,
                    dc.embedding <=> %s AS cosine_distance
                FROM document_chunks dc
                JOIN documents d ON d.id = dc.document_id
                WHERE d.source_type = 'incident'
                  AND d.access_level <> 'private'
                  AND dc.embedding_status = 'ready'
                  AND dc.embedding IS NOT NULL
                  AND dc.embedding_model = %s
                  AND dc.embedding_dimension = %s
                  AND (dc.embedding <=> %s) <= %s
                ORDER BY d.id, cosine_distance
            )
            SELECT
                document_id,
                chunk_id,
                title,
                source_type,
                excerpt,
                page,
                url,
                1 - cosine_distance AS similarity
            FROM best_per_document
            ORDER BY cosine_distance
            LIMIT %s
        """
        distance_limit = 1.0 - self.settings.min_similarity
        parameters = (
            vector,
            self.settings.model_name,
            int(vector.shape[0]),
            vector,
            distance_limit,
            self.settings.top_k,
        )
        with psycopg.connect(
            psycopg_database_url(self.settings.database_url),
            connect_timeout=5,
            row_factory=dict_row,
        ) as connection:
            register_vector(connection)
            with connection.cursor() as cursor:
                cursor.execute(query, parameters)
                rows = cursor.fetchall()

        return [
            ChatSource(
                document_id=row["document_id"],
                chunk_id=row["chunk_id"],
                title=row["title"],
                source_type=row["source_type"],
                excerpt=row["excerpt"],
                page=row["page"],
                url=row["url"],
                similarity=float(row["similarity"]),
            )
            for row in rows
        ]
