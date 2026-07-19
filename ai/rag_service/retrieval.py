from __future__ import annotations

from collections.abc import Sequence
from threading import Lock
from uuid import UUID

import numpy as np
import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row

from rag_service.config import Settings
from rag_service.schemas import ChatRequest, ChatSource, InternalChatRequest


class RetrievalError(RuntimeError):
    """Raised when the model or vector database cannot produce grounded results."""


def normalize_text(value: str) -> str:
    return "\n".join(line.strip() for line in value.strip().splitlines() if line.strip())


def psycopg_database_url(value: str) -> str:
    if value.startswith("postgresql+psycopg://"):
        return "postgresql://" + value.removeprefix("postgresql+psycopg://")
    return value


def normalize_source_types(
    source_types: Sequence[str] | None,
) -> tuple[str, ...] | None:
    if source_types is None:
        return None
    normalized: list[str] = []
    for source_type in source_types:
        value = source_type.strip()
        if not value:
            raise ValueError("source_types must not contain blank values")
        if value not in normalized:
            normalized.append(value)
    if not normalized:
        raise ValueError("source_types must not be empty; use None for all sources")
    return tuple(normalized)


def normalize_document_ids(
    document_ids: Sequence[UUID] | None,
) -> tuple[UUID, ...] | None:
    if document_ids is None:
        return None
    normalized = tuple(dict.fromkeys(document_ids))
    if not normalized:
        raise ValueError("document_ids must not be empty; use None for all documents")
    return normalized


def scope_sql(
    source_types: tuple[str, ...] | None,
    document_ids: tuple[UUID, ...] | None,
) -> tuple[str, list[object]]:
    clauses: list[str] = []
    parameters: list[object] = []
    if source_types is not None:
        clauses.append("AND d.source_type = ANY(%s)")
        parameters.append(list(source_types))
    if document_ids is not None:
        clauses.append("AND d.id = ANY(%s)")
        parameters.append(list(document_ids))
    return "\n".join(clauses), parameters


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

    def encode_many(self, texts: Sequence[str], batch_size: int = 16) -> np.ndarray:
        normalized = [normalize_text(text) for text in texts]
        if not normalized or any(not text for text in normalized):
            raise RetrievalError("Embedding input must contain non-empty text.")
        with self._encode_lock:
            vectors = self._get_model().encode(
                normalized,
                batch_size=batch_size,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        return np.asarray(vectors, dtype=np.float32)


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
            request.context.energy_source,
            request.context.task_description,
            request.question,
        )
        return " ".join(value.strip() for value in values if value and value.strip())

    def ready_chunk_count(
        self,
        source_types: Sequence[str] | None = None,
        document_ids: Sequence[UUID] | None = None,
    ) -> int:
        resolved_source_types = normalize_source_types(
            self.settings.source_types if source_types is None else source_types
        )
        resolved_document_ids = normalize_document_ids(document_ids)
        scope_clause, scope_parameters = scope_sql(
            resolved_source_types,
            resolved_document_ids,
        )
        query = f"""
            SELECT COUNT(*)
            FROM document_chunks dc
            JOIN documents d ON d.id = dc.document_id
            WHERE d.lifecycle_status = 'active'
              AND (dc.document_version_id IS NULL OR dc.document_version_id = d.current_version_id)
              AND d.access_level <> 'private'
              {scope_clause}
              AND dc.embedding_status = 'ready'
              AND dc.embedding IS NOT NULL
              AND dc.embedding_model = %s
        """
        with psycopg.connect(
            psycopg_database_url(self.settings.database_url),
            connect_timeout=5,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    query,
                    (*scope_parameters, self.settings.model_name),
                )
                return int(cursor.fetchone()[0])

    def search(
        self,
        request: InternalChatRequest,
        source_types: Sequence[str] | None = None,
        document_ids: Sequence[UUID] | None = None,
    ) -> list[ChatSource]:
        resolved_source_types = normalize_source_types(
            self.settings.source_types if source_types is None else source_types
        )
        resolved_document_ids = normalize_document_ids(document_ids)
        scope_clause, scope_parameters = scope_sql(
            resolved_source_types,
            resolved_document_ids,
        )
        vector = self.embedder.encode(self.build_search_query(request))
        query = f"""
            WITH best_per_document AS (
                SELECT DISTINCT ON (d.id)
                    d.id::text AS document_id,
                    dc.id::text AS chunk_id,
                    d.title,
                    d.document_type_code AS source_type,
                    dt.scope AS document_scope,
                    dv.original_filename,
                    dv.version_number AS document_version,
                    COALESCE(dc.metadata->>'section', dc.section_path->>0) AS section,
                    LEFT(dc.content, 420) AS excerpt,
                    COALESCE(dc.page_number, dc.page_start) AS page,
                    d.source_url AS url,
                    dc.embedding <=> %s AS cosine_distance
                FROM document_chunks dc
                JOIN documents d ON d.id = dc.document_id
                JOIN document_types dt ON dt.code = d.document_type_code
                LEFT JOIN document_versions dv ON dv.id = dc.document_version_id
                WHERE d.lifecycle_status = 'active'
                  AND (dc.document_version_id IS NULL OR dc.document_version_id = d.current_version_id)
                  AND (%s OR d.access_level <> 'private')
                  AND (
                      dt.scope = 'public'
                      OR (
                          dt.scope = 'company'
                          AND (%s OR d.site_id IS NULL OR d.site_id::text = ANY(%s))
                      )
                  )
                  {scope_clause}
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
                document_scope,
                original_filename,
                document_version,
                section,
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
            request.access_scope.allow_private,
            request.access_scope.all_sites,
            request.access_scope.site_ids,
            *scope_parameters,
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
                document_scope=row["document_scope"],
                original_filename=row["original_filename"],
                document_version=row["document_version"],
                section=row["section"],
                excerpt=row["excerpt"],
                page=row["page"],
                url=row["url"],
                similarity=float(row["similarity"]),
            )
            for row in rows
        ]
