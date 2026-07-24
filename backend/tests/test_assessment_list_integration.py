from os import getenv
from uuid import UUID, uuid4

import pytest
from sqlalchemy.engine import make_url

from app.core.config import settings
from app.db.models import Assessment, AuditEvent, User
from app.db.session import SessionLocal
from app.schemas.assessment import ChatChecklistSaveRequest
from app.schemas.chat import RetrievalAccessScope
from app.services.assessment import AssessmentService


pytestmark = pytest.mark.skipif(
    getenv("RUN_DB_INTEGRATION") != "1"
    or getenv("ALLOW_TEST_DB_MUTATION") != "1",
    reason="Run only against an explicitly enabled isolated test database.",
)


def test_list_assessments_scopes_to_owner_unless_all_sites() -> None:
    assert (make_url(settings.database_url).database or "").endswith("_test")
    owner_id = uuid4()
    other_id = uuid4()
    with SessionLocal() as session:
        owner = User(
            id=owner_id,
            employee_number=f"LISTOWNER-{owner_id.hex[:10].upper()}",
            name="Owner",
            auth_provider="oidc",
            status="active",
        )
        other = User(
            id=other_id,
            employee_number=f"LISTOTHER-{other_id.hex[:10].upper()}",
            name="Other",
            auth_provider="oidc",
            status="active",
        )
        session.add_all((owner, other))
        session.commit()

    service = AssessmentService()
    payload = ChatChecklistSaveRequest(
        description="채팅에서 저장한 TBM 체크리스트 목록 테스트용 설명입니다.",
        checklist_items=["항목 1", "항목 2"],
    )
    assessment_id = None
    try:
        with SessionLocal() as session:
            owner = session.get(User, owner_id)
            assert owner is not None
            created = service.create_from_chat_checklist(payload, session, owner)
            assessment_id = created.assessment_id

        no_scope = RetrievalAccessScope()
        all_sites_scope = RetrievalAccessScope(all_sites=True)

        with SessionLocal() as session:
            owner = session.get(User, owner_id)
            other = session.get(User, other_id)
            assert owner is not None and other is not None

            owner_ids = {
                item.assessment_id
                for item in service.list_assessments(session, owner, no_scope)
            }
            assert assessment_id in owner_ids

            other_ids = {
                item.assessment_id
                for item in service.list_assessments(session, other, no_scope)
            }
            assert assessment_id not in other_ids

            other_all_sites_ids = {
                item.assessment_id
                for item in service.list_assessments(session, other, all_sites_scope)
            }
            assert assessment_id in other_all_sites_ids
    finally:
        with SessionLocal() as session:
            if assessment_id is not None:
                parsed_assessment_id = UUID(assessment_id)
                for event in session.query(AuditEvent).filter(
                    AuditEvent.entity_id == parsed_assessment_id
                ):
                    session.delete(event)
                assessment = session.get(Assessment, parsed_assessment_id)
                if assessment is not None:
                    session.delete(assessment)
            for user_id in (owner_id, other_id):
                user = session.get(User, user_id)
                if user is not None:
                    session.delete(user)
            session.commit()
