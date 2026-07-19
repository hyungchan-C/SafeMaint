from os import getenv
from uuid import UUID, uuid4

import pytest
from sqlalchemy.engine import make_url

from app.core.config import settings
from app.db.models import Assessment, Document, DocumentChunk
from app.db.session import SessionLocal
from app.schemas.assessment import AssessmentRequest, EvidenceItem
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

    def search(self, request: AssessmentRequest, limit: int = 5):
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
    with SessionLocal() as session:
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
        session.add(document)
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
    with SessionLocal() as session:
        created = service.create_and_save(request, session)
        loaded = service.get_by_id(created.assessment_id, session)
        assert loaded is not None
        assert loaded.evidence_status == "connected"
        assert loaded.evidence[0].chunk_id == str(chunk_id)
        assert loaded.evidence[0].retrieval_rank == 1
        assert loaded.evidence[0].reranker_score == 0.86
        assessment = session.get(Assessment, UUID(created.assessment_id))
        assert assessment is not None
        session.delete(assessment)
        session.commit()

    with SessionLocal() as session:
        document = session.get(Document, document_id)
        assert document is not None
        session.delete(document)
        session.commit()
