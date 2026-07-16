from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
import math
from pathlib import Path
from typing import Protocol
from uuid import UUID

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.db.models import Document, DocumentChunk
from app.services.document_ingestion import (
    normalize_chunk_content,
)


class EmbeddingError(RuntimeError):
    """Raised when vectors cannot be produced or safely stored."""


class TextEmbedder(Protocol):
    model_name: str
    device: str

    def encode(self, texts: Sequence[str], batch_size: int) -> list[list[float]]: ...


class SentenceTransformerEmbedder:
    def __init__(
        self,
        model_name: str,
        device: str = "auto",
        cache_dir: str | Path | None = None,
    ) -> None:
        import torch
        from sentence_transformers import SentenceTransformer

        if device == "auto":
            resolved_device = "cuda" if torch.cuda.is_available() else "cpu"
        elif device in {"cpu", "cuda"}:
            resolved_device = device
        else:
            raise ValueError("device must be one of: auto, cpu, cuda")
        if resolved_device == "cuda" and not torch.cuda.is_available():
            raise EmbeddingError("CUDA was requested but is not available")

        self.model_name = model_name
        self.device = resolved_device
        self.cache_dir = Path(cache_dir).resolve() if cache_dir else None
        if self.cache_dir is not None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._model = SentenceTransformer(
            model_name,
            device=resolved_device,
            cache_folder=str(self.cache_dir) if self.cache_dir else None,
        )

    def encode(self, texts: Sequence[str], batch_size: int) -> list[list[float]]:
        normalized_texts = [normalize_text_for_embedding(text) for text in texts]
        vectors = self._model.encode(
            normalized_texts,
            batch_size=batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return [[float(value) for value in vector] for vector in vectors]


@dataclass(slots=True)
class EmbeddingRunResult:
    model: str
    device: str
    requested_limit: int
    source_types: tuple[str, ...] | None = None
    document_ids: tuple[str, ...] | None = None
    processed: int = 0
    newly_ready: int = 0
    newly_failed: int = 0
    newly_skipped: int = 0
    vector_dimension: int | None = None
    status_counts: dict[str, int] = field(default_factory=dict)

    def to_report(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SearchHit:
    document_id: UUID
    chunk_id: UUID
    external_id: str
    title: str
    source_type: str
    publisher: str | None
    page_number: int | None
    dataset_type: str | None
    chunk_index: int
    cosine_distance: float
    similarity: float
    content_preview: str
    content_quality: str | None

    def to_report(self) -> dict[str, object]:
        report = asdict(self)
        report["document_id"] = str(self.document_id)
        report["chunk_id"] = str(self.chunk_id)
        return report


def normalize_text_for_embedding(value: str) -> str:
    return normalize_chunk_content(value)


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


def _document_scope_filters(
    source_types: tuple[str, ...] | None,
    document_ids: tuple[UUID, ...] | None,
) -> tuple[object, ...]:
    filters: list[object] = []
    if source_types is not None:
        filters.append(Document.source_type.in_(source_types))
    if document_ids is not None:
        filters.append(Document.id.in_(document_ids))
    return tuple(filters)


def _validate_vectors(
    vectors: Sequence[Sequence[float]],
    expected_count: int,
    expected_dimension: int | None,
) -> int:
    if len(vectors) != expected_count:
        raise EmbeddingError(
            f"Model returned {len(vectors)} vectors for {expected_count} texts"
        )
    dimensions = {len(vector) for vector in vectors}
    if not dimensions or 0 in dimensions or len(dimensions) != 1:
        raise EmbeddingError(f"Inconsistent vector dimensions: {sorted(dimensions)}")
    dimension = dimensions.pop()
    if expected_dimension is not None and dimension != expected_dimension:
        raise EmbeddingError(
            f"Vector dimension changed from {expected_dimension} to {dimension}"
        )
    for vector in vectors:
        if not all(math.isfinite(float(value)) for value in vector):
            raise EmbeddingError("Model returned a non-finite vector value")
        norm = math.sqrt(sum(float(value) ** 2 for value in vector))
        if not math.isclose(norm, 1.0, rel_tol=1e-3, abs_tol=1e-3):
            raise EmbeddingError(
                f"Expected normalized vectors, observed L2 norm {norm:.6f}"
            )
    return dimension


def _set_embedding_error(
    chunk: DocumentChunk,
    model_name: str,
    reason: str,
    status: str,
) -> None:
    metadata = dict(chunk.metadata_json or {})
    metadata["embedding_error"] = {
        "model": model_name,
        "reason": reason[:1000],
    }
    chunk.metadata_json = metadata
    chunk.embedding = None
    chunk.embedding_model = model_name
    chunk.embedding_dimension = None
    chunk.embedding_status = status


def _clear_embedding_error(chunk: DocumentChunk) -> None:
    metadata = dict(chunk.metadata_json or {})
    metadata.pop("embedding_error", None)
    chunk.metadata_json = metadata


def _ready_dimensions(
    session: Session,
    model_name: str,
    source_types: tuple[str, ...] | None = None,
    document_ids: tuple[UUID, ...] | None = None,
) -> set[int]:
    return {
        int(value)
        for value in session.scalars(
            select(distinct(DocumentChunk.embedding_dimension))
            .join(Document)
            .where(
                DocumentChunk.embedding_status == "ready",
                DocumentChunk.embedding_model == model_name,
                DocumentChunk.embedding_dimension.is_not(None),
                *_document_scope_filters(source_types, document_ids),
            )
        )
        if value is not None
    }


def _status_counts(
    session: Session,
    source_types: tuple[str, ...] | None = None,
    document_ids: tuple[UUID, ...] | None = None,
) -> dict[str, int]:
    return {
        status: int(count)
        for status, count in session.execute(
            select(DocumentChunk.embedding_status, func.count(DocumentChunk.id))
            .join(Document)
            .where(*_document_scope_filters(source_types, document_ids))
            .group_by(DocumentChunk.embedding_status)
        )
    }


def embed_pending_chunks(
    session_factory: Callable[[], Session],
    embedder: TextEmbedder,
    *,
    limit: int = 500,
    batch_size: int = 16,
    source_types: Sequence[str] | None = None,
    document_ids: Sequence[UUID] | None = None,
) -> EmbeddingRunResult:
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    if batch_size <= 0:
        raise ValueError("batch_size must be greater than zero")

    normalized_source_types = normalize_source_types(source_types)
    normalized_document_ids = normalize_document_ids(document_ids)

    result = EmbeddingRunResult(
        model=embedder.model_name,
        device=embedder.device,
        requested_limit=limit,
        source_types=normalized_source_types,
        document_ids=(
            tuple(str(document_id) for document_id in normalized_document_ids)
            if normalized_document_ids is not None
            else None
        ),
    )
    with session_factory() as session:
        bind = session.get_bind()
        if bind is None:
            raise EmbeddingError("Database session is not bound to an engine")
        existing_dimensions = _ready_dimensions(session, embedder.model_name)
    if len(existing_dimensions) > 1:
        raise EmbeddingError(
            f"Ready rows contain mixed dimensions for {embedder.model_name}: "
            f"{sorted(existing_dimensions)}"
        )
    expected_dimension = next(iter(existing_dimensions), None)

    while result.processed < limit:
        current_batch_size = min(batch_size, limit - result.processed)
        with session_factory() as session:
            pending_rows = list(
                session.execute(
                    select(DocumentChunk.id, DocumentChunk.content)
                    .join(Document)
                    .where(
                        DocumentChunk.embedding_status == "pending",
                        *_document_scope_filters(
                            normalized_source_types,
                            normalized_document_ids,
                        ),
                    )
                    .order_by(DocumentChunk.created_at, DocumentChunk.id)
                    .limit(current_batch_size)
                )
            )
        if not pending_rows:
            break

        empty_ids = [
            chunk_id
            for chunk_id, content in pending_rows
            if not normalize_text_for_embedding(content)
        ]
        valid_rows = [
            (chunk_id, normalize_text_for_embedding(content))
            for chunk_id, content in pending_rows
            if normalize_text_for_embedding(content)
        ]

        if empty_ids:
            with session_factory() as session, session.begin():
                for chunk_id in empty_ids:
                    chunk = session.get(DocumentChunk, chunk_id)
                    if chunk is None or chunk.embedding_status != "pending":
                        continue
                    _set_embedding_error(
                        chunk,
                        embedder.model_name,
                        "Chunk content is empty after normalization",
                        "skipped",
                    )
                    result.newly_skipped += 1

        if valid_rows:
            try:
                vectors = embedder.encode(
                    [content for _, content in valid_rows],
                    batch_size=min(batch_size, len(valid_rows)),
                )
                dimension = _validate_vectors(
                    vectors,
                    expected_count=len(valid_rows),
                    expected_dimension=expected_dimension,
                )
                expected_dimension = dimension
                result.vector_dimension = dimension
            except Exception as exc:
                with session_factory() as session, session.begin():
                    for chunk_id, _ in valid_rows:
                        chunk = session.get(DocumentChunk, chunk_id)
                        if chunk is None or chunk.embedding_status != "pending":
                            continue
                        _set_embedding_error(
                            chunk,
                            embedder.model_name,
                            f"{type(exc).__name__}: {exc}",
                            "failed",
                        )
                        result.newly_failed += 1
            else:
                with session_factory() as session, session.begin():
                    for (chunk_id, _), vector in zip(valid_rows, vectors, strict=True):
                        chunk = session.get(DocumentChunk, chunk_id)
                        if chunk is None or chunk.embedding_status != "pending":
                            continue
                        _clear_embedding_error(chunk)
                        chunk.embedding = vector
                        chunk.embedding_model = embedder.model_name
                        chunk.embedding_dimension = dimension
                        chunk.embedding_status = "ready"
                        result.newly_ready += 1

        result.processed += len(pending_rows)

    with session_factory() as session:
        result.status_counts = _status_counts(
            session,
            normalized_source_types,
            normalized_document_ids,
        )
        final_dimensions = _ready_dimensions(session, embedder.model_name)
    if len(final_dimensions) > 1:
        raise EmbeddingError(
            f"Stored rows contain mixed dimensions: {sorted(final_dimensions)}"
        )
    if final_dimensions:
        result.vector_dimension = next(iter(final_dimensions))
    return result


def search_similar_chunks(
    session_factory: Callable[[], Session],
    embedder: TextEmbedder,
    query: str,
    top_k: int = 5,
    min_similarity: float = 0.25,
    source_types: Sequence[str] | None = None,
    document_ids: Sequence[UUID] | None = None,
) -> tuple[int, list[SearchHit]]:
    if top_k <= 0:
        raise ValueError("top_k must be greater than zero")
    if not -1.0 <= min_similarity <= 1.0:
        raise ValueError("min_similarity must be between -1 and 1")
    normalized_query = normalize_text_for_embedding(query)
    if not normalized_query:
        raise ValueError("query must not be empty")

    normalized_source_types = normalize_source_types(source_types)
    normalized_document_ids = normalize_document_ids(document_ids)

    with session_factory() as session:
        dimensions = _ready_dimensions(
            session,
            embedder.model_name,
            normalized_source_types,
            normalized_document_ids,
        )
    if len(dimensions) > 1:
        raise EmbeddingError(
            f"Expected at most one ready vector dimension for {embedder.model_name}, "
            f"found {sorted(dimensions)}"
        )
    expected_dimension = next(iter(dimensions), None)
    query_vectors = embedder.encode([normalized_query], batch_size=1)
    dimension = _validate_vectors(
        query_vectors,
        expected_count=1,
        expected_dimension=expected_dimension,
    )
    query_vector = query_vectors[0]

    distance = DocumentChunk.embedding.cosine_distance(query_vector).label(
        "cosine_distance"
    )
    with session_factory() as session:
        rows = session.execute(
            select(Document, DocumentChunk, distance)
            .join(DocumentChunk, DocumentChunk.document_id == Document.id)
            .where(
                DocumentChunk.embedding_status == "ready",
                DocumentChunk.embedding.is_not(None),
                DocumentChunk.embedding_model == embedder.model_name,
                DocumentChunk.embedding_dimension == dimension,
                distance <= 1.0 - min_similarity,
                *_document_scope_filters(
                    normalized_source_types,
                    normalized_document_ids,
                ),
            )
            .order_by(distance)
            .limit(top_k)
        ).all()

    hits: list[SearchHit] = []
    for document, chunk, cosine_distance in rows:
        metadata = document.metadata_json or {}
        distance_value = float(cosine_distance)
        hits.append(
            SearchHit(
                document_id=document.id,
                chunk_id=chunk.id,
                external_id=document.external_id,
                title=document.title,
                source_type=document.source_type,
                publisher=document.publisher,
                page_number=chunk.page_number or chunk.page_start,
                dataset_type=metadata.get("dataset_type"),
                chunk_index=chunk.chunk_index,
                cosine_distance=distance_value,
                similarity=1.0 - distance_value,
                content_preview=chunk.content[:240],
                content_quality=metadata.get("content_quality"),
            )
        )
    return dimension, hits
