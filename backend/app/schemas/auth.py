from uuid import UUID

from pydantic import BaseModel, Field, field_validator


EMPLOYEE_PATTERN = r"^[가-힣A-Za-z0-9]+$"
NAME_PATTERN = r"^[가-힣]+$"
ORGANIZATION_PATTERN = r"^[가-힣A-Za-z ]+$"
PASSWORD_PATTERN = r"^[A-Za-z0-9]+$"
EMAIL_PATTERN = r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$"


class RegisterRequest(BaseModel):
    employee_number: str = Field(min_length=1, max_length=30, pattern=EMPLOYEE_PATTERN)
    name: str = Field(min_length=1, max_length=100, pattern=NAME_PATTERN)
    password: str = Field(min_length=8, max_length=128, pattern=PASSWORD_PATTERN)
    email: str | None = Field(default=None, max_length=255, pattern=EMAIL_PATTERN)
    department: str | None = Field(default=None, max_length=100, pattern=ORGANIZATION_PATTERN)
    job_title: str | None = Field(default=None, max_length=100, pattern=ORGANIZATION_PATTERN)

    @field_validator("employee_number", "name")
    @classmethod
    def strip_required(cls, value: str) -> str:
        return value.strip()

    @field_validator("email", "department", "job_title")
    @classmethod
    def empty_to_none(cls, value: str | None) -> str | None:
        return value.strip() if value and value.strip() else None


class LoginRequest(BaseModel):
    employee_number: str = Field(min_length=1, max_length=30, pattern=EMPLOYEE_PATTERN)
    password: str = Field(min_length=1, max_length=128, pattern=PASSWORD_PATTERN)

    @field_validator("employee_number")
    @classmethod
    def strip_employee_number(cls, value: str) -> str:
        return value.strip()


class UserResponse(BaseModel):
    id: UUID
    employee_number: str
    name: str
    email: str | None
    department: str | None
    job_title: str | None
    status: str

