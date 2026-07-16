import asyncio
from unittest.mock import patch

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


def test_ai_chat_returns_model_answer() -> None:
    with patch("app.api.routes.ai.AIService.answer", return_value="전원을 차단하고 LOTO를 적용하세요."):
        response = asyncio.run(
            request(
                "POST",
                "/api/v1/ai/chat",
                json={"question": "센서를 교체해도 될까요?", "context": "에너지원: 전기"},
            )
        )

    assert response.status_code == 200
    assert response.json() == {
        "answer": "전원을 차단하고 LOTO를 적용하세요.",
        "model": "gpt-4o-mini",
    }
