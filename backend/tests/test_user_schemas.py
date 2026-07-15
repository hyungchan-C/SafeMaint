from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.user import AuthProvider, UserCreate, UserResponse


def test_user_create_normalizes_login_identity() -> None:
    user = UserCreate(
        employee_number=" 0012a ",
        name="  홍길동 ",
        email="USER@Example.com ",
        department=" 안전관리팀 ",
        password="long-local-password",
    )

    assert user.employee_number == "0012A"
    assert user.name == "홍길동"
    assert user.email == "user@example.com"
    assert user.department == "안전관리팀"


def test_local_and_external_password_policies() -> None:
    with pytest.raises(ValidationError):
        UserCreate(
            employee_number="   ",
            name="Blank Employee",
            password="long-local-password",
        )

    with pytest.raises(ValidationError):
        UserCreate(employee_number="0001", name="Local User")

    external = UserCreate(
        employee_number="0002",
        name="LDAP User",
        auth_provider=AuthProvider.LDAP,
    )
    assert external.password is None

    with pytest.raises(ValidationError):
        UserCreate(
            employee_number="0003",
            name="LDAP User",
            auth_provider=AuthProvider.LDAP,
            password="must-not-be-sent",
        )


def test_user_response_never_exposes_password_material() -> None:
    now = datetime.now(UTC)
    source = SimpleNamespace(
        id=uuid4(),
        employee_number="0007",
        name="Response User",
        email=None,
        department=None,
        job_title=None,
        auth_provider="local",
        password_hash="$argon2id$never-return-this",
        status="active",
        failed_login_count=0,
        locked_until=None,
        last_login_at=None,
        password_changed_at=None,
        deactivated_at=None,
        created_at=now,
        updated_at=now,
        roles=[],
    )

    payload = UserResponse.model_validate(source).model_dump()
    assert "password" not in payload
    assert "password_hash" not in payload
