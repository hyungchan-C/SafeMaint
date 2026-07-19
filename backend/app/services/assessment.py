from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import Assessment
from app.repositories.assessment import AssessmentRepository
from app.schemas.assessment import AssessmentRequest, AssessmentResponse, EvidenceItem
from app.services.retrieval import RagRetrievalService, RetrievalService
from app.services.risk_engine import RiskEngine


DISCLAIMER = (
    "이 결과는 규칙과 검색 근거를 결합한 위험성평가 초안입니다. "
    "실제 작업 전 현장 조건과 제조사 매뉴얼을 확인하고 안전관리자의 승인을 받으세요."
)


class AssessmentService:
    def __init__(
        self,
        risk_engine: RiskEngine | None = None,
        retrieval_service: RetrievalService | None = None,
    ) -> None:
        self.risk_engine = risk_engine or RiskEngine()
        self.retrieval_service = retrieval_service or RagRetrievalService(
            settings.rag_service_url
        )

    def create_preview(self, request: AssessmentRequest) -> AssessmentResponse:
        retrieved = self.retrieval_service.search(request)
        evidence = [
            item.model_copy(update={"used_in_answer": index <= 3})
            for index, item in enumerate(retrieved, start=1)
        ]
        hazards, checklist = self.risk_engine.evaluate(
            request,
            (item for item in evidence if item.used_in_answer),
        )
        return AssessmentResponse(
            assessment_id=str(uuid4()),
            status="draft",
            created_at=datetime.now(timezone.utc),
            hazards=hazards,
            tbm_checklist=checklist,
            evidence=evidence,
            evidence_status="connected" if evidence else "not_connected",
            disclaimer=DISCLAIMER,
        )

    def create_and_save(
        self, request: AssessmentRequest, session: Session
    ) -> AssessmentResponse:
        # Retrieval and any remote model work finish before the single database
        # transaction in the repository begins.
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
        evidence = [
            EvidenceItem(
                document_id=str(link.chunk.document_id),
                chunk_id=str(link.chunk_id),
                title=link.chunk.document.title,
                page=link.chunk.page_number or link.chunk.page_start,
                page_start=link.chunk.page_start,
                page_end=link.chunk.page_end,
                section=(
                    str(link.chunk.metadata_json.get("section"))
                    if link.chunk.metadata_json.get("section")
                    else (
                        str(link.chunk.section_path[0])
                        if link.chunk.section_path
                        else None
                    )
                ),
                source_type=link.chunk.document.document_type_code,
                document_scope=link.chunk.document.document_type.scope,
                original_filename=(
                    link.chunk.document_version.original_filename
                    if link.chunk.document_version
                    else None
                ),
                document_version=(
                    link.chunk.document_version.version_number
                    if link.chunk.document_version
                    else None
                ),
                excerpt=link.chunk.content[:700],
                url=link.chunk.document.source_url,
                retrieval_rank=link.retrieval_rank,
                retrieval_score=link.retrieval_score,
                reranker_score=link.reranker_score,
                used_in_answer=link.used_in_answer,
            )
            for link in assessment.evidence_links
        ]
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
            evidence=evidence,
            evidence_status="connected" if evidence else "not_connected",
            disclaimer=DISCLAIMER,
        )
