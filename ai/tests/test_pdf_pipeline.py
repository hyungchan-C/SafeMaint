import pytest

from ai.preprocessing import pdf_pipeline


def test_docling_accelerator_uses_automatic_detection() -> None:
    assert pdf_pipeline.DOCLING_ACCELERATOR_DEVICE == "auto"


def test_long_sentence_is_bounded_by_chunk_size() -> None:
    chunks = pdf_pipeline.chunk_text("가" * 1400, chunk_size=600, overlap=100)

    assert len(chunks) == 3
    assert all(0 < len(chunk) <= 600 for chunk in chunks)


def test_long_table_row_is_bounded_by_chunk_size() -> None:
    chunks = pdf_pipeline.chunk_table(
        {"header": "| 항목 | 내용 |", "rows": [f"| 안전조치 | {'가' * 900} |"]},
        chunk_size=600,
    )

    assert len(chunks) == 2
    assert all(0 < len(chunk) <= 600 for chunk in chunks)


@pytest.mark.parametrize(
    ("chunk_size", "overlap"),
    ((0, 0), (100, -1), (100, 100)),
)
def test_invalid_chunk_options_are_rejected(
    chunk_size: int,
    overlap: int,
) -> None:
    with pytest.raises(ValueError):
        pdf_pipeline.chunk_text("안전", chunk_size=chunk_size, overlap=overlap)


def test_repeated_section_titles_create_unique_chunk_index(monkeypatch, tmp_path) -> None:
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake content for hashing only")

    monkeypatch.setattr(
        pdf_pipeline,
        "extract_sections",
        lambda _path: [
            {
                "header": "주의",
                "section_path": ["주의"],
                "start_page": 1,
                "end_page": 1,
                "blocks": [{"type": "text", "text": "첫 번째 내용"}],
            },
            {
                "header": "주의",
                "section_path": ["주의"],
                "start_page": 2,
                "end_page": 2,
                "blocks": [{"type": "text", "text": "두 번째 내용"}],
            },
        ],
    )

    result = pdf_pipeline.process_pdf(
        str(pdf_path),
        product_type="센서",
        model_name="M1",
        manufacturer="테스트 제조사",
        exclude_sections=[],
    )
    chunks = result["chunks"]

    assert result["document"]["document_type_code"] == "equipment_manual"
    assert len(chunks) == 2
    assert [c["chunk_index"] for c in chunks] == [0, 1]
    assert all(c["document_external_id"] == result["document"]["external_id"] for c in chunks)
    assert len({c["content_hash"] for c in chunks}) == 2
