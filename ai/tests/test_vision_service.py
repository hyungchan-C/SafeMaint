from pathlib import Path

from vision_service.analyzer import (
    CatalogAnalyzer,
    _has_verified_ocr_text,
    _json_object,
    _remove_unverified_text_claims,
)
from vision_service.schemas import CatalogItem
from vision_service.config import Settings
from vision_service.catalog_matcher import CatalogImageMatcher, _feature
from PIL import Image, ImageDraw
import numpy as np
import pytest
from pypdf import PdfWriter


def test_json_object_accepts_fenced_model_output() -> None:
    payload = _json_object(
        '```json\n{"items":[{"model_number":"CV-203"}]}\n```'
    )

    assert payload["items"][0]["model_number"] == "CV-203"


def test_analyzer_can_run_with_models_disabled() -> None:
    image = Path("catalog.png")
    analyzer = CatalogAnalyzer(
        Settings(enable_paddle=False, enable_qwen=False, device="cpu")
    )

    response = analyzer.analyze(image, image.name)

    assert response.filename == "catalog.png"
    assert response.items == []
    assert response.models == []
    assert response.warnings


def test_analyzer_can_skip_document_ocr_for_field_photo(monkeypatch) -> None:
    analyzer = CatalogAnalyzer(
        Settings(enable_paddle=True, enable_qwen=False, device="cpu")
    )
    monkeypatch.setattr(
        analyzer,
        "_analyze_with_paddle",
        lambda _path: (_ for _ in ()).throw(AssertionError("document OCR called")),
    )

    response = analyzer.analyze(Path("field.jpg"), "field.jpg", include_ocr=False)

    assert response.filename == "field.jpg"


def test_image_only_layout_does_not_count_as_verified_ocr_text() -> None:
    extracted = '{"res":{"parsing_res_list":[{"block_label":"image","block_content":""}]}}'

    assert not _has_verified_ocr_text(extracted)


def test_unverified_model_and_marking_claims_are_removed() -> None:
    items = [CatalogItem(
        model_number="M10",
        equipment_type="육각머리 볼트",
        specifications={"grade": "4.8"},
        visible_conditions=["머리에 M10 x 1.0 문자가 있음", "육각형 머리가 보임"],
    )]

    sanitized = _remove_unverified_text_claims(items)

    assert sanitized[0].model_number is None
    assert sanitized[0].specifications == {}
    assert sanitized[0].visible_conditions == ["육각형 머리가 보임"]


def test_offline_catalog_feature_is_stable_across_white_margins() -> None:
    small = Image.new("RGB", (100, 100), "white")
    ImageDraw.Draw(small).rectangle((25, 20, 75, 80), fill="black")
    large = Image.new("RGB", (300, 300), "white")
    ImageDraw.Draw(large).rectangle((100, 90, 200, 210), fill="black")

    similarity = float(np.dot(_feature(small), _feature(large)))

    assert similarity > 0.98


def test_small_part_search_uses_overlapping_detail_views() -> None:
    image = Image.new("RGB", (1000, 800), "black")

    views = CatalogImageMatcher._query_views(image)

    assert len(views) == 7
    assert views[0].size == (1000, 800)
    assert len({view.size for view in views[1:]}) == 1
    assert views[1].width < image.width
    assert views[1].height < image.height


def test_pdf_page_index_uses_full_page_and_overlapping_regions() -> None:
    image = Image.new("RGB", (1200, 1600), "white")

    views = CatalogImageMatcher._page_views(image)

    assert len(views) == 5
    assert views[0] == (image, None)
    assert all(box is not None for _, box in views[1:])
    assert all(view.width > image.width // 2 for view, _ in views[1:])
    assert all(view.height > image.height // 2 for view, _ in views[1:])


def test_pdf_index_renders_three_pages_for_vector_search(tmp_path, monkeypatch) -> None:
    pdf_path = tmp_path / "three-pages.pdf"
    writer = PdfWriter()
    for _ in range(3):
        writer.add_blank_page(width=600, height=800)
    with pdf_path.open("wb") as output:
        writer.write(output)

    matcher = CatalogImageMatcher(str(tmp_path / "index"), 0.5)
    monkeypatch.setattr(
        matcher.embedder,
        "encode_many",
        lambda images: np.ones((len(images), 4), dtype=np.float32) / 2,
    )
    monkeypatch.setattr(
        matcher.embedder,
        "encode",
        lambda _image: np.ones(4, dtype=np.float32) / 2,
    )

    summary = matcher.index_pdf(pdf_path, pdf_path.name)

    assert summary.page_count == 3
    assert summary.image_count == 15
    assert summary.index_version.startswith("safemaint-page-matrix-v5:")
    assert matcher.resolve_image(summary.catalog_id, 2, 1).name == "page-0002.jpg"


def test_unlimited_pdf_index_does_not_truncate_entries(
    tmp_path,
    monkeypatch,
) -> None:
    pdf_path = tmp_path / "unlimited-pages.pdf"
    writer = PdfWriter()
    for _ in range(3):
        writer.add_blank_page(width=600, height=800)
    with pdf_path.open("wb") as output:
        writer.write(output)

    matcher = CatalogImageMatcher(
        str(tmp_path / "unlimited-index"),
        0.5,
        max_pages=None,
        max_images=None,
    )
    monkeypatch.setattr(
        matcher.embedder,
        "encode_many",
        lambda images: np.ones((len(images), 4), dtype=np.float32) / 2,
    )
    monkeypatch.setattr(
        matcher.embedder,
        "encode",
        lambda _image: np.ones(4, dtype=np.float32) / 2,
    )
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda _path: (_ for _ in ()).throw(
            AssertionError("PDF hashing must not use read_bytes")
        ),
    )

    summary = matcher.index_pdf(pdf_path, pdf_path.name)

    assert summary.page_count == 3
    assert summary.image_count == 15


def test_positive_catalog_image_limit_is_preserved(tmp_path, monkeypatch) -> None:
    pdf_path = tmp_path / "limited-images.pdf"
    writer = PdfWriter()
    for _ in range(3):
        writer.add_blank_page(width=600, height=800)
    with pdf_path.open("wb") as output:
        writer.write(output)

    matcher = CatalogImageMatcher(
        str(tmp_path / "limited-index"),
        0.5,
        max_images=6,
    )
    monkeypatch.setattr(
        matcher.embedder,
        "encode_many",
        lambda images: np.ones((len(images), 4), dtype=np.float32) / 2,
    )
    monkeypatch.setattr(
        matcher.embedder,
        "encode",
        lambda _image: np.ones(4, dtype=np.float32) / 2,
    )

    summary = matcher.index_pdf(pdf_path, pdf_path.name)

    assert summary.image_count == 6
    assert any("최대 6개" in warning for warning in summary.warnings)


def test_positive_pdf_page_limit_is_preserved(tmp_path) -> None:
    pdf_path = tmp_path / "limited-pages.pdf"
    writer = PdfWriter()
    for _ in range(3):
        writer.add_blank_page(width=600, height=800)
    with pdf_path.open("wb") as output:
        writer.write(output)

    matcher = CatalogImageMatcher(
        str(tmp_path / "page-limit-index"),
        0.5,
        max_pages=2,
    )

    with pytest.raises(ValueError, match="2개"):
        matcher.index_pdf(pdf_path, pdf_path.name)


def test_analyzer_warmup_preloads_qwen_when_enabled(monkeypatch) -> None:
    analyzer = CatalogAnalyzer(Settings(enable_qwen=True))
    calls: list[str] = []
    monkeypatch.setattr(analyzer, "_load_qwen", lambda: calls.append("qwen"))

    analyzer.warmup()

    assert calls == ["qwen"]


def test_catalog_matcher_warmup_initializes_image_and_text_paths(tmp_path, monkeypatch) -> None:
    matcher = CatalogImageMatcher(str(tmp_path), 0.5)
    calls: list[str] = []
    monkeypatch.setattr(matcher.embedder, "encode_many", lambda _images: np.ones((1, 4)))
    monkeypatch.setattr(matcher.embedder, "classify", lambda _vectors: calls.append("classify"))
    monkeypatch.setattr(matcher.embedder, "has_visible_text", lambda _vectors: calls.append("text"))

    matcher.warmup()

    assert calls == ["classify", "text"]
