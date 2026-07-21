from pathlib import Path

import fitz
import pytest

from ai.preprocessing import pdf_pipeline


def _save_text_pdf(path: Path, pages: int = 1) -> None:
    pdf = fitz.open()
    for _ in range(pages):
        page = pdf.new_page()
        page.insert_text((72, 72), "SafeMaint PDF pipeline test")
    pdf.save(path)
    pdf.close()


def _sections(text: str = "작업 전 전원을 차단합니다") -> list[dict]:
    return [
        {
            "header": "안전",
            "section_path": ["안전"],
            "start_page": 1,
            "end_page": 1,
            "blocks": [{"type": "text", "text": text}],
        }
    ]


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
        lambda _path, **_kwargs: pdf_pipeline.SectionExtractionResult(
            sections=[
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
            processing_metadata={
                "extractor": "docling",
                "extractor_version": "2.113.0",
                "fallback_used": False,
                "fallback_reason": None,
                "ocr_used": False,
            },
        ),
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
    assert result["processing_metadata"]["extractor"] == "docling"


def test_docling_success_does_not_call_pymupdf(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "docling-success.pdf"
    _save_text_pdf(path)
    runtime = pdf_pipeline.DoclingRuntimeSettings()
    monkeypatch.setattr(
        pdf_pipeline, "validate_docling_runtime", lambda _runtime: "2.113.0"
    )
    monkeypatch.setattr(
        pdf_pipeline,
        "extract_sections_with_docling",
        lambda _path, _runtime: (_sections(), False),
    )

    def unexpected_fallback(_path: str):
        raise AssertionError("PyMuPDF fallback must not be called")

    monkeypatch.setattr(
        pdf_pipeline, "extract_sections_with_pymupdf", unexpected_fallback
    )

    result = pdf_pipeline.extract_sections(str(path), runtime=runtime)

    assert result.sections == _sections()
    assert result.processing_metadata == {
        "extractor": "docling",
        "extractor_version": "2.113.0",
        "fallback_used": False,
        "fallback_reason": None,
        "ocr_used": False,
    }


def test_docling_conversion_failure_uses_logged_pymupdf_fallback(
    monkeypatch,
    tmp_path: Path,
    caplog,
) -> None:
    path = tmp_path / "docling-failure.pdf"
    _save_text_pdf(path)
    runtime = pdf_pipeline.DoclingRuntimeSettings()
    monkeypatch.setattr(
        pdf_pipeline, "validate_docling_runtime", lambda _runtime: "2.113.0"
    )

    def fail_docling(_path: str, _runtime):
        raise UnicodeDecodeError("utf-8", b"x", 0, 1, "invalid")

    monkeypatch.setattr(
        pdf_pipeline, "extract_sections_with_docling", fail_docling
    )
    monkeypatch.setattr(
        pdf_pipeline, "extract_sections_with_pymupdf", lambda _path: _sections()
    )
    monkeypatch.setattr(
        pdf_pipeline,
        "_installed_version",
        lambda name: "1.28.0" if name == "PyMuPDF" else "2.113.0",
    )

    with caplog.at_level("WARNING"):
        result = pdf_pipeline.extract_sections(
            str(path),
            runtime=runtime,
            log_context={
                "document_id": "doc-1",
                "document_version_id": "version-1",
            },
        )

    assert result.processing_metadata["extractor"] == "pymupdf"
    assert result.processing_metadata["fallback_used"] is True
    assert "Docling conversion failed" in result.processing_metadata["fallback_reason"]
    assert "document_id=doc-1" in caplog.text
    assert "extractor=pymupdf" in caplog.text


def test_missing_docling_is_not_swallowed_when_required(
    monkeypatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "missing-docling.pdf"
    _save_text_pdf(path)
    runtime = pdf_pipeline.DoclingRuntimeSettings(required=True)

    def missing(_runtime):
        raise pdf_pipeline.DoclingDeploymentError(
            "Docling runtime import failed: ModuleNotFoundError"
        )

    monkeypatch.setattr(pdf_pipeline, "validate_docling_runtime", missing)
    monkeypatch.setattr(
        pdf_pipeline,
        "extract_sections_with_pymupdf",
        lambda _path: pytest.fail("fallback must not run"),
    )

    with pytest.raises(pdf_pipeline.DoclingDeploymentError, match="ModuleNotFoundError"):
        pdf_pipeline.extract_sections(str(path), runtime=runtime)


def test_empty_docling_sections_use_pymupdf_fallback(
    monkeypatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "empty-docling.pdf"
    _save_text_pdf(path)
    runtime = pdf_pipeline.DoclingRuntimeSettings()
    monkeypatch.setattr(
        pdf_pipeline, "validate_docling_runtime", lambda _runtime: "2.113.0"
    )
    monkeypatch.setattr(
        pdf_pipeline,
        "extract_sections_with_docling",
        lambda _path, _runtime: ([], False),
    )
    monkeypatch.setattr(
        pdf_pipeline, "extract_sections_with_pymupdf", lambda _path: _sections()
    )
    monkeypatch.setattr(
        pdf_pipeline, "_installed_version", lambda _name: "1.28.0"
    )

    result = pdf_pipeline.extract_sections(str(path), runtime=runtime)

    assert result.sections
    assert result.processing_metadata["fallback_used"] is True
    assert result.processing_metadata["fallback_reason"] == "Docling returned no sections"


def test_pdf_file_and_page_limits_are_rejected(
    tmp_path: Path,
) -> None:
    path = tmp_path / "limits.pdf"
    _save_text_pdf(path, pages=2)

    with pytest.raises(pdf_pipeline.PdfProcessingLimitError, match="file size"):
        pdf_pipeline.extract_sections(
            str(path),
            runtime=pdf_pipeline.DoclingRuntimeSettings(
                max_file_bytes=path.stat().st_size - 1
            ),
        )

    with pytest.raises(pdf_pipeline.PdfProcessingLimitError, match="page count"):
        pdf_pipeline.extract_sections(
            str(path),
            runtime=pdf_pipeline.DoclingRuntimeSettings(
                max_file_bytes=path.stat().st_size,
                max_pages=1,
            ),
        )


def test_docling_settings_parse_false_and_thread_count(monkeypatch) -> None:
    monkeypatch.setenv("DOCLING_REQUIRED", "false")
    monkeypatch.setenv("DOCLING_ALLOW_PYMUPDF_FALLBACK", "true")
    monkeypatch.setenv("DOCLING_NUM_THREADS", "6")

    runtime = pdf_pipeline.DoclingRuntimeSettings.from_env()

    assert runtime.required is False
    assert runtime.allow_pymupdf_fallback is True
    assert runtime.num_threads == 6


def test_configured_artifacts_require_model_weights_and_config(tmp_path: Path) -> None:
    runtime = pdf_pipeline.DoclingRuntimeSettings(artifacts_path=str(tmp_path))

    with pytest.raises(pdf_pipeline.DoclingDeploymentError, match="does not contain"):
        pdf_pipeline._validate_artifacts_path(runtime)

    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    with pytest.raises(pdf_pipeline.DoclingDeploymentError, match="missing"):
        pdf_pipeline._validate_artifacts_path(runtime)

    (tmp_path / "model.safetensors").write_bytes(b"test")
    assert pdf_pipeline._validate_artifacts_path(runtime) == tmp_path


def test_missing_docling_model_is_not_silently_fallback(
    monkeypatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "missing-model.pdf"
    _save_text_pdf(path)
    runtime = pdf_pipeline.DoclingRuntimeSettings()
    monkeypatch.setattr(
        pdf_pipeline, "validate_docling_runtime", lambda _runtime: "2.113.0"
    )
    monkeypatch.setattr(
        pdf_pipeline,
        "extract_sections_with_docling",
        lambda _path, _runtime: (_ for _ in ()).throw(
            FileNotFoundError("Missing safe tensors file: model.safetensors")
        ),
    )
    monkeypatch.setattr(
        pdf_pipeline,
        "extract_sections_with_pymupdf",
        lambda _path: pytest.fail("model configuration failures must not fallback"),
    )

    with pytest.raises(pdf_pipeline.DoclingDeploymentError, match="model/configuration"):
        pdf_pipeline.extract_sections(str(path), runtime=runtime)
