from pathlib import Path
from uuid import uuid4

import fitz
import numpy as np

from rag_service.pdf_processing import ProcessedChunk, ProcessedPdf
from rag_service import worker


def test_chunk_text_has_overlap_and_no_empty_chunks() -> None:
    chunks = worker._chunk_text("A" * 120 + "\n" + "B" * 120, size=100, overlap=20)

    assert len(chunks) >= 3
    assert all(chunks)
    assert chunks[0][-20:] == chunks[1][:20]


def test_local_pdf_text_is_extracted(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "manual.pdf"
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "SafeMaint local PDF extraction")
    pdf.save(path)
    pdf.close()
    content = "SafeMaint local PDF extraction"
    monkeypatch.setattr(
        worker,
        "process_document_pdf",
        lambda *_args, **_kwargs: ProcessedPdf(
            kind="text",
            page_count=1,
            chunks=(
                ProcessedChunk(
                    source_chunk_id="chunk-1",
                    content=content,
                    content_hash="hash-1",
                    page_start=1,
                    page_end=1,
                    section_path=("Safety",),
                    metadata={},
                ),
            ),
            processing_metadata={
                "extractor": "docling",
                "extractor_version": "2.113.0",
                "fallback_used": False,
                "fallback_reason": None,
                "ocr_used": False,
            },
        ),
    )

    page_count, chunks = worker._extract(path)

    assert page_count == 1
    assert chunks[0][0] == 1
    assert "SafeMaint local PDF extraction" in chunks[0][1]


def test_completion_status_update_never_resurrects_deleted_document(
    monkeypatch,
) -> None:
    executed_sql: list[str] = []

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, statement: str, _params=None) -> None:
            executed_sql.append(" ".join(statement.split()))

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def cursor(self) -> FakeCursor:
            return FakeCursor()

        def commit(self) -> None:
            pass

    processed = ProcessedPdf(
        kind="text",
        page_count=1,
        chunks=(
            ProcessedChunk(
                source_chunk_id="chunk-1",
                content="검증된 매뉴얼 내용",
                content_hash="hash-1",
                page_start=1,
                page_end=1,
                section_path=("안전",),
                metadata={},
            ),
        ),
        processing_metadata={
            "extractor": "docling",
            "fallback_used": False,
        },
    )
    monkeypatch.setattr(worker, "_process", lambda _job: processed)
    monkeypatch.setattr(worker, "_connect", lambda: FakeConnection())

    class FakeEmbedder:
        def encode_many(self, _texts: list[str]) -> np.ndarray:
            return np.asarray([[1.0, 0.0, 0.0]], dtype=np.float32)

    job = worker.ClaimedJob(
        job_id=uuid4(),
        version_id=uuid4(),
        document_id=uuid4(),
        storage_path="manual.pdf",
        attempts=1,
        title="테스트 매뉴얼",
        original_filename="manual.pdf",
        document_type="equipment_manual",
        metadata={},
    )

    worker.complete_job(job, FakeEmbedder())  # type: ignore[arg-type]

    document_status_updates = [
        statement
        for statement in executed_sql
        if statement.startswith("UPDATE documents")
        and "lifecycle_status = 'review_required'" in statement
    ]
    assert len(document_status_updates) == 1
    assert "lifecycle_status <> 'deleted'" in document_status_updates[0]
