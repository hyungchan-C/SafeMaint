from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models import (
    Assessment,
    AssessmentEvidence,
    AssessmentHazard,
    AuditEvent,
    ChecklistItem,
)
from app.schemas.assessment import AssessmentRequest, AssessmentResponse


class AssessmentRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self, request: AssessmentRequest, response: AssessmentResponse
    ) -> Assessment:
        assessment = Assessment(
            id=UUID(response.assessment_id),
            site_id=request.site_id,
            equipment_id=request.equipment_id,
            component_id=request.component_id,
            site_name=request.site_name,
            equipment_name=request.equipment_name,
            component_name=request.component_name,
            task_type=request.task_type,
            energy_sources=request.energy_sources,
            description=request.description,
            status=response.status.value,
            engine_version="rule-engine",
            rule_version="0.1.0",
            request_snapshot=request.model_dump(mode="json"),
            created_at=response.created_at,
        )
        assessment.hazards = [
            AssessmentHazard(
                name=hazard.name,
                accident_type=hazard.accident_type,
                likelihood=hazard.likelihood,
                severity=hazard.severity,
                score=hazard.score,
                risk_level=hazard.risk_level.value,
                safety_actions=hazard.safety_actions,
            )
            for hazard in response.hazards
        ]
        assessment.checklist_items = [
            ChecklistItem(sequence=index, content=content)
            for index, content in enumerate(response.tbm_checklist, start=1)
        ]

        self.session.add(assessment)
        self.session.add(
            AuditEvent(
                event_type="assessment.created",
                entity_type="assessment",
                entity_id=assessment.id,
                payload={
                    "status": response.status.value,
                    "engine_version": assessment.engine_version,
                    "rule_version": assessment.rule_version,
                },
            )
        )
        try:
            self.session.commit()
            self.session.refresh(assessment)
        except Exception:
            self.session.rollback()
            raise
        return assessment

    def get_by_id(self, assessment_id: str) -> Assessment | None:
        try:
            parsed_id = UUID(assessment_id)
        except ValueError:
            return None

        statement = (
            select(Assessment)
            .where(Assessment.id == parsed_id)
            .options(
                selectinload(Assessment.hazards),
                selectinload(Assessment.checklist_items),
                selectinload(Assessment.evidence_links).selectinload(
                    AssessmentEvidence.chunk
                ),
            )
        )
        return self.session.scalar(statement)
