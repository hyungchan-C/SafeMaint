import json

import httpx

from app.schemas.assessment import AssessmentRequest, EvidenceItem
from app.schemas.chat import RetrievalAccessScope
from app.services.assessment import AssessmentService
from app.services.retrieval import RagRetrievalService


class FakeRetrievalService:
    def search(
        self,
        request: AssessmentRequest,
        access_scope: RetrievalAccessScope,
        limit: int = 5,
    ):
        assert request.equipment_name == "Conveyor CV-203"
        assert access_scope.allow_company is True
        return [
            EvidenceItem(
                document_id="11111111-1111-1111-1111-111111111111",
                chunk_id="22222222-2222-2222-2222-222222222222",
                title="Conveyor maintenance guide",
                page=7,
                page_start=7,
                page_end=8,
                section="Bearing replacement",
                source_type="public_guide",
                document_scope="public",
                original_filename="guide.pdf",
                document_version=2,
                excerpt="Disconnect all energy before bearing replacement.",
                retrieval_rank=1,
                retrieval_score=0.82,
                reranker_score=0.88,
            )
        ][:limit]


def _request() -> AssessmentRequest:
    return AssessmentRequest(
        site_name="Site A",
        equipment_name="Conveyor CV-203",
        component_name="Bearing",
        task_type="Replacement",
        energy_sources=["electricity"],
        description="Replace the conveyor bearing after stopping the machine.",
    )


def test_assessment_uses_retrieved_evidence_without_changing_risk_formula() -> None:
    service = AssessmentService(retrieval_service=FakeRetrievalService())

    response = service.create_preview(
        _request(),
        None,
        RetrievalAccessScope(allow_company=True),
    )

    assert response.evidence_status == "connected"
    assert response.evidence[0].used_in_answer is True
    assert response.evidence[0].reranker_score == 0.88
    assert any("근거 [1]" in item for item in response.tbm_checklist)
    assert all(item.score == item.likelihood * item.severity for item in response.hazards)


def test_assessment_without_evidence_is_explicitly_labeled() -> None:
    class EmptyRetrieval:
        def search(
            self,
            request: AssessmentRequest,
            access_scope: RetrievalAccessScope,
            limit: int = 5,
        ):
            return []

    response = AssessmentService(
        retrieval_service=EmptyRetrieval()
    ).create_preview(
        _request(),
        None,
        RetrievalAccessScope(),
    )

    assert response.evidence_status == "not_connected"
    assert response.evidence == []
    assert any("문서 근거 없음" in item for item in response.tbm_checklist)


def test_rag_adapter_forwards_trusted_company_scope() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "answer": "safe",
                "sources": [],
                "retrieval_mode": "safety-fallback",
            },
        )

    scope = RetrievalAccessScope(
        requester_user_id="11111111-1111-1111-1111-111111111111",
        site_ids=["22222222-2222-2222-2222-222222222222"],
        allow_company=True,
    )
    service = RagRetrievalService(
        "http://rag.test",
        transport=httpx.MockTransport(handler),
    )

    assert service.search(_request(), scope) == []
    assert captured["access_scope"] == scope.model_dump(mode="json")


def test_rag_adapter_keeps_safe_fallback_on_transport_failure() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    service = RagRetrievalService(
        "http://rag.test",
        transport=httpx.MockTransport(handler),
    )

    assert service.search(_request(), RetrievalAccessScope()) == []
