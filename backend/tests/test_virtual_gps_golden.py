"""GPS 근접 판정 평가 리포트(reports/SafeMaint_평가_리포트.md §8-1)의 골든 케이스.

기존 test_virtual_gps.py는 로직 회귀 검증용이고, 이 파일은 평가 리포트의
근접 판정 정확도 지표에 실제 수치 근거를 제공하기 위한 골든셋이다.
반경 경계값은 실측 거리(SAMPLE_EQUIPMENT_LOCATIONS 간 haversine 거리)를 기준으로
정확히 계산했다: CONV-203↔PNL-01 = CONV-203↔WLD-05 ≈ 28.38m, CONV-203↔CONV-101 ≈ 427m.
"""

from app.services.virtual_gps import SAMPLE_EQUIPMENT_LOCATIONS, find_nearby_equipment

_BY_CODE = {loc.equipment_code: loc for loc in SAMPLE_EQUIPMENT_LOCATIONS}


def _nearby_codes(lat: float, lon: float, radius_m: float) -> set[str]:
    return {
        entry.location.equipment_code
        for entry in find_nearby_equipment(lat, lon, SAMPLE_EQUIPMENT_LOCATIONS, radius_m)
    }


def test_boundary_just_inside_isolates_conveyor() -> None:
    # CONV-203 위치, 반경 27m: PNL-01/WLD-05(각 28.38m)는 반경 밖이라 제외돼야 한다.
    conv = _BY_CODE["CONV-203"]
    assert _nearby_codes(conv.latitude, conv.longitude, radius_m=27) == {"CONV-203"}


def test_boundary_just_outside_includes_cluster() -> None:
    # 같은 위치, 반경 29m: PNL-01/WLD-05(28.38m)가 반경 안으로 들어와 함께 감지돼야 한다.
    conv = _BY_CODE["CONV-203"]
    assert _nearby_codes(conv.latitude, conv.longitude, radius_m=29) == {
        "CONV-203",
        "PNL-01",
        "WLD-05",
    }


def test_default_radius_at_isolated_equipment_matches_only_itself() -> None:
    # CONV-101은 나머지 설비와 400m 이상 떨어져 있어 기본 반경(30m)에서는 단독 감지된다.
    conv101 = _BY_CODE["CONV-101"]
    assert _nearby_codes(conv101.latitude, conv101.longitude, radius_m=30) == {"CONV-101"}


def test_far_away_position_shows_no_equipment() -> None:
    # 반경 밖 = 체크리스트 게이팅: 근처 설비가 없으면 빈 목록이어야 한다.
    assert _nearby_codes(0.0, 0.0, radius_m=30) == set()
