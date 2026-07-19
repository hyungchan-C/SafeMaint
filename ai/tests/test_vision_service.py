from pathlib import Path

from vision_service.analyzer import (
    CatalogAnalyzer,
    _has_verified_ocr_text,
    _json_object,
    _remove_unverified_text_claims,
)
from vision_service.schemas import CatalogItem
from vision_service.config import Settings
from vision_service.catalog_matcher import _feature
from PIL import Image, ImageDraw
import numpy as np


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
