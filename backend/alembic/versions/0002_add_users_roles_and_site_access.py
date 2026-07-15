"""Add users, roles, and per-site access assignments.

Revision ID: 0004_users_roles_sites
Revises: 0003_align_user_accounts
Create Date: 2026-07-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0004_users_roles_sites"
down_revision: Union[str, Sequence[str], None] = "0003_align_user_accounts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


UUID = postgresql.UUID(as_uuid=True)


def _id_column() -> sa.Column:
    return sa.Column(
        "id",
        UUID,
        server_default=sa.text("gen_random_uuid()"),
        nullable=False,
    )


def _timestamp_columns() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def upgrade() -> None:
    op.execute("UPDATE users SET employee_number = upper(btrim(employee_number))")
    op.create_check_constraint("ck_users_employee_number_nonempty", "users", "length(btrim(employee_number)) > 0")
    op.create_check_constraint("ck_users_employee_number_normalized", "users", "employee_number = upper(btrim(employee_number))")
    op.create_check_constraint("ck_users_name_nonempty", "users", "length(btrim(name)) > 0")
    op.create_check_constraint("ck_users_email_nonempty", "users", "email IS NULL OR length(btrim(email)) > 0")
    op.create_check_constraint("ck_users_local_password_hash", "users", "auth_provider <> 'local' OR (password_hash IS NOT NULL AND length(btrim(password_hash)) > 0)")
    op.create_index("ix_users_department", "users", ["department"])
    op.create_index("ix_users_status", "users", ["status"])
    op.create_index(
        "uq_users_email_lower",
        "users",
        [sa.text("lower(email)")],
        unique=True,
        postgresql_where=sa.text("email IS NOT NULL"),
    )

    op.create_table(
        "roles",
        _id_column(),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        *_timestamp_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_roles"),
        sa.UniqueConstraint("code", name="uq_roles_code"),
        sa.CheckConstraint(
            "length(btrim(code)) > 0", name="ck_roles_code_nonempty"
        ),
        sa.CheckConstraint(
            "code = lower(btrim(code))", name="ck_roles_code_normalized"
        ),
        sa.CheckConstraint(
            "length(btrim(name)) > 0", name="ck_roles_name_nonempty"
        ),
    )

    op.create_table(
        "user_roles",
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("role_id", UUID, nullable=False),
        sa.Column("assigned_by_user_id", UUID, nullable=True),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_user_roles_user_id_users", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["role_id"], ["roles.id"], name="fk_user_roles_role_id_roles", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["assigned_by_user_id"],
            ["users.id"],
            name="fk_user_roles_assigned_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("user_id", "role_id", name="pk_user_roles"),
    )
    op.create_index("ix_user_roles_role_id", "user_roles", ["role_id"])

    op.create_table(
        "user_sites",
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("site_id", UUID, nullable=False),
        sa.Column(
            "is_primary",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("assigned_by_user_id", UUID, nullable=True),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_user_sites_user_id_users", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["site_id"], ["sites.id"], name="fk_user_sites_site_id_sites", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["assigned_by_user_id"],
            ["users.id"],
            name="fk_user_sites_assigned_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("user_id", "site_id", name="pk_user_sites"),
    )
    op.create_index("ix_user_sites_site_id", "user_sites", ["site_id"])
    op.create_index(
        "uq_user_sites_one_primary",
        "user_sites",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("is_primary"),
    )

    op.add_column("assessments", sa.Column("created_by_user_id", UUID, nullable=True))
    op.add_column("assessments", sa.Column("reviewed_by_user_id", UUID, nullable=True))
    op.create_foreign_key(
        "fk_assessments_created_by_user_id_users",
        "assessments",
        "users",
        ["created_by_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_assessments_reviewed_by_user_id_users",
        "assessments",
        "users",
        ["reviewed_by_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_assessments_created_by_user_id", "assessments", ["created_by_user_id"]
    )
    op.create_index(
        "ix_assessments_reviewed_by_user_id", "assessments", ["reviewed_by_user_id"]
    )

    op.add_column(
        "checklist_items", sa.Column("completed_by_user_id", UUID, nullable=True)
    )
    op.create_foreign_key(
        "fk_checklist_items_completed_by_user_id_users",
        "checklist_items",
        "users",
        ["completed_by_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_checklist_items_completed_by_user_id",
        "checklist_items",
        ["completed_by_user_id"],
    )

    op.add_column("audit_events", sa.Column("actor_user_id", UUID, nullable=True))
    op.create_foreign_key(
        "fk_audit_events_actor_user_id_users",
        "audit_events",
        "users",
        ["actor_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_audit_events_actor_user_id", "audit_events", ["actor_user_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_audit_events_actor_user_id", table_name="audit_events")
    op.drop_constraint(
        "fk_audit_events_actor_user_id_users", "audit_events", type_="foreignkey"
    )
    op.drop_column("audit_events", "actor_user_id")

    op.drop_index(
        "ix_checklist_items_completed_by_user_id", table_name="checklist_items"
    )
    op.drop_constraint(
        "fk_checklist_items_completed_by_user_id_users",
        "checklist_items",
        type_="foreignkey",
    )
    op.drop_column("checklist_items", "completed_by_user_id")

    op.drop_index("ix_assessments_reviewed_by_user_id", table_name="assessments")
    op.drop_index("ix_assessments_created_by_user_id", table_name="assessments")
    op.drop_constraint(
        "fk_assessments_reviewed_by_user_id_users",
        "assessments",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_assessments_created_by_user_id_users",
        "assessments",
        type_="foreignkey",
    )
    op.drop_column("assessments", "reviewed_by_user_id")
    op.drop_column("assessments", "created_by_user_id")

    op.drop_index("uq_user_sites_one_primary", table_name="user_sites")
    op.drop_index("ix_user_sites_site_id", table_name="user_sites")
    op.drop_table("user_sites")
    op.drop_index("ix_user_roles_role_id", table_name="user_roles")
    op.drop_table("user_roles")
    op.drop_table("roles")
    op.drop_index("uq_users_email_lower", table_name="users")
    op.drop_index("ix_users_status", table_name="users")
    op.drop_index("ix_users_department", table_name="users")
    op.drop_constraint("ck_users_local_password_hash", "users", type_="check")
    op.drop_constraint("ck_users_email_nonempty", "users", type_="check")
    op.drop_constraint("ck_users_name_nonempty", "users", type_="check")
    op.drop_constraint("ck_users_employee_number_normalized", "users", type_="check")
    op.drop_constraint("ck_users_employee_number_nonempty", "users", type_="check")
