from pathlib import Path

import fitz

from rag_service.worker import _chunk_text, _extract


def test_chunk_text_has_overlap_and_no_empty_chunks() -> None:
    chunks = _chunk_text("A" * 120 + "\n" + "B" * 120, size=100, overlap=20)

    assert len(chunks) >= 3
    assert all(chunks)
    assert chunks[0][-20:] == chunks[1][:20]


def test_local_pdf_text_is_extracted(tmp_path: Path) -> None:
    path = tmp_path / "manual.pdf"
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "SafeMaint local PDF extraction")
    pdf.save(path)
    pdf.close()

    page_count, chunks = _extract(path)

    assert page_count == 1
    assert chunks[0][0] == 1
    assert "SafeMaint local PDF extraction" in chunks[0][1]
