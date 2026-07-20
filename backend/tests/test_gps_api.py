import asyncio

from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services.virtual_gps import VIRTUAL_EQUIPMENT_LOCATIONS


async def request(method: str, path: str, **kwargs: object):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def test_list_virtual_equipment_returns_mock_locations() -> None:
    response = asyncio.run(request("GET", "/api/v1/gps/equipment"))

    assert response.status_code == 200
    body = response.json()
    assert {item["equipment_code"] for item in body} == {
        location.equipment_code for location in VIRTUAL_EQUIPMENT_LOCATIONS
    }


def test_check_location_returns_checklist_for_nearby_equipment() -> None:
    conveyor = next(
        location
        for location in VIRTUAL_EQUIPMENT_LOCATIONS
        if location.equipment_code == "CONV-203"
    )

    response = asyncio.run(
        request(
            "POST",
            "/api/v1/gps/check",
            json={
                "latitude": conveyor.latitude,
                "longitude": conveyor.longitude,
                "radius_m": 5,
            },
        )
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["nearby"]) == 1
    nearby_item = body["nearby"][0]
    assert nearby_item["equipment_code"] == "CONV-203"
    assert nearby_item["checklist"]
    assert any(hazard["accident_type"] == "끼임" for hazard in nearby_item["hazards"])


def test_check_location_returns_empty_when_far_from_equipment() -> None:
    response = asyncio.run(
        request(
            "POST",
            "/api/v1/gps/check",
            json={"latitude": 0.0, "longitude": 0.0, "radius_m": 100},
        )
    )

    assert response.status_code == 200
    assert response.json()["nearby"] == []


def test_check_location_rejects_invalid_coordinates() -> None:
    response = asyncio.run(
        request(
            "POST",
            "/api/v1/gps/check",
            json={"latitude": 999.0, "longitude": 126.9780},
        )
    )

    assert response.status_code == 422
