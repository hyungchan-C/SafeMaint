from datetime import datetime, timezone
from os import getenv
from pathlib import Path
from uuid import uuid4

import fitz
import numpy as np
import psycopg
import pytest

from rag_service.config import settings
from rag_service import pdf_processing
from rag_service.retrieval import psycopg_database_url
from rag_service.worker import claim_job, complete_job


pytestmark = pytest.mark.skipif(
    getenv("RUN_DB_INTEGRATION") != "1",
    reason="Set RUN_DB_INTEGRATION=1 to run worker DB integration tests.",
)


class FakeEmbedder:
    def encode_many(self, texts: list[str]) -> np.ndarray:
        assert texts
        return np.asarray([[1.0, 0.0, 0.0] for _ in texts], dtype=np.float32)


def test_worker_extracts_embeds_and_moves_version_to_review(
    monkeypatch,
    tmp_path: Path,
) -> None:
    assert settings.database_url.rsplit("/", 1)[-1].endswith("_test")
    path = tmp_path / "worker-manual.pdf"
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Disconnect power and apply lockout before maintenance.")
    pdf.save(path)
    pdf.close()

    def fake_process_pdf(*_args, **_kwargs) -> dict:
        content = "Disconnect power and apply lockout before maintenance."
        return {
            "document": {"external_id": "worker-test"},
            "chunks": [
                {
                    "document_external_id": "worker-test",
                    "chunk_index": 0,
                    "content": content,
                    "page_number": 1,
                    "section_path": ["Safety"],
                    "metadata": {},
                }
            ],
            "processing_metadata": {
                "extractor": "docling",
                "extractor_version": "2.113.0",
                "fallback_used": False,
                "fallback_reason": None,
                "ocr_used": False,
            },
        }

    monkeypatch.setattr(pdf_processing, "process_pdf", fake_process_pdf)

    document_id = uuid4()
    version_id = uuid4()
    job_id = uuid4()
    now = datetime.now(timezone.utc)
    database_url = psycopg_database_url(settings.database_url)
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO documents
                    (id, external_id, title, source_type, document_type_code,
                     lifecycle_status, access_level, metadata)
                VALUES (%s, %s, 'Worker integration manual', 'equipment_manual',
                        'equipment_manual', 'pending', 'restricted', '{}'::jsonb)
                """,
                (document_id, f"worker-test:{document_id}"),
            )
            cursor.execute(
                """
                INSERT INTO document_versions
                    (id, document_id, version_number, original_filename,
                     stored_filename, storage_path, sha256, file_size,
                     mime_type, status)
                VALUES (%s, %s, 1, 'worker-manual.pdf', %s, %s, %s, %s,
                        'application/pdf', 'pending')
                """,
                (
                    version_id,
                    document_id,
                    f"{version_id}.pdf",
                    str(path),
                    "c" * 64,
                    path.stat().st_size,
                ),
            )
            cursor.execute(
                """
                INSERT INTO document_processing_jobs
                    (id, document_version_id, status, attempts, created_at, updated_at)
                VALUES (%s, %s, 'queued', 0, %s, %s)
                """,
                (job_id, version_id, now, now),
            )
        connection.commit()

    claimed = claim_job(version_id)
    assert claimed is not None
    complete_job(claimed, FakeEmbedder())  # type: ignore[arg-type]

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT status, page_count, processing_metadata
                FROM document_versions WHERE id = %s
                """,
                (version_id,),
            )
            status, page_count, processing_metadata = cursor.fetchone()
            assert (status, page_count) == ("review_required", 1)
            assert processing_metadata["extractor"] == "docling"
            assert processing_metadata["fallback_used"] is False
            cursor.execute(
                "SELECT status FROM document_processing_jobs WHERE id = %s", (job_id,)
            )
            assert cursor.fetchone()[0] == "completed"
            cursor.execute(
                """
                SELECT COUNT(*), min(embedding_dimension), min(embedding_status)
                FROM document_chunks WHERE document_version_id = %s
                """,
                (version_id,),
            )
            count, dimension, embedding_status = cursor.fetchone()
            assert count >= 1
            assert dimension == 3
            assert embedding_status == "ready"

            cursor.execute(
                "SELECT id FROM document_chunks WHERE document_version_id = %s ORDER BY chunk_index",
                (version_id,),
            )
            original_chunk_ids = [row[0] for row in cursor.fetchall()]

    # A repeated completion for the same version replaces rows atomically and
    # preserves deterministic chunk identifiers instead of creating duplicates.
    complete_job(claimed, FakeEmbedder())  # type: ignore[arg-type]

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM document_chunks WHERE document_version_id = %s ORDER BY chunk_index",
                (version_id,),
            )
            assert [row[0] for row in cursor.fetchall()] == original_chunk_ids
            cursor.execute("DELETE FROM documents WHERE id = %s", (document_id,))
        connection.commit()
