from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.db.models import (
    Assessment,
    AssessmentEvidence,
    AssessmentHazard,
    AuditEvent,
    ChecklistItem,
    Document,
    DocumentChunk,
)
from app.schemas.assessment import AssessmentRequest, AssessmentResponse


class AssessmentRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        request: AssessmentRequest,
        response: AssessmentResponse,
        *,
        created_by_user_id: UUID,
    ) -> Assessment:
        assessment = Assessment(
            id=UUID(response.assessment_id),
            site_id=request.site_id,
            equipment_id=request.equipment_id,
            component_id=request.component_id,
            created_by_user_id=created_by_user_id,
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
        evidence_links: list[AssessmentEvidence] = []
        for evidence in response.evidence:
            try:
                chunk_id = UUID(evidence.chunk_id)
            except ValueError:
                continue
            evidence_links.append(
                AssessmentEvidence(
                    chunk_id=chunk_id,
                    retrieval_rank=evidence.retrieval_rank,
                    retrieval_score=evidence.retrieval_score,
                    reranker_score=evidence.reranker_score,
                    used_in_answer=evidence.used_in_answer,
                )
            )
        assessment.evidence_links = evidence_links

        self.session.add(assessment)
        self.session.add(
            AuditEvent(
                event_type="assessment.created",
                actor_user_id=created_by_user_id,
                entity_type="assessment",
                entity_id=assessment.id,
                payload={
                    "status": response.status.value,
                    "engine_version": assessment.engine_version,
                    "rule_version": assessment.rule_version,
                    "evidence_count": len(evidence_links),
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
                ).selectinload(DocumentChunk.document).selectinload(
                    Document.document_type
                ),
                selectinload(Assessment.evidence_links).selectinload(
                    AssessmentEvidence.chunk
                ).selectinload(DocumentChunk.document_version),
            )
        )
        return self.session.scalar(statement)

    def list_assessments(
        self,
        *,
        requester_user_id: UUID,
        all_sites: bool,
        site_ids: list[str],
    ) -> list[Assessment]:
        """`all_sites` 권한이 있으면 전체를, 없으면 본인 것 + 배정된 사업장 것만 본다."""

        statement = (
            select(Assessment)
            .options(
                selectinload(Assessment.checklist_items),
                selectinload(Assessment.created_by_user),
            )
            .order_by(Assessment.created_at.desc())
        )
        if not all_sites:
            conditions = [Assessment.created_by_user_id == requester_user_id]
            parsed_site_ids = [UUID(site_id) for site_id in site_ids]
            if parsed_site_ids:
                conditions.append(Assessment.site_id.in_(parsed_site_ids))
            statement = statement.where(or_(*conditions))
        return list(self.session.scalars(statement).all())

    def get_checklist_item_for_update(
        self,
        assessment_id: UUID,
        item_id: UUID,
    ) -> ChecklistItem | None:
        statement = (
            select(ChecklistItem)
            .join(Assessment, Assessment.id == ChecklistItem.assessment_id)
            .where(
                ChecklistItem.id == item_id,
                ChecklistItem.assessment_id == assessment_id,
            )
            .options(selectinload(ChecklistItem.assessment))
            .with_for_update(of=ChecklistItem)
        )
        return self.session.scalar(statement)

    def save_checklist_completion(
        self,
        item: ChecklistItem,
        *,
        actor_user_id: UUID,
    ) -> ChecklistItem:
        self.session.add(
            AuditEvent(
                event_type=(
                    "checklist.completed"
                    if item.is_completed
                    else "checklist.uncompleted"
                ),
                actor_user_id=actor_user_id,
                entity_type="checklist_item",
                entity_id=item.id,
                payload={
                    "assessment_id": str(item.assessment_id),
                    "checklist_item_id": str(item.id),
                    "sequence": item.sequence,
                    "is_completed": item.is_completed,
                },
            )
        )
        try:
            self.session.commit()
            self.session.refresh(item)
        except Exception:
            self.session.rollback()
            raise
        return item
