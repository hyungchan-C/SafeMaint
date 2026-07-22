import asyncio
from unittest.mock import patch
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from app.api.deps import get_current_user, get_retrieval_access_scope
from app.api.routes.assessments import get_assessment_service
from app.db.models import User
from app.main import app
from app.schemas.assessment import AssessmentRequest
from app.schemas.chat import RetrievalAccessScope
from app.services.assessment import AssessmentService


async def request(method: str, path: str, **kwargs: object):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def test_health_check() -> None:
    response = asyncio.run(request("GET", "/health"))

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def _assessment_payload() -> dict:
    return {
        "site_name": "A공장",
        "equipment_name": "컨베이어 CV-203",
        "manufacturer": "테스트 제조사",
        "model_number": "CV-203",
        "component_name": "벨트",
        "task_type": "이물질 제거",
        "energy_sources": ["전기"],
        "description": "컨베이어를 정지한 뒤 벨트에 낀 이물질을 제거합니다.",
    }


def test_assessment_preview_requires_authentication() -> None:
    response = asyncio.run(
        request(
            "POST",
            "/api/v1/assessments/preview",
            json=_assessment_payload(),
        )
    )

    assert response.status_code == 401


def test_assessment_create_and_get_require_authentication() -> None:
    created = asyncio.run(
        request("POST", "/api/v1/assessments", json=_assessment_payload())
    )
    loaded = asyncio.run(
        request(
            "GET",
            "/api/v1/assessments/11111111-1111-1111-1111-111111111111",
        )
    )
    checklist_update = asyncio.run(
        request(
            "PATCH",
            "/api/v1/assessments/11111111-1111-1111-1111-111111111111/"
            "checklist-items/22222222-2222-2222-2222-222222222222",
            json={"is_completed": True},
        )
    )

    assert created.status_code == 401
    assert loaded.status_code == 401
    assert checklist_update.status_code == 401


def test_authenticated_assessment_preview_returns_rule_based_draft() -> None:
    captured_scopes: list[RetrievalAccessScope] = []

    class EmptyRetrieval:
        def search(
            self,
            _request: AssessmentRequest,
            _scope: RetrievalAccessScope,
            limit: int = 5,
        ):
            captured_scopes.append(_scope)
            return []

    user = User(
        id=uuid4(),
        employee_number="EMP-01",
        name="평가 사용자",
        auth_provider="oidc",
        status="active",
    )
    scope = RetrievalAccessScope(
        requester_user_id=user.id,
        allow_company=True,
    )
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_retrieval_access_scope] = lambda: scope
    app.dependency_overrides[get_assessment_service] = lambda: AssessmentService(
        retrieval_service=EmptyRetrieval()
    )
    try:
        response = asyncio.run(
            request(
                "POST",
                "/api/v1/assessments/preview",
                json={
                    **_assessment_payload(),
                    "access_scope": {"allow_company": False, "all_sites": True},
                },
            )
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "draft"
    assert body["evidence_status"] == "not_connected"
    assert captured_scopes == [scope]
    assert captured_scopes[0].all_sites is False
    assert captured_scopes[0].allow_company is True
    assert any(item["accident_type"] == "끼임" for item in body["hazards"])
    assert any(item["accident_type"] == "감전" for item in body["hazards"])
    assert body["tbm_checklist"]
    assert len(body["checklist_items"]) == len(body["tbm_checklist"])
    assert [item["sequence"] for item in body["checklist_items"]] == list(
        range(1, len(body["checklist_items"]) + 1)
    )
    assert all(item["id"] is None for item in body["checklist_items"])
    assert all(item["is_completed"] is False for item in body["checklist_items"])


def test_legacy_direct_ai_route_is_not_exposed() -> None:
    with patch("app.api.routes.ai.AIService.answer", return_value="전원을 차단하고 LOTO를 적용하세요."):
        response = asyncio.run(
            request(
                "POST",
                "/api/v1/ai/chat",
                json={"question": "센서를 교체해도 될까요?", "context": "에너지원: 전기"},
            )
        )

    assert response.status_code == 404
