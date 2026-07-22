from io import BytesIO
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile
from PIL import Image
from starlette.datastructures import Headers

from vision_service import main as vision_main
from vision_service.catalog_matcher import CatalogMatchSignals
from vision_service.main import _read_limited, _validate_image, match_catalog
from vision_service.schemas import CatalogAnalysisResponse, CatalogCandidate


def _png(width: int = 4, height: int = 4) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), "white").save(output, format="PNG")
    return output.getvalue()


def test_internal_service_rejects_oversized_upload() -> None:
    upload = UploadFile(filename="large.png", file=BytesIO(b"x" * 6))
    with pytest.raises(HTTPException) as error:
        _read_limited(upload, 5)
    assert error.value.status_code == 413


def test_internal_service_validates_image_bytes() -> None:
    _validate_image(_png())

    with pytest.raises(HTTPException) as error:
        _validate_image(b"not-an-image")
    assert error.value.status_code == 422


def test_internal_service_rejects_non_list_catalog_ids() -> None:
    upload = UploadFile(
        filename="photo.png",
        file=BytesIO(_png()),
        headers=Headers({"content-type": "image/png"}),
    )
    with pytest.raises(HTTPException) as error:
        match_catalog(upload, '{"unexpected": "value"}')
    assert error.value.status_code == 422


def test_fast_mode_skips_heavy_analyzer(monkeypatch: pytest.MonkeyPatch) -> None:
    upload = UploadFile(
        filename="photo.png",
        file=BytesIO(_png()),
        headers=Headers({"content-type": "image/png"}),
    )
    monkeypatch.setattr(
        vision_main.matcher,
        "match_with_signals",
        lambda *_args, **_kwargs: CatalogMatchSignals([], False, 0.0, 0.0),
    )
    monkeypatch.setattr(
        vision_main.analyzer,
        "analyze",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("heavy analyzer called")),
    )

    response = match_catalog(upload, "[]", "fast")

    assert response.catalog_candidates == []
    assert response.models == [vision_main.settings.embedding_model]


def _candidate(similarity: float) -> CatalogCandidate:
    return CatalogCandidate(
        catalog_id="a" * 20,
        filename="catalog.pdf",
        page=1,
        image_index=1,
        similarity=similarity,
        confidence="high",
        note="shape candidate",
    )


def test_deep_mode_skips_qwen_for_confident_siglip_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload = UploadFile(
        filename="photo.png",
        file=BytesIO(_png()),
        headers=Headers({"content-type": "image/png"}),
    )
    candidate = _candidate(0.95)
    monkeypatch.setattr(
        vision_main.matcher,
        "match_with_signals",
        lambda *_args, **_kwargs: CatalogMatchSignals([candidate], False, 0.95, 0.95),
    )
    monkeypatch.setattr(
        vision_main.analyzer,
        "analyze",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Qwen/OCR called")),
    )

    response = match_catalog(upload, '["aaaaaaaaaaaaaaaaaaaa"]', "deep")

    assert response.catalog_candidates == [candidate]
    assert response.models == [vision_main.settings.embedding_model]


def test_deep_mode_runs_ocr_only_when_siglip_detects_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload = UploadFile(
        filename="label.png",
        file=BytesIO(_png()),
        headers=Headers({"content-type": "image/png"}),
    )
    candidate = _candidate(0.95)
    monkeypatch.setattr(
        vision_main.matcher,
        "match_with_signals",
        lambda *_args, **_kwargs: CatalogMatchSignals([candidate], True, 0.95, 0.95),
    )
    calls: list[tuple[bool, bool]] = []

    def fake_analyze(*_args, include_ocr: bool, include_qwen: bool, **_kwargs):
        calls.append((include_ocr, include_qwen))
        return CatalogAnalysisResponse(
            filename="label.png",
            items=[],
            extracted_markdown="16 GB",
            models=[vision_main.settings.paddle_model],
        )

    monkeypatch.setattr(vision_main.analyzer, "analyze", fake_analyze)

    response = match_catalog(upload, '["aaaaaaaaaaaaaaaaaaaa"]', "deep")

    assert calls == [(True, False)]
    assert response.extracted_markdown == "16 GB"


def test_deep_mode_keeps_one_strong_siglip_fallback_when_qwen_rejects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload = UploadFile(
        filename="photo.png",
        file=BytesIO(_png()),
        headers=Headers({"content-type": "image/png"}),
    )
    candidate = _candidate(0.80)
    monkeypatch.setattr(
        vision_main.matcher,
        "match_with_signals",
        lambda *_args, **_kwargs: CatalogMatchSignals([candidate], False, 0.80, 0.80),
    )
    monkeypatch.setattr(vision_main.matcher, "image_path", lambda *_args: Path("candidate.jpg"))
    monkeypatch.setattr(
        vision_main.analyzer,
        "rerank_catalog_candidates",
        lambda *_args, **_kwargs: [],
    )

    response = match_catalog(upload, '["aaaaaaaaaaaaaaaaaaaa"]', "deep")

    assert len(response.catalog_candidates) == 1
    assert response.catalog_candidates[0].confidence == "낮음"
    assert "참고 후보 1개" in response.warnings[0]


def test_deep_mode_keeps_siglip_usb_category_without_catalog_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload = UploadFile(
        filename="usb.png",
        file=BytesIO(_png()),
        headers=Headers({"content-type": "image/png"}),
    )
    monkeypatch.setattr(
        vision_main.matcher,
        "match_with_signals",
        lambda *_args, **_kwargs: CatalogMatchSignals(
            [],
            True,
            0.0,
            0.0,
            "사진상 USB 플래시 메모리",
            ("USB 단자와 휴대용 저장장치 몸체가 보임",),
        ),
    )
    monkeypatch.setattr(
        vision_main.analyzer,
        "analyze",
        lambda *_args, **_kwargs: CatalogAnalysisResponse(filename="usb.png", items=[]),
    )

    response = match_catalog(upload, "[]", "deep")

    assert response.items[0].component_name == "사진상 USB 플래시 메모리"
    assert "USB 단자" in response.items[0].visible_conditions[0]


def test_internal_service_rejects_unknown_analysis_mode() -> None:
    upload = UploadFile(
        filename="photo.png",
        file=BytesIO(_png()),
        headers=Headers({"content-type": "image/png"}),
    )
    with pytest.raises(HTTPException) as error:
        match_catalog(upload, "[]", "turbo")
    assert error.value.status_code == 422
