import asyncio
from os import getenv

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, inspect, select, text
from sqlalchemy.engine import make_url

from app.core.config import settings
from app.db.models import ReferenceCode
from app.db.seed import REFERENCE_CODES, seed_reference_data
from app.db.session import SessionLocal, engine
from app.main import app


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
        "sites",
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
    assert alembic_revision == "0001_initial_schema"
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
