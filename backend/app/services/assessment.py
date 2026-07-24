from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import (
    Assessment,
    ChecklistItem,
    Component,
    Document,
    DocumentChunk,
    Equipment,
    Site,
    User,
)
from app.repositories.assessment import AssessmentRepository
from app.schemas.assessment import (
    AssessmentRequest,
    AssessmentResponse,
    AssessmentSummaryResponse,
    ChatChecklistSaveRequest,
    ChecklistItemResponse,
    ChecklistItemUpdateResponse,
    EvidenceItem,
)
from app.schemas.chat import RetrievalAccessScope
from app.services.retrieval import RagRetrievalService, RetrievalService
from app.services.risk_engine import RiskEngine


DISCLAIMER = (
    "이 결과는 규칙과 검색 근거를 결합한 위험성평가 초안입니다. "
    "실제 작업 전 현장 조건과 제조사 매뉴얼을 확인하고 안전관리자의 승인을 받으세요."
)


class AssessmentReferenceError(ValueError):
    """The requested site/equipment/component hierarchy is invalid."""


class AssessmentAccessDeniedError(PermissionError):
    """The current user cannot use the requested site."""


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

    def create_preview(
        self,
        request: AssessmentRequest,
        session: Session | None,
        access_scope: RetrievalAccessScope,
    ) -> AssessmentResponse:
        normalized_request, narrowed_scope = self._normalize_request(
            request,
            session,
            access_scope,
        )
        return self._build_preview(normalized_request, narrowed_scope, session)

    def _build_preview(
        self,
        request: AssessmentRequest,
        access_scope: RetrievalAccessScope,
        session: Session | None,
    ) -> AssessmentResponse:
        retrieved = self.retrieval_service.search(request, access_scope)
        if session is not None:
            retrieved = self._filter_authorized_evidence(
                retrieved,
                session,
                access_scope,
            )
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
            checklist_items=[
                ChecklistItemResponse(
                    sequence=index,
                    content=content,
                )
                for index, content in enumerate(checklist, start=1)
            ],
            evidence=evidence,
            evidence_status="connected" if evidence else "not_connected",
            disclaimer=DISCLAIMER,
        )

    def create_and_save(
        self,
        request: AssessmentRequest,
        session: Session,
        current_user: User,
        access_scope: RetrievalAccessScope,
    ) -> AssessmentResponse:
        # Retrieval and any remote model work finish before the single database
        # transaction in the repository begins.
        normalized_request, narrowed_scope = self._normalize_request(
            request,
            session,
            access_scope,
        )
        response = self._build_preview(normalized_request, narrowed_scope, session)
        repository = AssessmentRepository(session)
        assessment = repository.create(
            normalized_request,
            response,
            created_by_user_id=current_user.id,
        )
        persisted = repository.get_by_id(str(assessment.id))
        if persisted is None:
            raise RuntimeError("저장한 위험성평가를 다시 불러오지 못했습니다.")
        return self._to_response(persisted, narrowed_scope)

    def create_from_chat_checklist(
        self,
        payload: ChatChecklistSaveRequest,
        session: Session,
        current_user: User,
    ) -> AssessmentResponse:
        """채팅 답변에 딸려 온 TBM 체크리스트를 규칙 엔진 재계산 없이 그대로 저장한다.

        채팅(Qwen)과 위험성평가(RiskEngine)는 서로 다른 파이프라인이라, 여기서
        `_build_preview()`(RiskEngine+검색)를 다시 돌리면 화면에 보이던 체크리스트와
        다른 내용이 저장될 수 있다. 그래서 화면에 보이는 항목을 입력 그대로 신뢰하고
        저장만 한다(근거/위험요인은 채팅 쪽 스키마가 달라 이번 범위에서는 비워 둠).
        """
        request = AssessmentRequest(
            site_name=payload.site_name or "AI 상담",
            equipment_name=payload.equipment_name or "AI 상담 기반 작업",
            task_type=payload.task_type or "AI 상담 기반 작업",
            description=payload.description,
        )
        response = AssessmentResponse(
            assessment_id=str(uuid4()),
            status="draft",
            created_at=datetime.now(timezone.utc),
            hazards=[],
            tbm_checklist=payload.checklist_items,
            checklist_items=[
                ChecklistItemResponse(sequence=index, content=content)
                for index, content in enumerate(payload.checklist_items, start=1)
            ],
            evidence=[],
            evidence_status="not_connected",
            disclaimer=DISCLAIMER,
        )
        repository = AssessmentRepository(session)
        assessment = repository.create(
            request,
            response,
            created_by_user_id=current_user.id,
        )
        persisted = repository.get_by_id(str(assessment.id))
        if persisted is None:
            raise RuntimeError("저장한 위험성평가를 다시 불러오지 못했습니다.")
        return self._to_response(persisted, RetrievalAccessScope())

    def get_by_id(
        self,
        assessment_id: str,
        session: Session,
        current_user: User,
        access_scope: RetrievalAccessScope,
    ) -> AssessmentResponse | None:
        assessment = AssessmentRepository(session).get_by_id(assessment_id)
        if assessment is None or not self._can_access_assessment(
            assessment,
            current_user,
            access_scope,
        ):
            return None
        return self._to_response(assessment, access_scope)

    def list_assessments(
        self,
        session: Session,
        current_user: User,
        access_scope: RetrievalAccessScope,
    ) -> list[AssessmentSummaryResponse]:
        """`access_scope.all_sites`(admin/document_manager)가 있으면 전체를,
        없으면 본인 것과 배정된 사업장 것만 요약해서 돌려준다."""

        assessments = AssessmentRepository(session).list_assessments(
            requester_user_id=current_user.id,
            all_sites=access_scope.all_sites,
            site_ids=access_scope.site_ids,
        )
        return [self._to_summary(assessment) for assessment in assessments]

    @staticmethod
    def _to_summary(assessment: Assessment) -> AssessmentSummaryResponse:
        completed = sum(1 for item in assessment.checklist_items if item.is_completed)
        return AssessmentSummaryResponse(
            assessment_id=str(assessment.id),
            status=assessment.status,
            created_at=assessment.created_at,
            created_by_user_id=assessment.created_by_user_id,
            created_by_name=(
                assessment.created_by_user.name
                if assessment.created_by_user is not None
                else None
            ),
            site_name=assessment.site_name,
            equipment_name=assessment.equipment_name,
            task_type=assessment.task_type,
            description=assessment.description,
            checklist_total=len(assessment.checklist_items),
            checklist_completed=completed,
        )

    def update_checklist_item(
        self,
        assessment_id: str,
        item_id: str,
        is_completed: bool,
        session: Session,
        current_user: User,
        access_scope: RetrievalAccessScope,
    ) -> ChecklistItemUpdateResponse | None:
        try:
            parsed_assessment_id = UUID(assessment_id)
            parsed_item_id = UUID(item_id)
        except ValueError:
            return None

        repository = AssessmentRepository(session)
        item = repository.get_checklist_item_for_update(
            parsed_assessment_id,
            parsed_item_id,
        )
        if item is None or not self._can_access_assessment(
            item.assessment,
            current_user,
            access_scope,
        ):
            session.rollback()
            return None

        item.is_completed = is_completed
        item.completed_by = None
        item.completed_by_user_id = current_user.id if is_completed else None
        item.completed_at = datetime.now(timezone.utc) if is_completed else None
        updated = repository.save_checklist_completion(
            item,
            actor_user_id=current_user.id,
        )
        return self._to_checklist_update_response(updated)

    @staticmethod
    def _can_access_site(site_id: UUID, access_scope: RetrievalAccessScope) -> bool:
        return access_scope.all_sites or str(site_id) in set(access_scope.site_ids)

    @classmethod
    def _normalize_request(
        cls,
        request: AssessmentRequest,
        session: Session | None,
        access_scope: RetrievalAccessScope,
    ) -> tuple[AssessmentRequest, RetrievalAccessScope]:
        component: Component | None = None
        equipment: Equipment | None = None
        site: Site | None = None

        has_asset_ids = any(
            value is not None
            for value in (request.site_id, request.equipment_id, request.component_id)
        )
        if has_asset_ids and session is None:
            raise AssessmentReferenceError(
                "사업장·설비·부품 ID를 검증할 DB 세션이 필요합니다."
            )

        if request.component_id is not None:
            assert session is not None
            component = session.get(Component, request.component_id)
            if component is None or not component.is_active:
                raise AssessmentReferenceError("활성 상태의 부품을 찾을 수 없습니다.")
            if (
                request.equipment_id is not None
                and component.equipment_id != request.equipment_id
            ):
                raise AssessmentReferenceError(
                    "선택한 부품이 요청한 설비에 속하지 않습니다."
                )

        resolved_equipment_id = request.equipment_id or (
            component.equipment_id if component is not None else None
        )
        if resolved_equipment_id is not None:
            assert session is not None
            equipment = session.get(Equipment, resolved_equipment_id)
            if equipment is None or not equipment.is_active:
                raise AssessmentReferenceError("활성 상태의 설비를 찾을 수 없습니다.")
            if request.site_id is not None and equipment.site_id != request.site_id:
                raise AssessmentReferenceError(
                    "선택한 설비가 요청한 사업장에 속하지 않습니다."
                )

        resolved_site_id = request.site_id or (
            equipment.site_id if equipment is not None else None
        )
        if resolved_site_id is not None:
            assert session is not None
            site = session.get(Site, resolved_site_id)
            if site is None or not site.is_active:
                raise AssessmentReferenceError("활성 상태의 사업장을 찾을 수 없습니다.")
            if not cls._can_access_site(site.id, access_scope):
                raise AssessmentAccessDeniedError(
                    "이 사업장의 위험성평가를 생성할 권한이 없습니다."
                )

        updates: dict[str, object] = {}
        if site is not None:
            updates.update(site_id=site.id, site_name=site.name)
        if equipment is not None:
            updates.update(
                equipment_id=equipment.id,
                equipment_name=equipment.name,
                manufacturer=equipment.manufacturer or request.manufacturer,
                model_number=equipment.model_number or request.model_number,
            )
        if component is not None:
            updates.update(component_id=component.id, component_name=component.name)

        normalized = request.model_copy(update=updates)
        if site is None:
            return normalized, access_scope

        narrowed_scope = access_scope.model_copy(
            update={"all_sites": False, "site_ids": [str(site.id)]}
        )
        return normalized, narrowed_scope

    @classmethod
    def _can_access_assessment(
        cls,
        assessment: Assessment,
        current_user: User,
        access_scope: RetrievalAccessScope,
    ) -> bool:
        if assessment.created_by_user_id == current_user.id:
            return True
        if access_scope.all_sites:
            return True
        return assessment.site_id is not None and cls._can_access_site(
            assessment.site_id,
            access_scope,
        )

    @staticmethod
    def _can_access_document(
        document: Document,
        access_scope: RetrievalAccessScope,
    ) -> bool:
        if document.deleted_at is not None or document.lifecycle_status != "active":
            return False
        if document.document_type.scope == "public":
            return document.access_level == "public"
        if document.document_type.scope != "company" or not access_scope.allow_company:
            return False
        if document.access_level == "private" and not access_scope.allow_private:
            return False
        if access_scope.all_sites:
            return True
        return (
            document.site_id is not None
            and str(document.site_id) in set(access_scope.site_ids)
        )

    @classmethod
    def _filter_authorized_evidence(
        cls,
        evidence_items: list[EvidenceItem],
        session: Session,
        access_scope: RetrievalAccessScope,
    ) -> list[EvidenceItem]:
        authorized: list[EvidenceItem] = []
        for evidence in evidence_items:
            try:
                chunk_id = UUID(evidence.chunk_id)
                expected_document_id = UUID(evidence.document_id)
            except ValueError:
                continue
            chunk = session.get(DocumentChunk, chunk_id)
            if chunk is None or chunk.document_id != expected_document_id:
                continue
            document = chunk.document
            if not cls._can_access_document(document, access_scope):
                continue
            if document.current_version_id is None:
                if chunk.document_version_id is not None:
                    continue
            elif chunk.document_version_id != document.current_version_id:
                continue
            authorized.append(evidence)
        return authorized

    @classmethod
    def _to_response(
        cls,
        assessment: Assessment,
        access_scope: RetrievalAccessScope,
    ) -> AssessmentResponse:
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
            if cls._can_access_document(link.chunk.document, access_scope)
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
            checklist_items=[
                ChecklistItemResponse(
                    id=item.id,
                    sequence=item.sequence,
                    content=item.content,
                    is_completed=item.is_completed,
                    completed_by_user_id=item.completed_by_user_id,
                    completed_at=item.completed_at,
                )
                for item in assessment.checklist_items
            ],
            evidence=evidence,
            evidence_status="connected" if evidence else "not_connected",
            disclaimer=DISCLAIMER,
        )

    @staticmethod
    def _to_checklist_update_response(
        item: ChecklistItem,
    ) -> ChecklistItemUpdateResponse:
        return ChecklistItemUpdateResponse(
            id=item.id,
            assessment_id=item.assessment_id,
            sequence=item.sequence,
            content=item.content,
            is_completed=item.is_completed,
            completed_by_user_id=item.completed_by_user_id,
            completed_at=item.completed_at,
        )
