"""Align users with the employee account design.

Revision ID: 0003_align_user_accounts
Revises: 0002_add_users
Create Date: 2026-07-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003_align_user_accounts"
down_revision: Union[str, Sequence[str], None] = "0002_add_users"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("employee_number", sa.String(30), nullable=True))
    op.add_column("users", sa.Column("name", sa.String(100), nullable=True))
    op.add_column("users", sa.Column("email", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("department", sa.String(100), nullable=True))
    op.add_column("users", sa.Column("job_title", sa.String(100), nullable=True))
    op.add_column("users", sa.Column("auth_provider", sa.String(20), server_default=sa.text("'local'"), nullable=False))
    op.add_column("users", sa.Column("status", sa.String(20), server_default=sa.text("'active'"), nullable=False))
    op.add_column("users", sa.Column("failed_login_count", sa.Integer(), server_default=sa.text("0"), nullable=False))
    op.add_column("users", sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True))

    op.execute("UPDATE users SET employee_number = username, name = display_name")
    op.execute("UPDATE users SET status = CASE WHEN is_active THEN 'active' ELSE 'retired' END")
    op.alter_column("users", "employee_number", nullable=False)
    op.alter_column("users", "name", nullable=False)
    op.alter_column("users", "password_hash", existing_type=sa.String(300), type_=sa.String(255), nullable=True)
    op.drop_index("ix_users_username", table_name="users")
    op.drop_constraint("uq_users_username", "users", type_="unique")
    op.drop_column("users", "username")
    op.drop_column("users", "display_name")
    op.drop_column("users", "is_active")

    op.create_unique_constraint("uq_users_employee_number", "users", ["employee_number"])
    op.create_unique_constraint("uq_users_email", "users", ["email"])
    op.create_index("ix_users_employee_number", "users", ["employee_number"], unique=True)
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_check_constraint("ck_users_auth_provider", "users", "auth_provider IN ('local', 'ldap', 'oidc')")
    op.create_check_constraint("ck_users_status", "users", "status IN ('active', 'locked', 'retired')")
    op.create_check_constraint("ck_users_failed_login_count", "users", "failed_login_count >= 0")


def downgrade() -> None:
    op.add_column("users", sa.Column("username", sa.String(50), nullable=True))
    op.add_column("users", sa.Column("display_name", sa.String(100), nullable=True))
    op.add_column("users", sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False))
    op.execute("UPDATE users SET username = employee_number, display_name = name, is_active = (status = 'active')")
    op.alter_column("users", "username", nullable=False)
    op.alter_column("users", "display_name", nullable=False)
    op.drop_constraint("ck_users_failed_login_count", "users", type_="check")
    op.drop_constraint("ck_users_status", "users", type_="check")
    op.drop_constraint("ck_users_auth_provider", "users", type_="check")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_index("ix_users_employee_number", table_name="users")
    op.drop_constraint("uq_users_email", "users", type_="unique")
    op.drop_constraint("uq_users_employee_number", "users", type_="unique")
    for column in ("deactivated_at", "password_changed_at", "last_login_at", "locked_until", "failed_login_count", "status", "auth_provider", "job_title", "department", "email", "name", "employee_number"):
        op.drop_column("users", column)
    op.alter_column("users", "password_hash", existing_type=sa.String(255), type_=sa.String(300), nullable=False)
    op.create_unique_constraint("uq_users_username", "users", ["username"])
    op.create_index("ix_users_username", "users", ["username"], unique=True)
