from pathlib import Path

import fitz

from rag_service import pdf_processing


def _fake_pipeline_result(*_args, **_kwargs) -> dict:
    content = "Disconnect power before conveyor maintenance."
    return {
        "document": {"external_id": "manual:SafeMaint:CV-203:manual"},
        "chunks": [
            {
                "document_external_id": "manual:SafeMaint:CV-203:manual",
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


def _fake_empty_pipeline_result(*_args, **_kwargs) -> dict:
    result = _fake_pipeline_result()
    result["chunks"] = []
    return result


def _save_text_pdf(path: Path) -> None:
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Disconnect power before conveyor maintenance.")
    pdf.save(path)
    pdf.close()


def test_text_pdf_keeps_normalized_metadata_and_stable_ids(
    monkeypatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "manual.pdf"
    _save_text_pdf(path)
    monkeypatch.setattr(pdf_processing, "process_pdf", _fake_pipeline_result)

    first = pdf_processing.process_document_pdf(
        path,
        title="Conveyor manual",
        manufacturer="SafeMaint",
        product_type="conveyor",
        model_name="CV-203",
    )
    second = pdf_processing.process_document_pdf(
        path,
        title="Conveyor manual",
        manufacturer="SafeMaint",
        product_type="conveyor",
        model_name="CV-203",
    )

    assert first.kind == "text"
    assert first.page_count == 1
    assert first.chunks
    assert [chunk.source_chunk_id for chunk in first.chunks] == [
        chunk.source_chunk_id for chunk in second.chunks
    ]
    assert [chunk.content_hash for chunk in first.chunks] == [
        chunk.content_hash for chunk in second.chunks
    ]
    assert first.chunks[0].metadata["manufacturer"] == "SafeMaint"
    assert first.chunks[0].page_start == 1
    assert first.processing_metadata["extractor"] == "docling"
    assert first.processing_metadata["fallback_used"] is False


def test_image_only_pdf_is_classified_as_scanned(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "scan.pdf"
    pdf = fitz.open()
    page = pdf.new_page()
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 20), False)
    pixmap.clear_with(255)
    page.insert_image(page.rect, pixmap=pixmap)
    pdf.save(path)
    pdf.close()
    monkeypatch.setattr(pdf_processing, "process_pdf", _fake_empty_pipeline_result)

    result = pdf_processing.process_document_pdf(path, title="Scanned manual")

    assert result.kind == "scanned"
    assert result.page_count == 1
    assert result.chunks == ()
    assert result.processing_metadata["extractor"] == "docling"


def test_blank_pdf_is_classified_as_empty(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "blank.pdf"
    pdf = fitz.open()
    pdf.new_page()
    pdf.save(path)
    pdf.close()
    monkeypatch.setattr(pdf_processing, "process_pdf", _fake_empty_pipeline_result)

    result = pdf_processing.process_document_pdf(path, title="Blank manual")

    assert result.kind == "empty"
    assert result.page_count == 1
    assert result.chunks == ()
