from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

from app.db.models import Assessment
from app.repositories.assessment import AssessmentRepository
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

    def create_preview(self, request: AssessmentRequest) -> AssessmentResponse:
        hazards, checklist = self.risk_engine.evaluate(request)
        evidence = self.retrieval_service.search(request)

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

    def create_and_save(
        self, request: AssessmentRequest, session: Session
    ) -> AssessmentResponse:
        response = self.create_preview(request)
        AssessmentRepository(session).create(request, response)
        return response

    def get_by_id(self, assessment_id: str, session: Session) -> AssessmentResponse | None:
        assessment = AssessmentRepository(session).get_by_id(assessment_id)
        if assessment is None:
            return None
        return self._to_response(assessment)

    @staticmethod
    def _to_response(assessment: Assessment) -> AssessmentResponse:
        return AssessmentResponse(
            assessment_id=str(assessment.id),
            status=assessment.status,
            created_at=assessment.created_at,
            hazards=[
                {
                    "name": hazard.name,
                    "accident_type": hazard.accident_type,
                    "likelihood": hazard.likelihood,
                    "severity": hazard.severity,
                    "score": hazard.score,
                    "risk_level": hazard.risk_level,
                    "safety_actions": hazard.safety_actions,
                }
                for hazard in assessment.hazards
            ],
            tbm_checklist=[item.content for item in assessment.checklist_items],
            evidence=[],
            evidence_status="connected" if assessment.evidence_links else "not_connected",
            disclaimer=(
                "이 결과는 초기 규칙 기반 위험성평가 초안입니다. 실제 작업 전 "
                "사업장 규정과 현장 상태를 확인하고 안전관리자의 검토를 받아야 합니다."
            ),
        )
