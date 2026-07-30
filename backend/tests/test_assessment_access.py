from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.db.models import (
    Assessment,
    AuditEvent,
    ChecklistItem,
    Component,
    Document,
    DocumentChunk,
    DocumentType,
    Equipment,
    Site,
    User,
)
from app.repositories.assessment import AssessmentRepository
from app.schemas.assessment import AssessmentRequest, AssessmentResponse, EvidenceItem
from app.schemas.chat import RetrievalAccessScope
from app.services.assessment import (
    AssessmentAccessDeniedError,
    AssessmentReferenceError,
    AssessmentService,
)


def _request(**updates) -> AssessmentRequest:
    values = {
        "site_name": "client site",
        "equipment_name": "client equipment",
        "component_name": "client component",
        "task_type": "replacement",
        "description": "Replace the component after complete isolation.",
    }
    values.update(updates)
    return AssessmentRequest(**values)


class ObjectSession:
    def __init__(self, *objects) -> None:
        self.objects = {(type(item), item.id): item for item in objects}

    def get(self, model, object_id):
        return self.objects.get((model, object_id))


class CapturingRetrieval:
    def __init__(self) -> None:
        self.request = None
        self.scope = None

    def search(self, request, access_scope, limit: int = 5):
        self.request = request
        self.scope = access_scope
        return []


def _asset_tree():
    site = Site(id=uuid4(), code="SITE-A", name="DB Site", is_active=True)
    equipment = Equipment(
        id=uuid4(),
        site_id=site.id,
        code="EQ-1",
        name="DB Equipment",
        equipment_type="conveyor",
        manufacturer="DB Manufacturer",
        model_number="DB-100",
        is_active=True,
    )
    component = Component(
        id=uuid4(),
        equipment_id=equipment.id,
        code="CP-1",
        name="DB Component",
        component_type="bearing",
        is_active=True,
    )
    return site, equipment, component


def test_preview_normalizes_assets_and_narrows_rag_scope_to_requested_site() -> None:
    site, equipment, component = _asset_tree()
    retrieval = CapturingRetrieval()
    service = AssessmentService(retrieval_service=retrieval)
    broad_scope = RetrievalAccessScope(
        requester_user_id=uuid4(),
        site_ids=[str(site.id), str(uuid4())],
        allow_company=True,
    )

    service.create_preview(
        _request(
            site_id=site.id,
            equipment_id=equipment.id,
            component_id=component.id,
        ),
        ObjectSession(site, equipment, component),  # type: ignore[arg-type]
        broad_scope,
    )

    assert retrieval.request.site_name == site.name
    assert retrieval.request.equipment_name == equipment.name
    assert retrieval.request.component_name == component.name
    assert retrieval.request.manufacturer == equipment.manufacturer
    assert retrieval.request.model_number == equipment.model_number
    assert retrieval.scope.allow_company is True
    assert retrieval.scope.all_sites is False
    assert retrieval.scope.site_ids == [str(site.id)]


def test_preview_rejects_site_outside_user_assignments() -> None:
    site, equipment, component = _asset_tree()
    service = AssessmentService(retrieval_service=CapturingRetrieval())

    with pytest.raises(AssessmentAccessDeniedError):
        service.create_preview(
            _request(
                site_id=site.id,
                equipment_id=equipment.id,
                component_id=component.id,
            ),
            ObjectSession(site, equipment, component),  # type: ignore[arg-type]
            RetrievalAccessScope(site_ids=[str(uuid4())], allow_company=True),
        )


def test_preview_rejects_equipment_site_mismatch() -> None:
    site, equipment, component = _asset_tree()
    other_site = Site(
        id=uuid4(), code="SITE-B", name="Other Site", is_active=True
    )
    service = AssessmentService(retrieval_service=CapturingRetrieval())

    with pytest.raises(AssessmentReferenceError, match="설비"):
        service.create_preview(
            _request(site_id=other_site.id, equipment_id=equipment.id),
            ObjectSession(site, other_site, equipment, component),  # type: ignore[arg-type]
            RetrievalAccessScope(all_sites=True, allow_company=True),
        )


def test_preview_rejects_component_equipment_mismatch() -> None:
    site, equipment, component = _asset_tree()
    other_equipment = Equipment(
        id=uuid4(),
        site_id=site.id,
        code="EQ-2",
        name="Other Equipment",
        equipment_type="conveyor",
        is_active=True,
    )
    service = AssessmentService(retrieval_service=CapturingRetrieval())

    with pytest.raises(AssessmentReferenceError, match="부품"):
        service.create_preview(
            _request(
                site_id=site.id,
                equipment_id=other_equipment.id,
                component_id=component.id,
            ),
            ObjectSession(site, equipment, other_equipment, component),  # type: ignore[arg-type]
            RetrievalAccessScope(all_sites=True, allow_company=True),
        )


def test_assessment_access_allows_owner_same_site_and_global_role_only() -> None:
    owner = User(id=uuid4(), employee_number="OWNER", name="Owner", auth_provider="oidc")
    colleague = User(
        id=uuid4(), employee_number="COLLEAGUE", name="Colleague", auth_provider="oidc"
    )
    site_id = uuid4()
    assessment = Assessment(
        id=uuid4(),
        created_by_user_id=owner.id,
        site_id=site_id,
        site_name="Site",
        equipment_name="Equipment",
        task_type="work",
        energy_sources=[],
        description="description",
        engine_version="test",
        rule_version="test",
        request_snapshot={},
    )

    assert AssessmentService._can_access_assessment(
        assessment, owner, RetrievalAccessScope()
    )
    assert AssessmentService._can_access_assessment(
        assessment,
        colleague,
        RetrievalAccessScope(site_ids=[str(site_id)]),
    )
    assert AssessmentService._can_access_assessment(
        assessment,
        colleague,
        RetrievalAccessScope(all_sites=True),
    )
    assert not AssessmentService._can_access_assessment(
        assessment,
        colleague,
        RetrievalAccessScope(site_ids=[str(uuid4())]),
    )


def test_company_evidence_is_hidden_outside_current_site_scope() -> None:
    document_type = DocumentType(
        code="equipment_manual",
        name="Equipment manual",
        scope="company",
        is_active=True,
    )
    site_id = uuid4()
    document = Document(
        id=uuid4(),
        external_id=f"manual:{uuid4()}",
        title="Manual",
        source_type="manual",
        document_type_code=document_type.code,
        document_type=document_type,
        lifecycle_status="active",
        access_level="restricted",
        site_id=site_id,
    )

    assert AssessmentService._can_access_document(
        document,
        RetrievalAccessScope(
            site_ids=[str(site_id)],
            allow_company=True,
        ),
    )
    assert not AssessmentService._can_access_document(
        document,
        RetrievalAccessScope(
            site_ids=[str(uuid4())],
            allow_company=True,
        ),
    )


def test_cross_site_rag_evidence_is_filtered_before_response_or_persistence() -> None:
    document_type = DocumentType(
        code="equipment_manual",
        name="Equipment manual",
        scope="company",
        is_active=True,
    )
    document = Document(
        id=uuid4(),
        external_id=f"manual:{uuid4()}",
        title="Other-site manual",
        source_type="manual",
        document_type_code=document_type.code,
        document_type=document_type,
        lifecycle_status="active",
        access_level="restricted",
        site_id=uuid4(),
    )
    chunk = DocumentChunk(
        id=uuid4(),
        document_id=document.id,
        document=document,
        chunk_index=0,
        content="other site content",
        content_hash="a" * 64,
        embedding_status="ready",
    )

    class CrossSiteRetrieval:
        def search(self, _request, _scope, limit: int = 5):
            return [
                EvidenceItem(
                    document_id=str(document.id),
                    chunk_id=str(chunk.id),
                    title=document.title,
                    source_type="equipment_manual",
                    document_scope="company",
                    excerpt=chunk.content,
                    retrieval_rank=1,
                )
            ]

    response = AssessmentService(
        retrieval_service=CrossSiteRetrieval()
    ).create_preview(
        _request(),
        ObjectSession(chunk),  # type: ignore[arg-type]
        RetrievalAccessScope(
            site_ids=[str(uuid4())],
            allow_company=True,
        ),
    )

    assert response.evidence == []
    assert response.evidence_status == "not_connected"


def test_repository_sets_creator_and_audit_actor() -> None:
    class RecordingSession:
        def __init__(self) -> None:
            self.added = []

        def add(self, item) -> None:
            self.added.append(item)

        def commit(self) -> None:
            pass

        def refresh(self, _item) -> None:
            pass

        def rollback(self) -> None:
            pass

    user_id = uuid4()
    response = AssessmentResponse(
        assessment_id=str(uuid4()),
        status="draft",
        created_at=datetime.now(timezone.utc),
        hazards=[],
        tbm_checklist=[],
        evidence=[],
        evidence_status="not_connected",
        disclaimer="test",
    )
    session = RecordingSession()

    assessment = AssessmentRepository(session).create(  # type: ignore[arg-type]
        _request(),
        response,
        created_by_user_id=user_id,
    )

    audit = next(item for item in session.added if isinstance(item, AuditEvent))
    assert assessment.created_by_user_id == user_id
    assert audit.actor_user_id == user_id


def test_checklist_completion_uses_server_actor_and_clears_completion_metadata() -> None:
    owner = User(id=uuid4(), employee_number="OWNER-2", name="Owner", auth_provider="oidc")
    colleague = User(
        id=uuid4(), employee_number="COLLEAGUE-2", name="Colleague", auth_provider="oidc"
    )
    site_id = uuid4()
    assessment = Assessment(
        id=uuid4(),
        created_by_user_id=owner.id,
        site_id=site_id,
        site_name="Site",
        equipment_name="Equipment",
        task_type="work",
        energy_sources=[],
        description="description",
        engine_version="test",
        rule_version="test",
        request_snapshot={},
    )
    item = ChecklistItem(
        id=uuid4(),
        assessment_id=assessment.id,
        assessment=assessment,
        sequence=1,
        content="LOTO",
        is_completed=False,
    )

    class FakeRepository:
        def get_checklist_item_for_update(self, assessment_id, item_id):
            assert assessment_id == assessment.id
            assert item_id == item.id
            return item

        def save_checklist_completion(self, value, *, actor_user_id):
            assert actor_user_id == colleague.id
            return value

    class FakeSession:
        def rollback(self):
            raise AssertionError("authorized update must not roll back")

    service = AssessmentService(retrieval_service=CapturingRetrieval())
    scope = RetrievalAccessScope(site_ids=[str(site_id)])
    with patch(
        "app.services.assessment.AssessmentRepository",
        return_value=FakeRepository(),
    ):
        completed = service.update_checklist_item(
            str(assessment.id),
            str(item.id),
            True,
            FakeSession(),  # type: ignore[arg-type]
            colleague,
            scope,
        )
        assert completed is not None
        assert completed.is_completed is True
        assert completed.completed_by_user_id == colleague.id
        assert completed.completed_at is not None

        uncompleted = service.update_checklist_item(
            str(assessment.id),
            str(item.id),
            False,
            FakeSession(),  # type: ignore[arg-type]
            colleague,
            scope,
        )
        assert uncompleted is not None
        assert uncompleted.is_completed is False
        assert uncompleted.completed_by_user_id is None
        assert uncompleted.completed_at is None


def test_checklist_update_hides_cross_site_item_as_not_found() -> None:
    owner = User(id=uuid4(), employee_number="OWNER-3", name="Owner", auth_provider="oidc")
    outsider = User(id=uuid4(), employee_number="OUT-3", name="Out", auth_provider="oidc")
    assessment = Assessment(
        id=uuid4(),
        created_by_user_id=owner.id,
        site_id=uuid4(),
        site_name="Site",
        equipment_name="Equipment",
        task_type="work",
        energy_sources=[],
        description="description",
        engine_version="test",
        rule_version="test",
        request_snapshot={},
    )
    item = ChecklistItem(
        id=uuid4(),
        assessment_id=assessment.id,
        assessment=assessment,
        sequence=1,
        content="LOTO",
        is_completed=False,
    )

    class FakeRepository:
        saved = False

        def get_checklist_item_for_update(self, _assessment_id, _item_id):
            return item

        def save_checklist_completion(self, *_args, **_kwargs):
            self.saved = True
            return item

    class FakeSession:
        rolled_back = False

        def rollback(self):
            self.rolled_back = True

    repository = FakeRepository()
    session = FakeSession()
    with patch(
        "app.services.assessment.AssessmentRepository",
        return_value=repository,
    ):
        response = AssessmentService(
            retrieval_service=CapturingRetrieval()
        ).update_checklist_item(
            str(assessment.id),
            str(item.id),
            True,
            session,  # type: ignore[arg-type]
            outsider,
            RetrievalAccessScope(site_ids=[str(uuid4())]),
        )

    assert response is None
    assert session.rolled_back is True
    assert repository.saved is False


def test_checklist_repository_writes_completion_and_uncompletion_audits() -> None:
    class RecordingSession:
        def __init__(self) -> None:
            self.added = []

        def add(self, value) -> None:
            self.added.append(value)

        def commit(self) -> None:
            pass

        def refresh(self, _value) -> None:
            pass

        def rollback(self) -> None:
            pass

    actor_id = uuid4()
    item = ChecklistItem(
        id=uuid4(),
        assessment_id=uuid4(),
        sequence=2,
        content="잔류 에너지 확인",
        is_completed=True,
    )
    session = RecordingSession()
    repository = AssessmentRepository(session)  # type: ignore[arg-type]

    repository.save_checklist_completion(item, actor_user_id=actor_id)
    item.is_completed = False
    repository.save_checklist_completion(item, actor_user_id=actor_id)

    audits = [value for value in session.added if isinstance(value, AuditEvent)]
    assert [audit.event_type for audit in audits] == [
        "checklist.completed",
        "checklist.uncompleted",
    ]
    assert all(audit.actor_user_id == actor_id for audit in audits)
    assert audits[0].payload == {
        "assessment_id": str(item.assessment_id),
        "checklist_item_id": str(item.id),
        "sequence": 2,
        "is_completed": True,
    }
    assert audits[1].payload["is_completed"] is False
