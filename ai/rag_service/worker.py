from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import fitz
import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row

from rag_service.config import settings
from rag_service.retrieval import BgeM3Embedder, psycopg_database_url


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    job_id: UUID
    version_id: UUID
    document_id: UUID
    storage_path: str


def _connect() -> psycopg.Connection:
    connection = psycopg.connect(
        psycopg_database_url(settings.database_url),
        row_factory=dict_row,
    )
    register_vector(connection)
    return connection


def claim_job(document_version_id: UUID | None = None) -> ClaimedJob | None:
    with _connect() as connection:
        with connection.cursor() as cursor:
            version_filter = "AND dv.id = %s" if document_version_id else ""
            cursor.execute(
                f"""
                SELECT j.id AS job_id, dv.id AS version_id,
                       dv.document_id, dv.storage_path
                FROM document_processing_jobs j
                JOIN document_versions dv ON dv.id = j.document_version_id
                JOIN documents d ON d.id = dv.document_id
                WHERE j.status = 'queued'
                  AND dv.status = 'pending'
                  AND d.lifecycle_status <> 'deleted'
                  {version_filter}
                ORDER BY j.created_at, j.id
                FOR UPDATE OF j SKIP LOCKED
                LIMIT 1
                """,
                ((document_version_id,) if document_version_id else None),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            now = datetime.now(timezone.utc)
            cursor.execute(
                """
                UPDATE document_processing_jobs
                SET status = 'processing', attempts = attempts + 1,
                    started_at = %s, error_message = NULL, updated_at = %s
                WHERE id = %s
                """,
                (now, now, row["job_id"]),
            )
            cursor.execute(
                "UPDATE document_versions SET status = 'processing', updated_at = %s WHERE id = %s",
                (now, row["version_id"]),
            )
            cursor.execute(
                """
                UPDATE documents
                SET lifecycle_status = 'processing', updated_at = %s
                WHERE id = %s AND current_version_id IS NULL
                """,
                (now, row["document_id"]),
            )
            cursor.execute(
                """
                INSERT INTO audit_events
                    (event_type, entity_type, entity_id, document_version_id,
                     success, payload)
                VALUES
                    ('DOCUMENT_PROCESSING_STARTED', 'document', %s, %s, true, '{}'::jsonb)
                """,
                (row["document_id"], row["version_id"]),
            )
        connection.commit()
    return ClaimedJob(
        job_id=row["job_id"],
        version_id=row["version_id"],
        document_id=row["document_id"],
        storage_path=row["storage_path"],
    )


def _chunk_text(text: str, size: int, overlap: int) -> list[str]:
    normalized = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    if not normalized:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(len(normalized), start + size)
        if end < len(normalized):
            boundary = normalized.rfind("\n", start, end)
            if boundary > start + (size // 2):
                end = boundary
        chunks.append(normalized[start:end].strip())
        if end >= len(normalized):
            break
        start = max(start + 1, end - overlap)
    return [chunk for chunk in chunks if chunk]


def _extract(path: Path) -> tuple[int, list[tuple[int, str]]]:
    pages: list[tuple[int, str]] = []
    with fitz.open(path) as pdf:
        page_count = pdf.page_count
        for index, page in enumerate(pdf, start=1):
            for chunk in _chunk_text(
                page.get_text("text"),
                settings.worker_chunk_characters,
                settings.worker_chunk_overlap,
            ):
                pages.append((index, chunk))
    return page_count, pages


def complete_job(job: ClaimedJob, embedder: BgeM3Embedder) -> None:
    path = Path(job.storage_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Stored PDF does not exist: {path}")
    page_count, chunks = _extract(path)
    if not chunks:
        mark_failed(
            job,
            "No extractable text was found. Local OCR is required.",
            version_status="ocr_required",
        )
        return
    vectors = embedder.encode_many([content for _, content in chunks])
    if vectors.shape[1] <= 0:
        raise RuntimeError("Embedding model returned an invalid dimension.")
    now = datetime.now(timezone.utc)
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM document_chunks WHERE document_version_id = %s",
                (job.version_id,),
            )
            for index, ((page_number, content), vector) in enumerate(zip(chunks, vectors, strict=True)):
                cursor.execute(
                    """
                    INSERT INTO document_chunks
                        (document_id, document_version_id, chunk_index,
                         page_number, page_start, page_end, section_path,
                         content, content_hash, metadata, embedding,
                         embedding_model, embedding_dimension, embedding_status)
                    VALUES
                        (%s, %s, %s, %s, %s, %s, '[]'::jsonb,
                         %s, %s, '{}'::jsonb, %s, %s, %s, 'ready')
                    """,
                    (
                        job.document_id,
                        job.version_id,
                        index,
                        page_number,
                        page_number,
                        page_number,
                        content,
                        hashlib.sha256(content.encode("utf-8")).hexdigest(),
                        vector,
                        settings.model_name,
                        int(vectors.shape[1]),
                    ),
                )
            cursor.execute(
                """
                UPDATE document_versions
                SET status = 'review_required', page_count = %s,
                    failure_reason = NULL, updated_at = %s
                WHERE id = %s
                """,
                (page_count, now, job.version_id),
            )
            cursor.execute(
                """
                UPDATE document_processing_jobs
                SET status = 'completed', completed_at = %s,
                    error_message = NULL, updated_at = %s
                WHERE id = %s
                """,
                (now, now, job.job_id),
            )
            cursor.execute(
                """
                UPDATE documents
                SET lifecycle_status = 'review_required', updated_at = %s
                WHERE id = %s AND current_version_id IS NULL
                """,
                (now, job.document_id),
            )
        connection.commit()


def mark_failed(
    job: ClaimedJob,
    reason: str,
    *,
    version_status: str = "failed",
) -> None:
    safe_reason = reason[:1000]
    now = datetime.now(timezone.utc)
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE document_versions
                SET status = %s, failure_reason = %s, updated_at = %s
                WHERE id = %s AND status <> 'deleted'
                """,
                (version_status, safe_reason, now, job.version_id),
            )
            cursor.execute(
                """
                UPDATE document_processing_jobs
                SET status = 'failed', error_message = %s,
                    completed_at = %s, updated_at = %s
                WHERE id = %s
                """,
                (safe_reason, now, now, job.job_id),
            )
            cursor.execute(
                """
                UPDATE documents
                SET lifecycle_status = 'failed', updated_at = %s
                WHERE id = %s AND current_version_id IS NULL
                  AND lifecycle_status <> 'deleted'
                """,
                (now, job.document_id),
            )
            cursor.execute(
                """
                INSERT INTO audit_events
                    (event_type, entity_type, entity_id, document_version_id,
                     success, payload)
                VALUES
                    ('DOCUMENT_PROCESSING_FAILED', 'document', %s, %s, false,
                     jsonb_build_object('reason', %s))
                """,
                (job.document_id, job.version_id, safe_reason),
            )
        connection.commit()


def run_forever() -> None:
    embedder = BgeM3Embedder(settings)
    while True:
        try:
            job = claim_job()
            if job is None:
                time.sleep(settings.worker_poll_seconds)
                continue
            try:
                complete_job(job, embedder)
            except Exception as exc:
                mark_failed(job, f"{type(exc).__name__}: {exc}")
        except KeyboardInterrupt:
            return
        except Exception:
            time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    run_forever()
