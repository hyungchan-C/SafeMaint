from pathlib import Path

import fitz

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
