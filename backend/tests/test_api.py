import asyncio

from httpx import ASGITransport, AsyncClient

from app.main import app


async def request(method: str, path: str, **kwargs: object):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def test_health_check() -> None:
    response = asyncio.run(request("GET", "/health"))

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_assessment_preview_returns_rule_based_draft() -> None:
    response = asyncio.run(
        request(
            "POST",
            "/api/v1/assessments/preview",
            json={
                "site_name": "A공장",
                "equipment_name": "컨베이어 CV-203",
                "manufacturer": "테스트 제조사",
                "model_number": "CV-203",
                "component_name": "벨트",
                "task_type": "이물질 제거",
                "energy_sources": ["전기"],
                "description": "컨베이어를 정지한 뒤 벨트에 낀 이물질을 제거합니다.",
            },
        )
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "draft"
    assert body["evidence_status"] == "not_connected"
    assert any(item["accident_type"] == "끼임" for item in body["hazards"])
    assert any(item["accident_type"] == "감전" for item in body["hazards"])
    assert body["tbm_checklist"]
