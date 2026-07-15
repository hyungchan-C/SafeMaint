import pytest
from pydantic import ValidationError

from app.schemas.auth import LoginRequest, RegisterRequest


def test_registration_accepts_employee_profile() -> None:
    payload = RegisterRequest(
        employee_number="사원A1024",
        name="홍길동",
        password="Safe1234",
        email="hong1024@example.com",
        department="안전 관리팀",
        job_title="Safety Manager",
    )

    assert payload.employee_number == "사원A1024"
    assert payload.name == "홍길동"


@pytest.mark.parametrize(
    ("field", "value"),
    (("name", "Hong길동"), ("employee_number", "EMP-01"), ("password", "비밀번호123"), ("department", "안전1팀")),
)
def test_registration_rejects_disallowed_characters(field: str, value: str) -> None:
    data = {
        "employee_number": "EMP01",
        "name": "홍길동",
        "password": "Safe1234",
        "department": "안전팀",
    }
    data[field] = value

    with pytest.raises(ValidationError):
        RegisterRequest(**data)


def test_login_only_requires_employee_number_and_password() -> None:
    payload = LoginRequest(employee_number="사원A1024", password="Safe1234")
    assert payload.employee_number == "사원A1024"
