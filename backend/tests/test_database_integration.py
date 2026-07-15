import asyncio
from os import getenv
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.db.models import (
    AuditEvent,
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
        "assessment_evidence",
        "assessment_hazards",
        "assessments",
        "audit_events",
        "checklist_items",
        "components",
        "document_chunks",
        "documents",
        "equipment",
        "reference_codes",
        "roles",
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
    assert alembic_revision == "0004_users_roles_sites"
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
    assert successful_login.json()["roles"] == ["worker"]
    assert successful_login.json()["last_login_at"] is not None

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

def test_assessment_is_persisted_and_dashboard_uses_database() -> None:
    _assert_isolated_test_database()

    payload = {
        "site_name": "통합테스트 사업장",
        "equipment_name": "컨베이어 TEST-01",
        "component_name": "벨트",
        "task_type": "이물질 제거",
        "energy_sources": ["전기"],
        "description": "전원을 차단하고 컨베이어 벨트 이물질을 제거합니다.",
    }
    created = asyncio.run(_request("POST", "/api/v1/assessments", json=payload))
    assert created.status_code == 201
    assessment_id = created.json()["assessment_id"]

    loaded = asyncio.run(
        _request("GET", f"/api/v1/assessments/{assessment_id}")
    )
    assert loaded.status_code == 200
    assert loaded.json()["assessment_id"] == assessment_id
    assert loaded.json()["hazards"] == created.json()["hazards"]

    dashboard = asyncio.run(_request("GET", "/api/v1/dashboard/summary"))
    assert dashboard.status_code == 200
    assert dashboard.json()["today_tasks"] >= 1
    assert dashboard.json()["high_risk_tasks"] >= 1
