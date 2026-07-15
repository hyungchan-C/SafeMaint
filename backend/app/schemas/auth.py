from datetime import datetime
import re
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.schemas.user import ASCII_PASSWORD_PATTERN, UserCreate, UserStatus


KOREAN_NAME_PATTERN = re.compile(r"^[가-힣]+(?: [가-힣]+)*$")
ORGANIZATION_TEXT_PATTERN = re.compile(r"^[A-Za-z가-힣]+(?: [A-Za-z가-힣]+)*$")


class RegisterRequest(BaseModel):
    employee_number: str = Field(min_length=1, max_length=30)
    name: str = Field(min_length=1, max_length=100)
    password: SecretStr = Field(min_length=12, max_length=128)
    email: str | None = Field(default=None, max_length=255)
    department: str | None = Field(default=None, max_length=100)
    job_title: str | None = Field(default=None, max_length=100)

    @field_validator("employee_number")
    @classmethod
    def normalize_employee_number(cls, value: str) -> str:
        return UserCreate.normalize_employee_number(value)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = UserCreate.normalize_name(value)
        if not KOREAN_NAME_PATTERN.fullmatch(normalized):
            raise ValueError("name must contain Korean characters only")
        return normalized

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str | None) -> str | None:
        return UserCreate.normalize_email(value)

    @field_validator("department", "job_title")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        normalized = UserCreate.normalize_optional_text(value)
        if normalized is not None and not ORGANIZATION_TEXT_PATTERN.fullmatch(normalized):
            raise ValueError("department and job_title must contain Korean or English letters only")
        return normalized

    @field_validator("password")
    @classmethod
    def reject_blank_password(cls, value: SecretStr) -> SecretStr:
        password = value.get_secret_value()
        if not password.strip():
            raise ValueError("password must not be blank")
        if not ASCII_PASSWORD_PATTERN.fullmatch(password):
            raise ValueError("password must use English letters, numbers, or ASCII symbols")
        return value


class LoginRequest(BaseModel):
    employee_number: str = Field(min_length=1, max_length=30)
    password: SecretStr = Field(min_length=1, max_length=128)

    @field_validator("employee_number")
    @classmethod
    def normalize_employee_number(cls, value: str) -> str:
        return UserCreate.normalize_employee_number(value)


class AuthUserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    employee_number: str
    name: str
    email: str | None
    department: str | None
    job_title: str | None
    status: UserStatus
    roles: list[str] = Field(default_factory=list)
    last_login_at: datetime | None
