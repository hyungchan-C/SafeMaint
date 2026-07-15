from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UuidPrimaryKeyMixin


class User(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("employee_number", name="uq_users_employee_number"),
        CheckConstraint(
            "length(btrim(employee_number)) > 0",
            name="employee_number_nonempty",
        ),
        CheckConstraint(
            "employee_number = upper(btrim(employee_number))",
            name="employee_number_normalized",
        ),
        CheckConstraint("length(btrim(name)) > 0", name="name_nonempty"),
        CheckConstraint(
            "email IS NULL OR length(btrim(email)) > 0",
            name="email_nonempty",
        ),
        CheckConstraint(
            "auth_provider IN ('local', 'ldap', 'oidc')",
            name="auth_provider",
        ),
        CheckConstraint(
            "status IN ('active', 'locked', 'retired')",
            name="status",
        ),
        CheckConstraint(
            "failed_login_count >= 0",
            name="failed_login_count_nonnegative",
        ),
        CheckConstraint(
            "auth_provider <> 'local' OR "
            "(password_hash IS NOT NULL AND length(btrim(password_hash)) > 0)",
            name="local_password_hash",
        ),
    )

    employee_number: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255))
    department: Mapped[str | None] = mapped_column(String(100), index=True)
    job_title: Mapped[str | None] = mapped_column(String(100))
    auth_provider: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'local'")
    )
    password_hash: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'active'"), index=True
    )
    failed_login_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    role_assignments: Mapped[list["UserRole"]] = relationship(
        back_populates="user",
        foreign_keys="UserRole.user_id",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    roles: Mapped[list["Role"]] = relationship(
        secondary="user_roles",
        foreign_keys="[UserRole.user_id, UserRole.role_id]",
        viewonly=True,
        order_by="Role.code",
    )
    assigned_role_assignments: Mapped[list["UserRole"]] = relationship(
        back_populates="assigned_by_user",
        foreign_keys="UserRole.assigned_by_user_id",
    )
    site_assignments: Mapped[list["UserSite"]] = relationship(
        back_populates="user",
        foreign_keys="UserSite.user_id",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    assigned_site_assignments: Mapped[list["UserSite"]] = relationship(
        back_populates="assigned_by_user",
        foreign_keys="UserSite.assigned_by_user_id",
    )
    primary_site: Mapped[Optional["Site"]] = relationship(
        secondary="user_sites",
        foreign_keys="[UserSite.user_id, UserSite.site_id]",
        primaryjoin="and_(User.id == UserSite.user_id, UserSite.is_primary.is_(True))",
        secondaryjoin="Site.id == UserSite.site_id",
        uselist=False,
        viewonly=True,
    )


Index(
    "uq_users_email_lower",
    func.lower(User.__table__.c.email),
    unique=True,
    postgresql_where=User.__table__.c.email.is_not(None),
)


class Role(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint("code", name="uq_roles_code"),
        CheckConstraint("length(btrim(code)) > 0", name="code_nonempty"),
        CheckConstraint("code = lower(btrim(code))", name="code_normalized"),
        CheckConstraint("length(btrim(name)) > 0", name="name_nonempty"),
    )

    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )

    user_assignments: Mapped[list["UserRole"]] = relationship(
        back_populates="role", passive_deletes=True
    )


class UserRole(Base):
    __tablename__ = "user_roles"

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    role_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="RESTRICT"),
        primary_key=True,
        index=True,
    )
    assigned_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user: Mapped[User] = relationship(
        back_populates="role_assignments", foreign_keys=[user_id]
    )
    role: Mapped[Role] = relationship(back_populates="user_assignments")
    assigned_by_user: Mapped[Optional[User]] = relationship(
        back_populates="assigned_role_assignments",
        foreign_keys=[assigned_by_user_id],
    )


class UserSite(Base):
    __tablename__ = "user_sites"
    __table_args__ = (
        Index(
            "uq_user_sites_one_primary",
            "user_id",
            unique=True,
            postgresql_where=text("is_primary"),
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    site_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("sites.id", ondelete="RESTRICT"),
        primary_key=True,
        index=True,
    )
    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    assigned_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user: Mapped[User] = relationship(
        back_populates="site_assignments", foreign_keys=[user_id]
    )
    site: Mapped["Site"] = relationship(back_populates="user_assignments")
    assigned_by_user: Mapped[Optional[User]] = relationship(
        back_populates="assigned_site_assignments",
        foreign_keys=[assigned_by_user_id],
    )
