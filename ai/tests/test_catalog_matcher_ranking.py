from vision_service.catalog_matcher import _category_page_bonus


def test_bolt_shape_prefers_bolt_catalog_text_as_tie_breaker() -> None:
    category = "사진상 둥근 머리 내부 육각 소켓 나사"

    assert _category_page_bonus(category, "육각렌치볼트 및 소켓 나사 제품 목록") == 0.08
    assert _category_page_bonus(category, "산업용 카메라 통신 설정") == 0.0


def test_unknown_shape_does_not_change_catalog_ranking() -> None:
    assert _category_page_bonus("사진상 알 수 없는 부품", "볼트와 나사 제품 목록") == 0.0
