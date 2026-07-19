from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from preprocessing.pdf_pipeline import chunk_text as _shared_chunk_text
from rag_service.config import settings
from rag_service.pdf_processing import ProcessedChunk, process_document_pdf
from rag_service.retrieval import BgeM3Embedder, psycopg_database_url


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    job_id: UUID
    version_id: UUID
    document_id: UUID
    storage_path: str
    attempts: int
    title: str
    original_filename: str
    document_type: str
    metadata: dict[str, Any]


def _connect() -> psycopg.Connection:
    connection = psycopg.connect(
        psycopg_database_url(settings.database_url),
        row_factory=dict_row,
    )
    register_vector(connection)
    return connection


def _audit(
    cursor: psycopg.Cursor,
    *,
    event_type: str,
    job: ClaimedJob,
    success: bool,
    payload: dict[str, Any],
) -> None:
    cursor.execute(
        """
        INSERT INTO audit_events
            (event_type, entity_type, entity_id, document_version_id,
             success, payload)
        VALUES (%s, 'document', %s, %s, %s, %s)
        """,
        (
            event_type,
            job.document_id,
            job.version_id,
            success,
            Jsonb(payload),
        ),
    )


def recover_stale_jobs() -> int:
    """Requeue interrupted work, or fail it once its retry budget is exhausted."""
    cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=settings.worker_stale_after_seconds
    )
    recovered = 0
    now = datetime.now(timezone.utc)
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT j.id AS job_id, j.attempts, dv.id AS version_id,
                       dv.document_id, dv.storage_path, dv.original_filename,
                       d.title, d.document_type_code, d.metadata
                FROM document_processing_jobs j
                JOIN document_versions dv ON dv.id = j.document_version_id
                JOIN documents d ON d.id = dv.document_id
                WHERE j.status = 'processing'
                  AND COALESCE(j.heartbeat_at, j.started_at, j.updated_at) < %s
                  AND dv.status = 'processing'
                  AND d.lifecycle_status <> 'deleted'
                ORDER BY j.created_at, j.id
                FOR UPDATE OF j SKIP LOCKED
                """,
                (cutoff,),
            )
            for row in cursor.fetchall():
                job = _claimed_job(row)
                recovered += 1
                reason = "Worker heartbeat expired before processing completed."
                if job.attempts >= settings.worker_max_attempts:
                    _mark_final_failure(cursor, job, reason, "failed", now)
                else:
                    cursor.execute(
                        """
                        UPDATE document_processing_jobs
                        SET status = 'queued', error_message = %s,
                            started_at = NULL, heartbeat_at = NULL,
                            next_attempt_at = %s, completed_at = NULL,
                            updated_at = %s
                        WHERE id = %s
                        """,
                        (reason, now, now, job.job_id),
                    )
                    cursor.execute(
                        """
                        UPDATE document_versions
                        SET status = 'pending', failure_reason = %s, updated_at = %s
                        WHERE id = %s AND status <> 'deleted'
                        """,
                        (reason, now, job.version_id),
                    )
                    cursor.execute(
                        """
                        UPDATE documents
                        SET lifecycle_status = 'pending', updated_at = %s
                        WHERE id = %s AND current_version_id IS NULL
                          AND lifecycle_status <> 'deleted'
                        """,
                        (now, job.document_id),
                    )
                    _audit(
                        cursor,
                        event_type="DOCUMENT_PROCESSING_REQUEUED",
                        job=job,
                        success=False,
                        payload={"reason": reason, "attempts": job.attempts},
                    )
        connection.commit()
    return recovered


def _claimed_job(row: dict[str, Any]) -> ClaimedJob:
    return ClaimedJob(
        job_id=row["job_id"],
        version_id=row["version_id"],
        document_id=row["document_id"],
        storage_path=row["storage_path"],
        attempts=int(row["attempts"]),
        title=row["title"],
        original_filename=row["original_filename"],
        document_type=row["document_type_code"],
        metadata=dict(row.get("metadata") or {}),
    )


def claim_job(document_version_id: UUID | None = None) -> ClaimedJob | None:
    with _connect() as connection:
        with connection.cursor() as cursor:
            version_filter = "AND dv.id = %s" if document_version_id else ""
            cursor.execute(
                f"""
                SELECT j.id AS job_id, j.attempts, dv.id AS version_id,
                       dv.document_id, dv.storage_path, dv.original_filename,
                       d.title, d.document_type_code, d.metadata
                FROM document_processing_jobs j
                JOIN document_versions dv ON dv.id = j.document_version_id
                JOIN documents d ON d.id = dv.document_id
                WHERE j.status = 'queued'
                  AND j.attempts < %s
                  AND (j.next_attempt_at IS NULL OR j.next_attempt_at <= now())
                  AND dv.status = 'pending'
                  AND d.lifecycle_status <> 'deleted'
                  {version_filter}
                ORDER BY j.created_at, j.id
                FOR UPDATE OF j SKIP LOCKED
                LIMIT 1
                """,
                (
                    (settings.worker_max_attempts, document_version_id)
                    if document_version_id
                    else (settings.worker_max_attempts,)
                ),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            now = datetime.now(timezone.utc)
            attempts = int(row["attempts"]) + 1
            cursor.execute(
                """
                UPDATE document_processing_jobs
                SET status = 'processing', attempts = %s, started_at = %s,
                    heartbeat_at = %s, next_attempt_at = NULL,
                    completed_at = NULL, error_message = NULL, updated_at = %s
                WHERE id = %s
                """,
                (attempts, now, now, now, row["job_id"]),
            )
            cursor.execute(
                """
                UPDATE document_versions
                SET status = 'processing', failure_reason = NULL, updated_at = %s
                WHERE id = %s
                """,
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
            row["attempts"] = attempts
            job = _claimed_job(row)
            _audit(
                cursor,
                event_type="DOCUMENT_PROCESSING_STARTED",
                job=job,
                success=True,
                payload={"attempt": attempts},
            )
        connection.commit()
    return job


def _metadata_value(metadata: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = metadata.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _chunk_text(text: str, size: int, overlap: int) -> list[str]:
    """Compatibility wrapper; all chunking delegates to the shared pipeline."""
    return _shared_chunk_text(text, chunk_size=size, overlap=overlap)


def _process(job: ClaimedJob):
    return process_document_pdf(
        job.storage_path,
        title=job.title,
        manufacturer=_metadata_value(job.metadata, "manufacturer", "제조사"),
        product_type=_metadata_value(
            job.metadata, "product_type", "제품군"
        ) or job.document_type,
        model_name=_metadata_value(job.metadata, "model_name", "모델명"),
        chunk_size=settings.worker_chunk_characters,
        overlap=settings.worker_chunk_overlap,
    )


def _extract(path: Path) -> tuple[int, list[tuple[int, str]]]:
    """Backward-compatible test helper backed by the common PDF pipeline."""
    result = process_document_pdf(
        path,
        title=path.stem,
        chunk_size=settings.worker_chunk_characters,
        overlap=settings.worker_chunk_overlap,
    )
    return result.page_count, [
        (chunk.page_start or 1, chunk.content) for chunk in result.chunks
    ]


def _chunk_uuid(version_id: UUID, chunk: ProcessedChunk) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"safemaint:{version_id}:{chunk.source_chunk_id}:{chunk.content_hash}",
    )


def complete_job(job: ClaimedJob, embedder: BgeM3Embedder) -> None:
    processed = _process(job)
    if processed.kind == "scanned":
        mark_failed(
            job,
            "The PDF is image-only and local OCR is required.",
            version_status="ocr_required",
            retryable=False,
        )
        return
    if processed.kind == "empty":
        mark_failed(
            job,
            "The PDF has no extractable text or image content.",
            retryable=False,
        )
        return

    vectors = embedder.encode_many([chunk.content for chunk in processed.chunks])
    if len(vectors.shape) != 2 or vectors.shape[0] != len(processed.chunks):
        raise RuntimeError("Embedding model returned an invalid result count.")
    if vectors.shape[1] <= 0:
        raise RuntimeError("Embedding model returned an invalid dimension.")

    now = datetime.now(timezone.utc)
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM document_chunks WHERE document_version_id = %s",
                (job.version_id,),
            )
            for index, (chunk, vector) in enumerate(
                zip(processed.chunks, vectors, strict=True)
            ):
                cursor.execute(
                    """
                    INSERT INTO document_chunks
                        (id, document_id, document_version_id, chunk_index,
                         page_number, page_start, page_end, section_path,
                         content, content_hash, metadata, embedding,
                         embedding_model, embedding_dimension, embedding_status)
                    VALUES
                        (%s, %s, %s, %s, %s, %s, %s, %s,
                         %s, %s, %s, %s, %s, %s, 'ready')
                    """,
                    (
                        _chunk_uuid(job.version_id, chunk),
                        job.document_id,
                        job.version_id,
                        index,
                        chunk.page_start,
                        chunk.page_start,
                        chunk.page_end,
                        Jsonb(list(chunk.section_path)),
                        chunk.content,
                        chunk.content_hash,
                        Jsonb(chunk.metadata),
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
                (processed.page_count, now, job.version_id),
            )
            cursor.execute(
                """
                UPDATE document_processing_jobs
                SET status = 'completed', completed_at = %s,
                    heartbeat_at = %s, next_attempt_at = NULL,
                    error_message = NULL, updated_at = %s
                WHERE id = %s
                """,
                (now, now, now, job.job_id),
            )
            cursor.execute(
                """
                UPDATE documents
                SET lifecycle_status = 'review_required', updated_at = %s
                WHERE id = %s AND current_version_id IS NULL
                """,
                (now, job.document_id),
            )
            _audit(
                cursor,
                event_type="DOCUMENT_PROCESSING_COMPLETED",
                job=job,
                success=True,
                payload={
                    "attempt": job.attempts,
                    "chunk_count": len(processed.chunks),
                    "page_count": processed.page_count,
                },
            )
        connection.commit()


def _mark_final_failure(
    cursor: psycopg.Cursor,
    job: ClaimedJob,
    reason: str,
    version_status: str,
    now: datetime,
) -> None:
    cursor.execute(
        """
        UPDATE document_versions
        SET status = %s, failure_reason = %s, updated_at = %s
        WHERE id = %s AND status <> 'deleted'
        """,
        (version_status, reason, now, job.version_id),
    )
    cursor.execute(
        """
        UPDATE document_processing_jobs
        SET status = 'failed', error_message = %s, completed_at = %s,
            heartbeat_at = %s, next_attempt_at = NULL, updated_at = %s
        WHERE id = %s
        """,
        (reason, now, now, now, job.job_id),
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
    _audit(
        cursor,
        event_type="DOCUMENT_PROCESSING_FAILED",
        job=job,
        success=False,
        payload={"reason": reason, "attempts": job.attempts},
    )


def mark_failed(
    job: ClaimedJob,
    reason: str,
    *,
    version_status: str = "failed",
    retryable: bool = True,
) -> None:
    safe_reason = reason[:1000]
    now = datetime.now(timezone.utc)
    should_retry = retryable and job.attempts < settings.worker_max_attempts
    with _connect() as connection:
        with connection.cursor() as cursor:
            if not should_retry:
                _mark_final_failure(cursor, job, safe_reason, version_status, now)
            else:
                next_attempt_at = now + timedelta(
                    seconds=settings.worker_retry_delay_seconds
                )
                cursor.execute(
                    """
                    UPDATE document_processing_jobs
                    SET status = 'queued', error_message = %s,
                        started_at = NULL, heartbeat_at = NULL,
                        next_attempt_at = %s, completed_at = NULL,
                        updated_at = %s
                    WHERE id = %s
                    """,
                    (safe_reason, next_attempt_at, now, job.job_id),
                )
                cursor.execute(
                    """
                    UPDATE document_versions
                    SET status = 'pending', failure_reason = %s, updated_at = %s
                    WHERE id = %s AND status <> 'deleted'
                    """,
                    (safe_reason, now, job.version_id),
                )
                cursor.execute(
                    """
                    UPDATE documents
                    SET lifecycle_status = 'pending', updated_at = %s
                    WHERE id = %s AND current_version_id IS NULL
                      AND lifecycle_status <> 'deleted'
                    """,
                    (now, job.document_id),
                )
                _audit(
                    cursor,
                    event_type="DOCUMENT_PROCESSING_RETRY_SCHEDULED",
                    job=job,
                    success=False,
                    payload={
                        "reason": safe_reason,
                        "attempts": job.attempts,
                        "next_attempt_at": next_attempt_at.isoformat(),
                    },
                )
        connection.commit()


def run_forever() -> None:
    embedder = BgeM3Embedder(settings)
    while True:
        try:
            recover_stale_jobs()
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
