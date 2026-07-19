from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.schemas.user import UserCreate, UserStatus


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
        return UserCreate.normalize_name(value)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str | None) -> str | None:
        return UserCreate.normalize_email(value)

    @field_validator("department", "job_title")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return UserCreate.normalize_optional_text(value)

    @field_validator("password")
    @classmethod
    def reject_blank_password(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("password must not be blank")
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


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: AuthUserResponse
