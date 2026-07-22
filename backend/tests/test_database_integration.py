import asyncio
from datetime import datetime, timezone
from os import getenv
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.db.models import (
    AuditEvent,
    ChecklistItem,
    Document,
    DocumentProcessingJob,
    DocumentVersion,
    ReferenceCode,
    Role,
    Site,
    User,
    UserRole,
    UserSite,
)
from app.db.seed import REFERENCE_CODES, ROLES, seed_reference_data, seed_roles
from app.db.session import SessionLocal, engine
from app.main import app
from app.schemas.chat import ChatResponse
from app.services.chat import get_chat_service
from app.services.passwords import verify_password


INTEGRATION_ENABLED = (
    getenv("RUN_DB_INTEGRATION") == "1"
    and getenv("ALLOW_TEST_DB_MUTATION") == "1"
)

pytestmark = pytest.mark.skipif(
    not INTEGRATION_ENABLED,
    reason="Set RUN_DB_INTEGRATION=1 and ALLOW_TEST_DB_MUTATION=1 to run DB tests",
)


def _assert_isolated_test_database() -> None:
    database_name = make_url(settings.database_url).database or ""
    if not database_name.endswith("_test"):
        raise RuntimeError(
            "Database integration tests only run against a database ending in '_test'."
        )


async def _request(method: str, path: str, **kwargs: object):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def test_schema_extension_and_alembic_head() -> None:
    _assert_isolated_test_database()

    expected_tables = {
        "alembic_version",
        "auth_sessions",
        "assessment_evidence",
        "assessment_hazards",
        "assessments",
        "audit_events",
        "checklist_items",
        "components",
        "document_chunks",
        "document_processing_jobs",
        "document_types",
        "document_versions",
        "documents",
        "equipment",
        "permissions",
        "public_rag_packages",
        "reference_codes",
        "roles",
        "role_permissions",
        "sites",
        "user_roles",
        "user_sites",
        "users",
    }
    assert expected_tables <= set(inspect(engine).get_table_names())

    with engine.connect() as connection:
        extension_version = connection.scalar(
            text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        )
        alembic_revision = connection.scalar(
            text("SELECT version_num FROM alembic_version")
        )
        constraints = set(
            connection.scalars(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid IN "
                    "('assessment_hazards'::regclass, 'assessments'::regclass)"
                )
            )
        )

    assert extension_version
    assert alembic_revision == "0009_processing_metadata"
    assert "ck_assessment_hazards_likelihood_range" in constraints
    assert "ck_assessment_hazards_severity_range" in constraints
    assert "ck_assessments_status" in constraints


def test_reference_seed_is_idempotent() -> None:
    _assert_isolated_test_database()

    with SessionLocal() as session:
        seed_reference_data(session)
        first_count = session.scalar(select(func.count(ReferenceCode.id)))
        seed_reference_data(session)
        second_count = session.scalar(select(func.count(ReferenceCode.id)))

    assert first_count == len(REFERENCE_CODES)
    assert second_count == first_count


def test_role_seed_is_idempotent() -> None:
    _assert_isolated_test_database()

    with SessionLocal() as session:
        seed_roles(session)
        first_count = session.scalar(select(func.count(Role.id)))
        seed_roles(session)
        second_count = session.scalar(select(func.count(Role.id)))

    assert first_count == len(ROLES)
    assert second_count == first_count


def test_user_constraints_and_case_insensitive_identity() -> None:
    _assert_isolated_test_database()
    suffix = uuid4().hex[:10].upper()
    email = f"person-{suffix}@Example.com"

    with SessionLocal() as session:
        first = User(
            employee_number=f"00{suffix}",
            name="Test User",
            email=email,
            auth_provider="ldap",
        )
        session.add(first)
        session.commit()
        session.refresh(first)
        assert first.employee_number == f"00{suffix}"

    with SessionLocal() as session:
        session.add(
            User(
                employee_number=f"00{suffix}",
                name="Duplicate Employee Number",
                auth_provider="ldap",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()

    with SessionLocal() as session:
        session.add(
            User(
                employee_number=f"01{suffix}",
                name="Duplicate Email",
                email=email.lower(),
                auth_provider="ldap",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()

    invalid_users = (
        User(
            employee_number=f"02{suffix}",
            name="Invalid Provider",
            auth_provider="unknown",
        ),
        User(
            employee_number=f"03{suffix}",
            name="Invalid Status",
            auth_provider="ldap",
            status="deleted",
        ),
        User(
            employee_number=f"04{suffix}",
            name="Invalid Counter",
            auth_provider="ldap",
            failed_login_count=-1,
        ),
        User(
            employee_number=f"05{suffix}",
            name="Missing Hash",
            auth_provider="local",
        ),
        User(
            employee_number=f" lower-{suffix} ",
            name="Unnormalized Employee Number",
            auth_provider="ldap",
        ),
    )
    for invalid_user in invalid_users:
        with SessionLocal() as session:
            session.add(invalid_user)
            with pytest.raises(IntegrityError):
                session.commit()


def test_role_and_site_assignments_enforce_uniqueness() -> None:
    _assert_isolated_test_database()
    suffix = uuid4().hex[:10].upper()

    with SessionLocal() as session:
        seed_roles(session)
        role = session.scalar(select(Role).where(Role.code == "worker"))
        assert role is not None
        assigner = User(
            employee_number=f"ADM{suffix}",
            name="Assignment Admin",
            auth_provider="ldap",
        )
        worker = User(
            employee_number=f"WRK{suffix}",
            name="Assigned Worker",
            auth_provider="ldap",
        )
        first_site = Site(code=f"SITE-A-{suffix}", name="First Test Site")
        second_site = Site(code=f"SITE-B-{suffix}", name="Second Test Site")
        session.add_all((assigner, worker, first_site, second_site))
        session.flush()
        session.add_all(
            (
                UserRole(
                    user_id=worker.id,
                    role_id=role.id,
                    assigned_by_user_id=assigner.id,
                ),
                UserSite(
                    user_id=worker.id,
                    site_id=first_site.id,
                    is_primary=True,
                    assigned_by_user_id=assigner.id,
                ),
            )
        )
        session.commit()
        worker_id = worker.id
        role_id = role.id
        first_site_id = first_site.id
        second_site_id = second_site.id

    with SessionLocal() as session:
        session.add(UserRole(user_id=worker_id, role_id=role_id))
        with pytest.raises(IntegrityError):
            session.commit()

    with SessionLocal() as session:
        session.add(UserSite(user_id=worker_id, site_id=first_site_id))
        with pytest.raises(IntegrityError):
            session.commit()

    with SessionLocal() as session:
        session.add(
            UserSite(user_id=worker_id, site_id=second_site_id, is_primary=True)
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_registration_login_and_temporary_lock_use_database() -> None:
    _assert_isolated_test_database()
    suffix = uuid4().hex[:10].upper()
    employee_number = f"AUTH-{suffix}"
    email = f"auth-{suffix.lower()}@example.com"
    password = "Correct-Horse-2026!"

    with SessionLocal() as session:
        seed_roles(session)

    registration = asyncio.run(
        _request(
            "POST",
            "/api/v1/auth/register",
            json={
                "employee_number": f" {employee_number.lower()} ",
                "name": " 통합 인증 사용자 ",
                "password": password,
                "email": email.upper(),
                "department": " 안전관리팀 ",
                "job_title": " 작업자 ",
            },
        )
    )
    assert registration.status_code == 201
    body = registration.json()
    assert body["employee_number"] == employee_number
    assert body["email"] == email
    assert body["roles"] == ["worker"]
    assert "password" not in body
    assert "password_hash" not in body

    with SessionLocal() as session:
        user = session.scalar(
            select(User).where(User.employee_number == employee_number)
        )
        assert user is not None
        user_id = user.id
        assert user.password_hash is not None
        assert user.password_hash.startswith("$argon2id$")
        assert user.password_hash != password
        assert verify_password(password, user.password_hash)
        assert session.scalar(
            select(func.count(UserRole.role_id)).where(UserRole.user_id == user.id)
        ) == 1
        assert session.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.actor_user_id == user.id,
                AuditEvent.event_type == "user.registered",
            )
        ) == 1

    duplicate = asyncio.run(
        _request(
            "POST",
            "/api/v1/auth/register",
            json={
                "employee_number": employee_number,
                "name": "Duplicate",
                "password": password,
            },
        )
    )
    assert duplicate.status_code == 409

    wrong_login = asyncio.run(
        _request(
            "POST",
            "/api/v1/auth/login",
            json={"employee_number": employee_number, "password": "wrong-password"},
        )
    )
    assert wrong_login.status_code == 401

    successful_login = asyncio.run(
        _request(
            "POST",
            "/api/v1/auth/login",
            json={"employee_number": employee_number.lower(), "password": password},
        )
    )
    assert successful_login.status_code == 200
    login_body = successful_login.json()
    assert login_body["token_type"] == "bearer"
    assert login_body["access_token"]
    assert login_body["user"]["roles"] == ["worker"]
    assert login_body["user"]["last_login_at"] is not None

    with SessionLocal() as session:
        user = session.get(User, user_id)
        assert user is not None
        assert user.failed_login_count == 0
        assert user.last_login_at is not None

    for _ in range(5):
        failed = asyncio.run(
            _request(
                "POST",
                "/api/v1/auth/login",
                json={
                    "employee_number": employee_number,
                    "password": "wrong-password",
                },
            )
        )
        assert failed.status_code == 401

    locked = asyncio.run(
        _request(
            "POST",
            "/api/v1/auth/login",
            json={"employee_number": employee_number, "password": password},
        )
    )
    assert locked.status_code == 423

    with SessionLocal() as session:
        user = session.get(User, user_id)
        assert user is not None
        assert user.status == "locked"
        assert user.failed_login_count == 5
        assert user.locked_until is not None
        assert session.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.actor_user_id == user.id,
                AuditEvent.event_type == "user.login_locked",
            )
        ) == 1


def test_upload_requires_permission_and_creates_processing_job() -> None:
    _assert_isolated_test_database()
    suffix = uuid4().hex[:10].upper()
    employee_number = f"UPLOAD-{suffix}"
    password = f"Test-{uuid4().hex}-A!"

    registration = asyncio.run(
        _request(
            "POST",
            "/api/v1/auth/register",
            json={
                "employee_number": employee_number,
                "name": "문서 업로드 통합 사용자",
                "password": password,
            },
        )
    )
    assert registration.status_code == 201

    login = asyncio.run(
        _request(
            "POST",
            "/api/v1/auth/login",
            json={"employee_number": employee_number, "password": password},
        )
    )
    assert login.status_code == 200
    access_token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}
    upload_data = {
        "product_type": "포토센서",
        "model_name": f"BTS-{suffix}",
        "manufacturer": "통합테스트 제조사",
    }
    upload_file = {
        "file": ("manual.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf")
    }

    denied = asyncio.run(
        _request(
            "POST",
            "/api/v1/documents/upload",
            headers=headers,
            data=upload_data,
            files=upload_file,
        )
    )
    assert denied.status_code == 403

    with SessionLocal() as session:
        user = session.scalar(
            select(User).where(User.employee_number == employee_number)
        )
        manager_role = session.scalar(
            select(Role).where(Role.code == "safety_manager")
        )
        assert user is not None
        assert manager_role is not None
        user_id = user.id
        session.add(UserRole(user_id=user.id, role_id=manager_role.id))
        session.commit()

    public_access = asyncio.run(
        _request(
            "POST",
            "/api/v1/documents/upload",
            headers=headers,
            data={**upload_data, "access_level": "public"},
            files=upload_file,
        )
    )
    assert public_access.status_code == 422

    public_type = asyncio.run(
        _request(
            "POST",
            "/api/v1/documents/upload",
            headers=headers,
            data={**upload_data, "document_type_code": "public_guide"},
            files=upload_file,
        )
    )
    assert public_type.status_code == 422

    uploaded = asyncio.run(
        _request(
            "POST",
            "/api/v1/documents/upload",
            headers=headers,
            data=upload_data,
            files=upload_file,
        )
    )
    assert uploaded.status_code == 201
    upload_body = uploaded.json()
    assert upload_body["version_number"] == 1
    assert upload_body["status"] == "pending"

    with SessionLocal() as session:
        document = session.get(Document, UUID(upload_body["document_id"]))
        version = session.get(
            DocumentVersion, UUID(upload_body["document_version_id"])
        )
        assert document is not None
        assert document.created_by_user_id == user_id
        assert document.document_type_code == "equipment_manual"
        assert version is not None
        assert version.uploaded_by_user_id == user_id
        assert session.scalar(
            select(func.count(DocumentProcessingJob.id)).where(
                DocumentProcessingJob.document_version_id == version.id
            )
        ) == 1

    logged_out = asyncio.run(
        _request("POST", "/api/v1/auth/logout", headers=headers)
    )
    assert logged_out.status_code == 204

    after_logout = asyncio.run(
        _request(
            "POST",
            "/api/v1/documents/upload",
            headers=headers,
            data={**upload_data, "model_name": f"BTS-{suffix}-RETRY"},
            files=upload_file,
        )
    )
    assert after_logout.status_code == 401


def test_document_approval_activates_and_supersedes_versions() -> None:
    _assert_isolated_test_database()
    suffix = uuid4().hex[:10].upper()
    employee_number = f"APPROVE-{suffix}"
    password = f"Test-{uuid4().hex}-A!"

    registration = asyncio.run(
        _request(
            "POST",
            "/api/v1/auth/register",
            json={
                "employee_number": employee_number,
                "name": "문서 승인 통합 사용자",
                "password": password,
            },
        )
    )
    assert registration.status_code == 201

    with SessionLocal() as session:
        user = session.scalar(
            select(User).where(User.employee_number == employee_number)
        )
        manager_role = session.scalar(
            select(Role).where(Role.code == "document_manager")
        )
        assert user is not None
        assert manager_role is not None
        manager_user_id = user.id
        session.add(UserRole(user_id=user.id, role_id=manager_role.id))
        session.commit()

    login = asyncio.run(
        _request(
            "POST",
            "/api/v1/auth/login",
            json={"employee_number": employee_number, "password": password},
        )
    )
    assert login.status_code == 200
    manager_headers = {
        "Authorization": f"Bearer {login.json()['access_token']}"
    }
    upload_data = {
        "product_type": "라이트커튼",
        "model_name": f"TEST-LC-{suffix}",
        "manufacturer": "SafeMaint E2E",
    }
    upload_file = {
        "file": ("approval-manual.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf")
    }

    first_upload = asyncio.run(
        _request(
            "POST",
            "/api/v1/documents/upload",
            headers=manager_headers,
            data=upload_data,
            files=upload_file,
        )
    )
    assert first_upload.status_code == 201
    first_body = first_upload.json()
    document_id = UUID(first_body["document_id"])
    first_version_id = UUID(first_body["document_version_id"])

    pending_approval = asyncio.run(
        _request(
            "POST",
            f"/api/v1/documents/{document_id}/versions/{first_version_id}/approve",
            headers=manager_headers,
        )
    )
    assert pending_approval.status_code == 409

    worker_number = f"APPROVE-WORKER-{suffix}"
    worker_password = f"Test-{uuid4().hex}-A!"
    assert asyncio.run(
        _request(
            "POST",
            "/api/v1/auth/register",
            json={
                "employee_number": worker_number,
                "name": "승인 권한 없는 사용자",
                "password": worker_password,
            },
        )
    ).status_code == 201
    worker_login = asyncio.run(
        _request(
            "POST",
            "/api/v1/auth/login",
            json={"employee_number": worker_number, "password": worker_password},
        )
    )
    assert worker_login.status_code == 200
    worker_headers = {
        "Authorization": f"Bearer {worker_login.json()['access_token']}"
    }
    unauthorized = asyncio.run(
        _request(
            "POST",
            f"/api/v1/documents/{document_id}/versions/{first_version_id}/approve",
            headers=worker_headers,
        )
    )
    assert unauthorized.status_code == 403

    with SessionLocal() as session:
        document = session.get(Document, document_id)
        first_version = session.get(DocumentVersion, first_version_id)
        assert document is not None
        assert first_version is not None
        document.lifecycle_status = "review_required"
        first_version.status = "review_required"
        unrelated_document = Document(
            external_id=f"approval-other:{suffix}",
            title="Unrelated approval document",
            source_type="manual",
            document_type_code="equipment_manual",
            access_level="restricted",
            metadata_json={},
        )
        session.add(unrelated_document)
        session.commit()
        unrelated_document_id = unrelated_document.id

        excluded_document_ids: set[UUID] = set()
        for label, lifecycle_status, version_status in (
            ("active", "active", "active"),
            ("failed", "failed", "failed"),
            ("superseded", "active", "superseded"),
            ("deleted", "deleted", "deleted"),
        ):
            excluded_document = Document(
                external_id=f"approval-excluded:{label}:{suffix}",
                title=f"Excluded {label} document",
                source_type="manual",
                document_type_code="equipment_manual",
                access_level="restricted",
                lifecycle_status=lifecycle_status,
                deleted_at=(
                    datetime.now(timezone.utc) if lifecycle_status == "deleted" else None
                ),
                metadata_json={},
            )
            session.add(excluded_document)
            session.flush()
            excluded_version = DocumentVersion(
                document_id=excluded_document.id,
                version_number=1,
                original_filename=f"excluded-{label}.pdf",
                stored_filename=f"excluded-{label}-{suffix}.pdf",
                storage_path=f"/tmp/excluded-{label}-{suffix}.pdf",
                sha256=(label.encode().hex() + suffix.lower()).ljust(64, "0")[:64],
                file_size=1,
                mime_type="application/pdf",
                status=version_status,
                is_active=version_status == "active",
                uploaded_by_user_id=manager_user_id,
            )
            session.add(excluded_version)
            session.flush()
            if version_status == "active":
                excluded_document.current_version_id = excluded_version.id
            excluded_document_ids.add(excluded_document.id)
        session.commit()

    mismatched = asyncio.run(
        _request(
            "POST",
            f"/api/v1/documents/{unrelated_document_id}/versions/{first_version_id}/approve",
            headers=manager_headers,
        )
    )
    assert mismatched.status_code == 404

    worker_queue = asyncio.run(
        _request(
            "GET",
            "/api/v1/documents/review-queue",
            headers=worker_headers,
        )
    )
    assert worker_queue.status_code == 403

    manager_queue = asyncio.run(
        _request(
            "GET",
            "/api/v1/documents/review-queue",
            headers=manager_headers,
        )
    )
    assert manager_queue.status_code == 200
    queued_first_version = next(
        item
        for item in manager_queue.json()
        if item["document_id"] == str(document_id)
    )
    assert queued_first_version["document_version_id"] == str(first_version_id)
    assert queued_first_version["version_status"] == "review_required"
    assert queued_first_version["uploader_name"] == "문서 승인 통합 사용자"
    assert excluded_document_ids.isdisjoint(
        {UUID(item["document_id"]) for item in manager_queue.json()}
    )

    first_approval = asyncio.run(
        _request(
            "POST",
            f"/api/v1/documents/{document_id}/versions/{first_version_id}/approve",
            headers=manager_headers,
        )
    )
    assert first_approval.status_code == 200
    assert first_approval.json()["status"] == "active"
    assert first_approval.json()["is_active"] is True

    duplicate_approval = asyncio.run(
        _request(
            "POST",
            f"/api/v1/documents/{document_id}/versions/{first_version_id}/approve",
            headers=manager_headers,
        )
    )
    assert duplicate_approval.status_code == 409

    queue_after_approval = asyncio.run(
        _request(
            "GET",
            "/api/v1/documents/review-queue",
            headers=manager_headers,
        )
    )
    assert queue_after_approval.status_code == 200
    assert all(
        item["document_id"] != str(document_id)
        for item in queue_after_approval.json()
    )

    second_upload = asyncio.run(
        _request(
            "POST",
            "/api/v1/documents/upload",
            headers=manager_headers,
            data=upload_data,
            files=upload_file,
        )
    )
    assert second_upload.status_code == 201
    second_body = second_upload.json()
    assert second_body["version_number"] == 2
    second_version_id = UUID(second_body["document_version_id"])

    third_upload = asyncio.run(
        _request(
            "POST",
            "/api/v1/documents/upload",
            headers=manager_headers,
            data=upload_data,
            files=upload_file,
        )
    )
    assert third_upload.status_code == 201
    third_body = third_upload.json()
    assert third_body["version_number"] == 3
    third_version_id = UUID(third_body["document_version_id"])

    with SessionLocal() as session:
        document = session.get(Document, document_id)
        second_version = session.get(DocumentVersion, second_version_id)
        third_version = session.get(DocumentVersion, third_version_id)
        assert document is not None
        assert second_version is not None
        assert third_version is not None
        document.lifecycle_status = "review_required"
        second_version.status = "review_required"
        third_version.status = "review_required"
        session.commit()

    latest_queue = asyncio.run(
        _request(
            "GET",
            "/api/v1/documents/review-queue?include_processing=true",
            headers=manager_headers,
        )
    )
    assert latest_queue.status_code == 200
    latest_item = next(
        item
        for item in latest_queue.json()
        if item["document_id"] == str(document_id)
    )
    assert latest_item["document_version_id"] == str(third_version_id)
    assert latest_item["version_number"] == 3

    latest_approval = asyncio.run(
        _request(
            "POST",
            f"/api/v1/documents/{document_id}/versions/{third_version_id}/approve",
            headers=manager_headers,
        )
    )
    assert latest_approval.status_code == 200

    with SessionLocal() as session:
        document = session.get(Document, document_id)
        first_version = session.get(DocumentVersion, first_version_id)
        second_version = session.get(DocumentVersion, second_version_id)
        third_version = session.get(DocumentVersion, third_version_id)
        assert document is not None
        assert document.current_version_id == third_version_id
        assert document.lifecycle_status == "active"
        assert first_version is not None
        assert first_version.status == "superseded"
        assert first_version.is_active is False
        assert second_version is not None
        assert second_version.status == "review_required"
        assert second_version.is_active is False
        assert third_version is not None
        assert third_version.status == "active"
        assert third_version.is_active is True
        assert third_version.approved_by_user_id is not None
        assert third_version.approved_at is not None
        assert session.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.entity_id == document_id,
                AuditEvent.event_type == "DOCUMENT_VERSION_APPROVED",
            )
        ) == 2


def test_chat_scope_is_derived_from_authenticated_roles_and_sites() -> None:
    _assert_isolated_test_database()
    suffix = uuid4().hex[:10].upper()
    employee_number = f"CHAT-{suffix}"
    password = f"Test-{uuid4().hex}-A!"
    captured_scopes = []

    class CapturingChatService:
        async def answer(self, request, access_scope=None) -> ChatResponse:
            captured_scopes.append(access_scope)
            return ChatResponse(
                answer="scope captured",
                sources=[],
                retrieval_mode="hybrid",
            )

    app.dependency_overrides[get_chat_service] = lambda: CapturingChatService()
    try:
        anonymous = asyncio.run(
            _request(
                "POST",
                "/api/v1/chat",
                json={
                    "question": "public safety guide",
                    "access_scope": {
                        "allow_company": True,
                        "all_sites": True,
                    },
                },
            )
        )
        assert anonymous.status_code == 200
        assert captured_scopes[-1].allow_company is False
        assert captured_scopes[-1].all_sites is False

        registration = asyncio.run(
            _request(
                "POST",
                "/api/v1/auth/register",
                json={
                    "employee_number": employee_number,
                    "name": "채팅 범위 통합 사용자",
                    "password": password,
                },
            )
        )
        assert registration.status_code == 201

        with SessionLocal() as session:
            user = session.scalar(
                select(User).where(User.employee_number == employee_number)
            )
            assert user is not None
            site = Site(code=f"CHAT-SITE-{suffix}", name="Chat scope site")
            session.add(site)
            session.flush()
            site_id = site.id
            session.add(UserSite(user_id=user.id, site_id=site.id, is_primary=True))
            session.commit()

        login = asyncio.run(
            _request(
                "POST",
                "/api/v1/auth/login",
                json={"employee_number": employee_number, "password": password},
            )
        )
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        scoped = asyncio.run(
            _request(
                "POST",
                "/api/v1/chat",
                headers=headers,
                json={"question": "company equipment manual"},
            )
        )
        assert scoped.status_code == 200
        assert captured_scopes[-1].allow_company is True
        assert captured_scopes[-1].all_sites is False
        assert captured_scopes[-1].site_ids == [str(site_id)]
        assert captured_scopes[-1].allow_private is False

        with SessionLocal() as session:
            user = session.scalar(
                select(User).where(User.employee_number == employee_number)
            )
            manager_role = session.scalar(
                select(Role).where(Role.code == "document_manager")
            )
            assert user is not None
            assert manager_role is not None
            session.add(UserRole(user_id=user.id, role_id=manager_role.id))
            session.commit()

        global_scope = asyncio.run(
            _request(
                "POST",
                "/api/v1/chat",
                headers=headers,
                json={"question": "company equipment manual"},
            )
        )
        assert global_scope.status_code == 200
        assert captured_scopes[-1].allow_company is True
        assert captured_scopes[-1].all_sites is True
        assert captured_scopes[-1].site_ids == []
    finally:
        app.dependency_overrides.clear()


def test_assessment_is_persisted_and_dashboard_uses_database() -> None:
    _assert_isolated_test_database()

    suffix = uuid4().hex[:10].upper()
    employee_number = f"ASSESS-{suffix}"
    password = f"Test-{uuid4().hex}-A!"
    registration = asyncio.run(
        _request(
            "POST",
            "/api/v1/auth/register",
            json={
                "employee_number": employee_number,
                "name": "평가 통합테스트 사용자",
                "password": password,
            },
        )
    )
    assert registration.status_code == 201
    login = asyncio.run(
        _request(
            "POST",
            "/api/v1/auth/login",
            json={"employee_number": employee_number, "password": password},
        )
    )
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    payload = {
        "site_name": "통합테스트 사업장",
        "equipment_name": "컨베이어 TEST-01",
        "component_name": "벨트",
        "task_type": "이물질 제거",
        "energy_sources": ["전기"],
        "description": "전원을 차단하고 컨베이어 벨트 이물질을 제거합니다.",
    }
    created = asyncio.run(
        _request(
            "POST",
            "/api/v1/assessments",
            headers=headers,
            json=payload,
        )
    )
    assert created.status_code == 201
    created_body = created.json()
    assessment_id = created_body["assessment_id"]
    assert created_body["tbm_checklist"]
    assert len(created_body["checklist_items"]) == len(
        created_body["tbm_checklist"]
    )
    assert all(item["id"] for item in created_body["checklist_items"])
    first_item = created_body["checklist_items"][0]

    completed = asyncio.run(
        _request(
            "PATCH",
            f"/api/v1/assessments/{assessment_id}/checklist-items/{first_item['id']}",
            headers=headers,
            json={"is_completed": True},
        )
    )
    assert completed.status_code == 200
    completed_body = completed.json()
    assert completed_body["assessment_id"] == assessment_id
    assert completed_body["is_completed"] is True
    assert completed_body["completed_by_user_id"] == login.json()["user"]["id"]
    assert completed_body["completed_at"] is not None

    wrong_assessment = asyncio.run(
        _request(
            "PATCH",
            f"/api/v1/assessments/{uuid4()}/checklist-items/{first_item['id']}",
            headers=headers,
            json={"is_completed": True},
        )
    )
    missing_item = asyncio.run(
        _request(
            "PATCH",
            f"/api/v1/assessments/{assessment_id}/checklist-items/{uuid4()}",
            headers=headers,
            json={"is_completed": True},
        )
    )
    forged_actor = asyncio.run(
        _request(
            "PATCH",
            f"/api/v1/assessments/{assessment_id}/checklist-items/{first_item['id']}",
            headers=headers,
            json={
                "is_completed": True,
                "completed_by_user_id": str(uuid4()),
                "completed_at": "2020-01-01T00:00:00Z",
            },
        )
    )
    assert wrong_assessment.status_code == 404
    assert missing_item.status_code == 404
    assert forged_actor.status_code == 422

    loaded = asyncio.run(
        _request(
            "GET",
            f"/api/v1/assessments/{assessment_id}",
            headers=headers,
        )
    )
    assert loaded.status_code == 200
    assert loaded.json()["assessment_id"] == assessment_id
    assert loaded.json()["hazards"] == created.json()["hazards"]
    loaded_item = next(
        item
        for item in loaded.json()["checklist_items"]
        if item["id"] == first_item["id"]
    )
    assert loaded_item["is_completed"] is True
    assert loaded_item["completed_by_user_id"] == login.json()["user"]["id"]

    uncompleted = asyncio.run(
        _request(
            "PATCH",
            f"/api/v1/assessments/{assessment_id}/checklist-items/{first_item['id']}",
            headers=headers,
            json={"is_completed": False},
        )
    )
    assert uncompleted.status_code == 200
    assert uncompleted.json()["is_completed"] is False
    assert uncompleted.json()["completed_by_user_id"] is None
    assert uncompleted.json()["completed_at"] is None

    with SessionLocal() as session:
        stored_item = session.get(ChecklistItem, UUID(first_item["id"]))
        assert stored_item is not None
        assert stored_item.is_completed is False
        assert stored_item.completed_by_user_id is None
        assert stored_item.completed_at is None
        audit_types = set(
            session.scalars(
                select(AuditEvent.event_type).where(
                    AuditEvent.entity_id == UUID(first_item["id"]),
                    AuditEvent.event_type.in_(
                        ["checklist.completed", "checklist.uncompleted"]
                    ),
                )
            ).all()
        )
        assert audit_types == {"checklist.completed", "checklist.uncompleted"}

    dashboard = asyncio.run(_request("GET", "/api/v1/dashboard/summary"))
    assert dashboard.status_code == 200
    assert dashboard.json()["today_tasks"] >= 1
    assert dashboard.json()["high_risk_tasks"] >= 1
