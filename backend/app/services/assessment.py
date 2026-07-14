from datetime import datetime, timezone
from uuid import uuid4

from app.schemas.assessment import AssessmentRequest, AssessmentResponse
from app.services.retrieval import NotConfiguredRetrievalService, RetrievalService
from app.services.risk_engine import RiskEngine


class AssessmentService:
    def __init__(
        self,
        risk_engine: RiskEngine | None = None,
        retrieval_service: RetrievalService | None = None,
    ) -> None:
        self.risk_engine = risk_engine or RiskEngine()
        self.retrieval_service = retrieval_service or NotConfiguredRetrievalService()

    async def create_preview(self, request: AssessmentRequest) -> AssessmentResponse:
        hazards, checklist = self.risk_engine.evaluate(request)
        evidence = await self.retrieval_service.search(request)

        return AssessmentResponse(
            assessment_id=str(uuid4()),
            status="draft",
            created_at=datetime.now(timezone.utc),
            hazards=hazards,
            tbm_checklist=checklist,
            evidence=evidence,
            evidence_status="connected" if evidence else "not_connected",
            disclaimer=(
                "이 결과는 초기 규칙 기반 위험성평가 초안입니다. 실제 작업 전 "
                "사업장 규정과 현장 상태를 확인하고 안전관리자의 검토를 받아야 합니다."
            ),
        )
