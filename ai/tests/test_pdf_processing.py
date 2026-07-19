from pathlib import Path

import fitz

from rag_service.pdf_processing import process_document_pdf


def _save_text_pdf(path: Path) -> None:
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Disconnect power before conveyor maintenance.")
    pdf.save(path)
    pdf.close()


def test_text_pdf_keeps_normalized_metadata_and_stable_ids(tmp_path: Path) -> None:
    path = tmp_path / "manual.pdf"
    _save_text_pdf(path)

    first = process_document_pdf(
        path,
        title="Conveyor manual",
        manufacturer="SafeMaint",
        product_type="conveyor",
        model_name="CV-203",
    )
    second = process_document_pdf(
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


def test_image_only_pdf_is_classified_as_scanned(tmp_path: Path) -> None:
    path = tmp_path / "scan.pdf"
    pdf = fitz.open()
    page = pdf.new_page()
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 20), False)
    pixmap.clear_with(255)
    page.insert_image(page.rect, pixmap=pixmap)
    pdf.save(path)
    pdf.close()

    result = process_document_pdf(path, title="Scanned manual")

    assert result.kind == "scanned"
    assert result.page_count == 1
    assert result.chunks == ()


def test_blank_pdf_is_classified_as_empty(tmp_path: Path) -> None:
    path = tmp_path / "blank.pdf"
    pdf = fitz.open()
    pdf.new_page()
    pdf.save(path)
    pdf.close()

    result = process_document_pdf(path, title="Blank manual")

    assert result.kind == "empty"
    assert result.page_count == 1
    assert result.chunks == ()
