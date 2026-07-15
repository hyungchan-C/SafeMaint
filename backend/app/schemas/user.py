from datetime import datetime
from enum import Enum
import re
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)


EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AuthProvider(str, Enum):
    LOCAL = "local"
    LDAP = "ldap"
    OIDC = "oidc"


class UserStatus(str, Enum):
    ACTIVE = "active"
    LOCKED = "locked"
    RETIRED = "retired"


class UserCreate(BaseModel):
    employee_number: str = Field(min_length=1, max_length=30)
    name: str = Field(min_length=1, max_length=100)
    email: str | None = Field(default=None, max_length=255)
    department: str | None = Field(default=None, max_length=100)
    job_title: str | None = Field(default=None, max_length=100)
    auth_provider: AuthProvider = AuthProvider.LOCAL
    password: SecretStr | None = Field(default=None, min_length=12, max_length=128)

    @field_validator("employee_number")
    @classmethod
    def normalize_employee_number(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("employee_number must not be blank")
        return normalized

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("name must not be blank")
        return normalized

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if not EMAIL_PATTERN.fullmatch(normalized):
            raise ValueError("email must be a valid address")
        return normalized

    @field_validator("department", "job_title")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_password_policy(self) -> "UserCreate":
        if self.auth_provider is AuthProvider.LOCAL and self.password is None:
            raise ValueError("local users require a password")
        if self.auth_provider is not AuthProvider.LOCAL and self.password is not None:
            raise ValueError("external authentication users must not include a password")
        return self


class UserUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    email: str | None = Field(default=None, max_length=255)
    department: str | None = Field(default=None, max_length=100)
    job_title: str | None = Field(default=None, max_length=100)
    status: UserStatus | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("name must not be blank")
        return normalized

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str | None) -> str | None:
        return UserCreate.normalize_email(value)

    @field_validator("department", "job_title")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return UserCreate.normalize_optional_text(value)


class RoleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    name: str
    description: str | None
    is_active: bool


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    employee_number: str
    name: str
    email: str | None
    department: str | None
    job_title: str | None
    auth_provider: AuthProvider
    status: UserStatus
    failed_login_count: int
    locked_until: datetime | None
    last_login_at: datetime | None
    password_changed_at: datetime | None
    deactivated_at: datetime | None
    created_at: datetime
    updated_at: datetime
    roles: list[RoleResponse] = Field(default_factory=list)


class UserRoleAssignment(BaseModel):
    role_id: UUID


class UserSiteAssignment(BaseModel):
    site_id: UUID
    is_primary: bool = False


class UserRoleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: UUID
    role_id: UUID
    assigned_by_user_id: UUID | None
    assigned_at: datetime


class UserSiteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: UUID
    site_id: UUID
    is_primary: bool
    assigned_by_user_id: UUID | None
    assigned_at: datetime
