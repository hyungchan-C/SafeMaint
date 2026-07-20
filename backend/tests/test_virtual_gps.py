from app.services.virtual_gps import (
    VIRTUAL_EQUIPMENT_LOCATIONS,
    build_checklist,
    find_nearby_equipment,
    haversine_distance_m,
    resolve_locations,
)


def test_haversine_distance_is_zero_for_same_point() -> None:
    assert haversine_distance_m(37.5665, 126.9780, 37.5665, 126.9780) == 0


def test_find_nearby_equipment_matches_equipment_within_radius() -> None:
    conveyor = next(
        location
        for location in VIRTUAL_EQUIPMENT_LOCATIONS
        if location.equipment_code == "CONV-203"
    )

    nearby = find_nearby_equipment(conveyor.latitude, conveyor.longitude, radius_m=5)

    assert [entry.location.equipment_code for entry in nearby] == ["CONV-203"]
    assert nearby[0].distance_m == 0


def test_find_nearby_equipment_returns_nothing_far_away() -> None:
    nearby = find_nearby_equipment(0.0, 0.0, radius_m=100)

    assert nearby == []


def test_find_nearby_equipment_sorts_by_distance() -> None:
    panel = next(
        location
        for location in VIRTUAL_EQUIPMENT_LOCATIONS
        if location.equipment_code == "PNL-01"
    )

    nearby = find_nearby_equipment(panel.latitude, panel.longitude, radius_m=1000)

    distances = [entry.distance_m for entry in nearby]
    assert distances == sorted(distances)
    assert nearby[0].location.equipment_code == "PNL-01"


def test_build_checklist_matches_conveyor_hazard() -> None:
    conveyor = next(
        location
        for location in VIRTUAL_EQUIPMENT_LOCATIONS
        if location.equipment_code == "CONV-203"
    )

    hazards, checklist = build_checklist(conveyor)

    assert any(hazard.accident_type == "끼임" for hazard in hazards)
    assert checklist


def test_build_checklist_matches_electrical_hazard() -> None:
    panel = next(
        location
        for location in VIRTUAL_EQUIPMENT_LOCATIONS
        if location.equipment_code == "PNL-01"
    )

    hazards, _checklist = build_checklist(panel)

    assert any(hazard.accident_type == "감전" for hazard in hazards)


def test_resolve_locations_without_origin_returns_original_coordinates() -> None:
    assert resolve_locations(None) == VIRTUAL_EQUIPMENT_LOCATIONS


def test_resolve_locations_shifts_whole_layout_to_origin() -> None:
    origin = (0.0, 0.0)

    shifted = resolve_locations(origin)

    conveyor = next(loc for loc in shifted if loc.equipment_code == "CONV-203")
    assert abs(conveyor.latitude - origin[0]) < 1e-9
    assert abs(conveyor.longitude - origin[1]) < 1e-9


def test_resolve_locations_preserves_relative_layout() -> None:
    def distance_between(locations, code_a: str, code_b: str) -> float:
        location_a = next(loc for loc in locations if loc.equipment_code == code_a)
        location_b = next(loc for loc in locations if loc.equipment_code == code_b)
        return haversine_distance_m(
            location_a.latitude, location_a.longitude, location_b.latitude, location_b.longitude
        )

    original_distance = distance_between(VIRTUAL_EQUIPMENT_LOCATIONS, "CONV-203", "PNL-01")
    shifted_distance = distance_between(resolve_locations((0.0, 0.0)), "CONV-203", "PNL-01")

    assert abs(original_distance - shifted_distance) < 0.01


def test_find_nearby_equipment_matches_calibrated_position() -> None:
    origin = (0.0, 0.0)

    nearby = find_nearby_equipment(0.0, 0.0, radius_m=5, origin=origin)

    assert [entry.location.equipment_code for entry in nearby] == ["CONV-203"]
    assert nearby[0].distance_m < 1
