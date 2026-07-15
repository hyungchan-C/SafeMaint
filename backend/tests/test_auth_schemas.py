from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.auth import AuthUserResponse, LoginRequest, RegisterRequest


def test_register_request_normalizes_identity_fields() -> None:
    request = RegisterRequest(
        employee_number=" 00-ab12 ",
        name=" 홍길동 ",
        password="Correct-Horse-2026!",
        email="USER@Example.com ",
        department=" 안전관리팀 ",
        job_title=" 작업자 ",
    )

    assert request.employee_number == "00-AB12"
    assert request.name == "홍길동"
    assert request.email == "user@example.com"
    assert request.department == "안전관리팀"
    assert request.job_title == "작업자"


def test_auth_requests_enforce_password_and_login_identity_rules() -> None:
    with pytest.raises(ValidationError):
        RegisterRequest(
            employee_number="0001",
            name="Short Password",
            password="too-short",
        )

    with pytest.raises(ValidationError):
        RegisterRequest(
            employee_number="0002",
            name="Blank Password",
            password="            ",
        )

    login = LoginRequest(employee_number=" emp-01 ", password="password")
    assert login.employee_number == "EMP-01"


def test_auth_response_never_contains_password_material() -> None:
    response = AuthUserResponse(
        id=uuid4(),
        employee_number="0007",
        name="Response User",
        email=None,
        department=None,
        job_title=None,
        status="active",
        roles=["worker"],
        last_login_at=datetime.now(UTC),
    ).model_dump()

    assert response["roles"] == ["worker"]
    assert "password" not in response
    assert "password_hash" not in response
