from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any, Mapping
from uuid import NAMESPACE_URL, UUID, uuid5
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import psycopg
import numpy as np
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from preprocessing.pdf_pipeline import (
    DoclingConversionError,
    DoclingDeploymentError,
    DoclingRuntimeSettings,
    PdfProcessingLimitError,
    chunk_text as _shared_chunk_text,
    validate_docling_runtime,
)
from rag_service.config import settings
from rag_service.pdf_processing import ProcessedChunk, process_document_pdf
from rag_service.retrieval import BgeM3Embedder, psycopg_database_url


logger = logging.getLogger(__name__)
_progress_lock = Lock()
_last_progress_write: dict[UUID, tuple[float, int, str]] = {}


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


@dataclass(frozen=True, slots=True)
class DocumentProfileExtraction:
    profile: dict[str, Any] | None
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


def update_job_progress(
    job: ClaimedJob,
    *,
    stage: str,
    percent: int,
    message: str,
    counters: Mapping[str, int] | None = None,
    force: bool = False,
) -> None:
    """Persist monotonic progress without coupling it to the document transaction."""

    bounded_percent = max(0, min(100, int(percent)))
    safe_message = str(message).strip()[:500] or "PDF 처리 중"
    now_monotonic = time.monotonic()
    with _progress_lock:
        previous = _last_progress_write.get(job.job_id)
        if (
            not force
            and previous is not None
            and previous[2] == stage
            and bounded_percent <= previous[1]
            and now_monotonic - previous[0] < 1.0
        ):
            return
        _last_progress_write[job.job_id] = (
            now_monotonic,
            max(previous[1], bounded_percent) if previous else bounded_percent,
            stage,
        )

    safe_counters = {
        key: max(0, int(value))
        for key, value in (counters or {}).items()
        if key
        in {
            "processed_pages",
            "total_pages",
            "processed_chunks",
            "total_chunks",
            "embedded_chunks",
        }
    }
    now = datetime.now(timezone.utc)
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE document_processing_jobs
                SET processing_stage = %s,
                    progress_percent = GREATEST(progress_percent, %s),
                    progress_message = %s,
                    progress_metadata =
                        COALESCE(progress_metadata, '{}'::jsonb) || %s,
                    progress_updated_at = %s,
                    updated_at = %s
                WHERE id = %s AND status = 'processing'
                """,
                (
                    stage,
                    bounded_percent,
                    safe_message,
                    Jsonb(safe_counters),
                    now,
                    now,
                    job.job_id,
                ),
            )
        connection.commit()


def _report_progress(
    job: ClaimedJob,
    stage: str,
    percent: int,
    message: str,
    counters: Mapping[str, int] | None = None,
    *,
    force: bool = False,
) -> None:
    try:
        update_job_progress(
            job,
            stage=stage,
            percent=percent,
            message=message,
            counters=counters,
            force=force,
        )
    except Exception:
        logger.exception(
            "Progress update failed document_id=%s document_version_id=%s stage=%s",
            job.document_id,
            job.version_id,
            stage,
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
                            processing_stage = 'queued', progress_percent = 15,
                            progress_message = '중단된 작업을 다시 처리할 예정입니다.',
                            progress_metadata = '{}'::jsonb,
                            progress_updated_at = %s,
                            updated_at = %s
                        WHERE id = %s
                        """,
                        (reason, now, now, now, job.job_id),
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
                    completed_at = NULL, error_message = NULL,
                    processing_stage = 'inspecting', progress_percent = 15,
                    progress_message = 'PDF 파일을 검사하고 있습니다.',
                    progress_metadata = '{}'::jsonb,
                    progress_updated_at = %s, updated_at = %s
                WHERE id = %s
                """,
                (attempts, now, now, now, now, row["job_id"]),
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
        model_name=_metadata_value(
            job.metadata, "model_name", "model_number", "모델명"
        ),
        chunk_size=settings.worker_chunk_characters,
        overlap=settings.worker_chunk_overlap,
        log_context={
            "document_id": job.document_id,
            "document_version_id": job.version_id,
            "original_filename": job.original_filename,
        },
        progress_callback=lambda stage, percent, message, counters: _report_progress(
            job,
            stage,
            percent,
            message,
            counters,
        ),
    )


def _document_profile_sample(chunks: tuple[ProcessedChunk, ...]) -> str:
    selected: list[str] = []
    seen_hashes: set[str] = set()
    signal_terms = (
        "모델",
        "형식",
        "사양",
        "정격",
        "설치",
        "장착",
        "배선",
        "결선",
        "주의",
        "경고",
        "기능",
        "설정",
        "부품",
        "구성",
    )
    for chunk in chunks[:12]:
        if chunk.content_hash not in seen_hashes:
            selected.append(chunk.content)
            seen_hashes.add(chunk.content_hash)
    for chunk in chunks[12:80]:
        text = chunk.content
        if chunk.content_hash in seen_hashes:
            continue
        if any(term in text for term in signal_terms):
            selected.append(text)
            seen_hashes.add(chunk.content_hash)
        if len(selected) >= 20:
            break
    sample = "\n\n".join(selected)
    return sample[:18000]


def _extract_document_profile(
    job: ClaimedJob,
    processed_chunks: tuple[ProcessedChunk, ...],
) -> DocumentProfileExtraction:
    if not settings.document_profile_extraction_enabled:
        return DocumentProfileExtraction(
            profile=None,
            metadata={"enabled": False, "status": "skipped"},
        )
    if not settings.qwen_service_url:
        return DocumentProfileExtraction(
            profile=None,
            metadata={
                "enabled": True,
                "status": "skipped",
                "reason": "QWEN_SERVICE_URL is not configured.",
            },
        )
    sample_text = _document_profile_sample(processed_chunks)
    if not sample_text.strip():
        return DocumentProfileExtraction(
            profile=None,
            metadata={
                "enabled": True,
                "status": "skipped",
                "reason": "No text sample was available.",
            },
        )
    payload = {
        "title": job.title,
        "original_filename": job.original_filename,
        "manufacturer": _metadata_value(job.metadata, "manufacturer", "제조사"),
        "product_type": _metadata_value(job.metadata, "product_type", "제품군"),
        "model_name": _metadata_value(
            job.metadata,
            "model_name",
            "model_number",
            "모델명",
        ),
        "document_type": job.document_type,
        "sample_text": sample_text,
    }
    url = f"{settings.qwen_service_url}/v1/document-profile"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "ngrok-skip-browser-warning": "true",
    }
    if settings.qwen_api_key:
        headers["Authorization"] = f"Bearer {settings.qwen_api_key}"
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=settings.qwen_timeout_seconds) as response:
            body = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        logger.warning(
            "Qwen document profile request failed document_id=%s status=%s detail=%s",
            job.document_id,
            exc.code,
            detail,
        )
        return DocumentProfileExtraction(
            profile=None,
            metadata={
                "enabled": True,
                "status": "failed",
                "reason": f"HTTP {exc.code}: {detail}",
            },
        )
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "Qwen document profile request failed document_id=%s error=%s",
            job.document_id,
            exc,
        )
        return DocumentProfileExtraction(
            profile=None,
            metadata={
                "enabled": True,
                "status": "failed",
                "reason": str(exc)[:300],
            },
        )
    profile = body.get("document_profile") if isinstance(body, dict) else None
    if not isinstance(profile, dict):
        return DocumentProfileExtraction(
            profile=None,
            metadata={
                "enabled": True,
                "status": "failed",
                "reason": "Response did not contain document_profile.",
            },
        )
    return DocumentProfileExtraction(
        profile=profile,
        metadata={
            "enabled": True,
            "status": "ready",
            "model": body.get("model"),
            "schema_version": 1,
        },
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
            processing_metadata=processed.processing_metadata,
        )
        return
    if processed.kind == "empty":
        mark_failed(
            job,
            "The PDF has no extractable text or image content.",
            retryable=False,
            processing_metadata=processed.processing_metadata,
        )
        return

    total_chunks = len(processed.chunks)
    base_counters = {
        "processed_pages": processed.page_count,
        "total_pages": processed.page_count,
        "processed_chunks": total_chunks,
        "total_chunks": total_chunks,
        "embedded_chunks": 0,
    }
    _report_progress(
        job,
        "chunking",
        60,
        "문서 구조화 메타데이터를 확인하고 있습니다.",
        base_counters,
        force=True,
    )
    profile_extraction = _extract_document_profile(job, processed.chunks)
    processing_metadata = {
        **processed.processing_metadata,
        "document_profile_extraction": profile_extraction.metadata,
    }
    _report_progress(
        job,
        "embedding",
        65,
        f"문서 청크 {total_chunks}개의 임베딩을 생성하고 있습니다.",
        base_counters,
        force=True,
    )
    batch_size = max(1, settings.worker_embedding_batch_size)
    vector_batches: list[np.ndarray] = []
    for start in range(0, total_chunks, batch_size):
        end = min(start + batch_size, total_chunks)
        vector_batches.append(
            embedder.encode_many(
                [chunk.content for chunk in processed.chunks[start:end]]
            )
        )
        percent = 65 + int((end / total_chunks) * 25)
        _report_progress(
            job,
            "embedding",
            percent,
            f"문서 청크 임베딩 생성 중 · {end}/{total_chunks}",
            {
                **base_counters,
                "embedded_chunks": end,
            },
        )
    vectors = np.concatenate(vector_batches, axis=0)
    if len(vectors.shape) != 2 or vectors.shape[0] != len(processed.chunks):
        raise RuntimeError("Embedding model returned an invalid result count.")
    if vectors.shape[1] <= 0:
        raise RuntimeError("Embedding model returned an invalid dimension.")

    _report_progress(
        job,
        "persisting",
        90,
        "청크와 임베딩을 DB에 저장하고 있습니다.",
        {
            **base_counters,
            "embedded_chunks": total_chunks,
        },
        force=True,
    )
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
                persisted = index + 1
                _report_progress(
                    job,
                    "persisting",
                    90 + int((persisted / total_chunks) * 8),
                    f"청크와 임베딩 DB 저장 중 · {persisted}/{total_chunks}",
                    {
                        **base_counters,
                        "embedded_chunks": total_chunks,
                    },
                )
            _report_progress(
                job,
                "validating",
                98,
                "저장된 청크와 임베딩을 검증하고 있습니다.",
                {
                    **base_counters,
                    "embedded_chunks": total_chunks,
                },
                force=True,
            )
            cursor.execute(
                """
                SELECT COUNT(*) AS stored_count,
                       COUNT(*) FILTER (
                           WHERE embedding_status = 'ready'
                             AND embedding IS NOT NULL
                       ) AS ready_count
                FROM document_chunks
                WHERE document_version_id = %s
                """,
                (job.version_id,),
            )
            verification = cursor.fetchone()
            stored_count = int(verification["stored_count"])
            ready_count = int(verification["ready_count"])
            if stored_count != total_chunks or ready_count != total_chunks:
                raise RuntimeError(
                    "Stored chunk and embedding verification failed."
                )
            cursor.execute(
                """
                UPDATE document_versions
                SET status = 'review_required', page_count = %s,
                    failure_reason = NULL, processing_metadata = %s,
                    updated_at = %s
                WHERE id = %s
                """,
                (
                    processed.page_count,
                    Jsonb(processing_metadata),
                    now,
                    job.version_id,
                ),
            )
            cursor.execute(
                """
                UPDATE document_processing_jobs
                SET status = 'completed', completed_at = %s,
                    heartbeat_at = %s, next_attempt_at = NULL,
                    error_message = NULL,
                    processing_stage = 'review_required',
                    progress_percent = 100,
                    progress_message =
                        'PDF 처리가 완료되었습니다. 관리자 승인이 필요합니다.',
                    progress_metadata = %s,
                    progress_updated_at = %s,
                    updated_at = %s
                WHERE id = %s
                """,
                (
                    now,
                    now,
                    Jsonb(
                        {
                            **base_counters,
                            "embedded_chunks": total_chunks,
                        }
                    ),
                    now,
                    now,
                    job.job_id,
                ),
            )
            cursor.execute(
                (
                    """
                    UPDATE documents
                    SET lifecycle_status = 'review_required',
                        metadata = COALESCE(metadata, '{}'::jsonb) || %s,
                        updated_at = %s
                    WHERE id = %s
                      AND current_version_id IS NULL
                      AND lifecycle_status <> 'deleted'
                    """
                    if profile_extraction.profile is not None
                    else """
                    UPDATE documents
                    SET lifecycle_status = 'review_required', updated_at = %s
                    WHERE id = %s
                      AND current_version_id IS NULL
                      AND lifecycle_status <> 'deleted'
                    """
                ),
                (
                    (
                        Jsonb(
                            {
                                "document_profile": profile_extraction.profile,
                                "document_profile_schema_version": 1,
                                "document_profile_source": "qwen",
                            }
                        ),
                        now,
                        job.document_id,
                    )
                    if profile_extraction.profile is not None
                    else (now, job.document_id)
                ),
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
                    **_processing_audit_payload(processing_metadata),
                },
            )
        connection.commit()
    with _progress_lock:
        _last_progress_write.pop(job.job_id, None)
    log_method = logger.warning if processing_metadata.get("fallback_used") else logger.info
    log_method(
        "PDF processing completed document_id=%s document_version_id=%s "
        "filename=%s extractor=%s extractor_version=%s fallback_used=%s ocr_used=%s",
        job.document_id,
        job.version_id,
        job.original_filename,
        processing_metadata.get("extractor"),
        processing_metadata.get("extractor_version"),
        bool(processing_metadata.get("fallback_used")),
        bool(processing_metadata.get("ocr_used")),
    )


def _processing_audit_payload(
    processing_metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    metadata = processing_metadata or {}
    return {
        "extractor": metadata.get("extractor"),
        "extractor_version": metadata.get("extractor_version"),
        "fallback_used": bool(metadata.get("fallback_used")),
        "ocr_used": bool(metadata.get("ocr_used")),
    }


def _mark_final_failure(
    cursor: psycopg.Cursor,
    job: ClaimedJob,
    reason: str,
    version_status: str,
    now: datetime,
    processing_metadata: dict[str, Any] | None = None,
) -> None:
    failure_stage = (
        "ocr_required" if version_status == "ocr_required" else "failed"
    )
    progress_message = (
        "텍스트를 확인할 수 없어 OCR 처리가 필요합니다."
        if version_status == "ocr_required"
        else "PDF 처리에 실패했습니다. 문서 상태와 Worker 로그를 확인해 주세요."
    )
    cursor.execute(
        """
        UPDATE document_versions
        SET status = %s, failure_reason = %s,
            processing_metadata = COALESCE(%s, processing_metadata),
            updated_at = %s
        WHERE id = %s AND status <> 'deleted'
        """,
        (
            version_status,
            reason,
            Jsonb(processing_metadata) if processing_metadata is not None else None,
            now,
            job.version_id,
        ),
    )
    cursor.execute(
        """
        UPDATE document_processing_jobs
        SET status = 'failed', error_message = %s, completed_at = %s,
            heartbeat_at = %s, next_attempt_at = NULL,
            processing_stage = %s, progress_message = %s,
            progress_updated_at = %s, updated_at = %s
        WHERE id = %s
        """,
        (
            reason,
            now,
            now,
            failure_stage,
            progress_message,
            now,
            now,
            job.job_id,
        ),
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
        payload={
            "reason": reason,
            "attempts": job.attempts,
            **_processing_audit_payload(processing_metadata),
        },
    )


def mark_failed(
    job: ClaimedJob,
    reason: str,
    *,
    version_status: str = "failed",
    retryable: bool = True,
    processing_metadata: dict[str, Any] | None = None,
) -> None:
    safe_reason = reason[:1000]
    now = datetime.now(timezone.utc)
    should_retry = retryable and job.attempts < settings.worker_max_attempts
    with _connect() as connection:
        with connection.cursor() as cursor:
            if not should_retry:
                _mark_final_failure(
                    cursor,
                    job,
                    safe_reason,
                    version_status,
                    now,
                    processing_metadata,
                )
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
                        processing_stage = 'queued', progress_percent = 15,
                        progress_message = '처리 오류로 재시도를 기다리고 있습니다.',
                        progress_metadata = '{}'::jsonb,
                        progress_updated_at = %s,
                        updated_at = %s
                    WHERE id = %s
                    """,
                    (safe_reason, next_attempt_at, now, now, job.job_id),
                )
                cursor.execute(
                    """
                    UPDATE document_versions
                    SET status = 'pending', failure_reason = %s,
                        processing_metadata = COALESCE(%s, processing_metadata),
                        updated_at = %s
                    WHERE id = %s AND status <> 'deleted'
                    """,
                    (
                        safe_reason,
                        Jsonb(processing_metadata)
                        if processing_metadata is not None
                        else None,
                        now,
                        job.version_id,
                    ),
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
                        **_processing_audit_payload(processing_metadata),
                    },
                )
        connection.commit()
    with _progress_lock:
        _last_progress_write.pop(job.job_id, None)


def _failure_processing_metadata(error: BaseException) -> dict[str, Any] | None:
    if isinstance(error, (DoclingDeploymentError, DoclingConversionError)):
        return {
            "extractor": "docling",
            "extractor_version": None,
            "fallback_used": False,
            "fallback_reason": f"{type(error).__name__}: {str(error)[:400]}",
            "ocr_used": False,
        }
    if isinstance(error, PdfProcessingLimitError):
        return {
            "extractor": "none",
            "extractor_version": None,
            "fallback_used": False,
            "fallback_reason": f"{type(error).__name__}: {str(error)[:400]}",
            "ocr_used": False,
        }
    return None


def _update_heartbeat(job: ClaimedJob) -> None:
    now = datetime.now(timezone.utc)
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE document_processing_jobs
                SET heartbeat_at = %s, updated_at = %s
                WHERE id = %s AND status = 'processing'
                """,
                (now, now, job.job_id),
            )
        connection.commit()


@contextmanager
def _job_heartbeat(job: ClaimedJob):
    interval = max(
        1.0,
        min(
            settings.worker_heartbeat_seconds,
            max(1.0, settings.worker_stale_after_seconds / 3),
        ),
    )
    stop_event = Event()

    def heartbeat_loop() -> None:
        while not stop_event.wait(interval):
            try:
                _update_heartbeat(job)
            except Exception:
                logger.exception(
                    "Worker heartbeat update failed document_id=%s "
                    "document_version_id=%s filename=%s",
                    job.document_id,
                    job.version_id,
                    job.original_filename,
                )

    thread = Thread(
        target=heartbeat_loop,
        name=f"document-heartbeat-{job.job_id}",
        daemon=True,
    )
    thread.start()
    try:
        yield
    finally:
        stop_event.set()
        thread.join(timeout=min(interval, 5.0))


def run_forever() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    docling_runtime = DoclingRuntimeSettings.from_env()
    try:
        docling_version = validate_docling_runtime(docling_runtime)
    except DoclingDeploymentError:
        if docling_runtime.required:
            logger.critical(
                "Docling worker preflight failed; worker will not start.",
                exc_info=True,
            )
            raise
        logger.warning(
            "Docling worker preflight failed but DOCLING_REQUIRED=false; "
            "document processing may use PyMuPDF fallback.",
            exc_info=True,
        )
    else:
        logger.info(
            "Docling worker preflight passed version=%s artifacts_path=%s "
            "offline=%s max_file_bytes=%s max_pages=%s num_threads=%s",
            docling_version,
            docling_runtime.artifacts_path or "default",
            docling_runtime.offline,
            (
                docling_runtime.max_file_bytes
                if docling_runtime.max_file_bytes is not None
                else "unlimited"
            ),
            (
                docling_runtime.max_pages
                if docling_runtime.max_pages is not None
                else "unlimited"
            ),
            docling_runtime.num_threads,
        )
    embedder = BgeM3Embedder(settings)
    while True:
        try:
            recover_stale_jobs()
            job = claim_job()
            if job is None:
                time.sleep(settings.worker_poll_seconds)
                continue
            try:
                with _job_heartbeat(job):
                    complete_job(job, embedder)
            except Exception as exc:
                logger.exception(
                    "PDF processing failed document_id=%s document_version_id=%s "
                    "filename=%s",
                    job.document_id,
                    job.version_id,
                    job.original_filename,
                )
                mark_failed(
                    job,
                    f"{type(exc).__name__}: {exc}",
                    processing_metadata=_failure_processing_metadata(exc),
                )
        except KeyboardInterrupt:
            return
        except Exception:
            time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    run_forever()
