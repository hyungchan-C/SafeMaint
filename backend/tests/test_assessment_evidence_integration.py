from os import getenv
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.engine import make_url

from app.core.config import settings
from app.db.models import Assessment, AuditEvent, Document, DocumentChunk, User
from app.db.session import SessionLocal
from app.schemas.assessment import AssessmentRequest, EvidenceItem
from app.schemas.chat import RetrievalAccessScope
from app.services.assessment import AssessmentService


pytestmark = pytest.mark.skipif(
    getenv("RUN_DB_INTEGRATION") != "1"
    or getenv("ALLOW_TEST_DB_MUTATION") != "1",
    reason="Run only against an explicitly enabled isolated test database.",
)


class FakeRetrieval:
    def __init__(self, document_id, chunk_id) -> None:
        self.document_id = document_id
        self.chunk_id = chunk_id

    def search(
        self,
        request: AssessmentRequest,
        access_scope: RetrievalAccessScope,
        limit: int = 5,
    ):
        return [
            EvidenceItem(
                document_id=str(self.document_id),
                chunk_id=str(self.chunk_id),
                title="Public test guide",
                page=3,
                page_start=3,
                page_end=3,
                section="Lockout",
                source_type="public_guide",
                document_scope="public",
                excerpt="Disconnect energy before maintenance.",
                retrieval_rank=1,
                retrieval_score=0.81,
                reranker_score=0.86,
            )
        ]


def test_assessment_evidence_is_saved_and_returned() -> None:
    assert (make_url(settings.database_url).database or "").endswith("_test")
    document_id = uuid4()
    chunk_id = uuid4()
    user_id = uuid4()
    with SessionLocal() as session:
        user = User(
            id=user_id,
            employee_number=f"ASSESS-{user_id.hex[:12].upper()}",
            name="Assessment integration user",
            auth_provider="oidc",
            status="active",
        )
        document = Document(
            id=document_id,
            external_id=f"assessment-evidence-test:{document_id}",
            title="Public test guide",
            source_type="guide",
            document_type_code="public_guide",
            lifecycle_status="active",
            access_level="public",
        )
        document.chunks.append(
            DocumentChunk(
                id=chunk_id,
                chunk_index=0,
                page_number=3,
                page_start=3,
                page_end=3,
                section_path=["Lockout"],
                content="Disconnect energy before maintenance.",
                content_hash="f" * 64,
                metadata_json={"section": "Lockout"},
                embedding_status="pending",
            )
        )
        session.add_all((user, document))
        session.commit()

    request = AssessmentRequest(
        site_name="Test site",
        equipment_name="Test conveyor",
        component_name="Bearing",
        task_type="Replacement",
        description="Replace a conveyor bearing after shutdown.",
    )
    service = AssessmentService(
        retrieval_service=FakeRetrieval(document_id, chunk_id)
    )
    scope = RetrievalAccessScope(requester_user_id=user_id)
    with SessionLocal() as session:
        user = session.get(User, user_id)
        assert user is not None
        created = service.create_and_save(request, session, user, scope)
        loaded = service.get_by_id(created.assessment_id, session, user, scope)
        assert loaded is not None
        assert loaded.evidence_status == "connected"
        assert loaded.evidence[0].chunk_id == str(chunk_id)
        assert loaded.evidence[0].retrieval_rank == 1
        assert loaded.evidence[0].reranker_score == 0.86
        assessment = session.get(Assessment, UUID(created.assessment_id))
        assert assessment is not None
        assert assessment.created_by_user_id == user_id
        audit_event = session.scalar(
            select(AuditEvent).where(
                AuditEvent.entity_id == assessment.id,
                AuditEvent.event_type == "assessment.created",
            )
        )
        assert audit_event is not None
        assert audit_event.actor_user_id == user_id
        session.delete(audit_event)
        session.delete(assessment)
        session.commit()

    with SessionLocal() as session:
        document = session.get(Document, document_id)
        assert document is not None
        session.delete(document)
        user = session.get(User, user_id)
        assert user is not None
        session.delete(user)
        session.commit()
