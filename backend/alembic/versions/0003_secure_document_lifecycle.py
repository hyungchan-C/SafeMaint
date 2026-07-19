"""Add server sessions, RBAC permissions, and document lifecycle.

Revision ID: 0003_secure_documents
Revises: 0002_users_roles_sites
Create Date: 2026-07-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0003_secure_documents"
down_revision: Union[str, Sequence[str], None] = "0002_users_roles_sites"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)


def _id_column() -> sa.Column:
    return sa.Column(
        "id", UUID, server_default=sa.text("gen_random_uuid()"), nullable=False
    )


def _timestamps() -> tuple[sa.Column, sa.Column]:
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
    op.create_table(
        "permissions",
        _id_column(),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_permissions"),
        sa.UniqueConstraint("code", name="uq_permissions_code"),
        sa.CheckConstraint(
            "code = lower(btrim(code))", name="ck_permissions_code_normalized"
        ),
    )
    op.create_table(
        "role_permissions",
        sa.Column("role_id", UUID, nullable=False),
        sa.Column("permission_id", UUID, nullable=False),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["roles.id"],
            name="fk_role_permissions_role_id_roles",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["permission_id"],
            ["permissions.id"],
            name="fk_role_permissions_permission_id_permissions",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("role_id", "permission_id", name="pk_role_permissions"),
    )
    op.create_index(
        "ix_role_permissions_permission_id", "role_permissions", ["permission_id"]
    )
    op.create_table(
        "auth_sessions",
        _id_column(),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_auth_sessions_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_auth_sessions"),
        sa.UniqueConstraint("token_hash", name="uq_auth_sessions_token_hash"),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])

    op.create_table(
        "document_types",
        _id_column(),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("scope", sa.String(length=20), nullable=False),
        sa.Column(
            "is_exportable",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_document_types"),
        sa.UniqueConstraint("code", name="uq_document_types_code"),
        sa.CheckConstraint(
            "scope IN ('public', 'company')", name="ck_document_types_scope"
        ),
    )
    op.create_table(
        "public_rag_packages",
        _id_column(),
        sa.Column("package_version", sa.String(length=100), nullable=False),
        sa.Column("schema_version", sa.String(length=50), nullable=False),
        sa.Column("embedding_model", sa.String(length=200), nullable=False),
        sa.Column("embedding_dimension", sa.Integer(), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=30),
            server_default=sa.text("'importing'"),
            nullable=False,
        ),
        sa.Column("imported_by_user_id", UUID, nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["imported_by_user_id"],
            ["users.id"],
            name="fk_public_rag_packages_imported_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_public_rag_packages"),
        sa.UniqueConstraint(
            "package_version", name="uq_public_rag_packages_version"
        ),
        sa.CheckConstraint(
            "status IN ('importing', 'active', 'superseded', 'failed')",
            name="ck_public_rag_packages_status",
        ),
    )
    op.create_index(
        "ix_public_rag_packages_status", "public_rag_packages", ["status"]
    )
    op.create_index(
        "uq_public_rag_packages_one_active",
        "public_rag_packages",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.add_column(
        "documents", sa.Column("document_type_code", sa.String(length=50), nullable=True)
    )
    op.add_column(
        "documents",
        sa.Column(
            "lifecycle_status",
            sa.String(length=30),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
    )
    op.add_column("documents", sa.Column("site_id", UUID, nullable=True))
    op.add_column("documents", sa.Column("created_by_user_id", UUID, nullable=True))
    op.add_column("documents", sa.Column("public_package_id", UUID, nullable=True))
    op.add_column(
        "documents", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_documents_document_type_code_document_types",
        "documents",
        "document_types",
        ["document_type_code"],
        ["code"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_documents_site_id_sites",
        "documents",
        "sites",
        ["site_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_documents_created_by_user_id_users",
        "documents",
        "users",
        ["created_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_documents_public_package_id_public_rag_packages",
        "documents",
        "public_rag_packages",
        ["public_package_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_documents_document_type_code", "documents", ["document_type_code"]
    )
    op.create_index("ix_documents_lifecycle_status", "documents", ["lifecycle_status"])
    op.create_index("ix_documents_site_id", "documents", ["site_id"])
    op.create_index(
        "ix_documents_created_by_user_id", "documents", ["created_by_user_id"]
    )
    op.create_index(
        "ix_documents_public_package_id", "documents", ["public_package_id"]
    )
    op.create_check_constraint(
        "ck_documents_lifecycle_status",
        "documents",
        "lifecycle_status IN ('pending', 'processing', 'review_required', "
        "'active', 'failed', 'deleted')",
    )

    op.execute(
        """
        INSERT INTO document_types
            (code, name, scope, is_exportable)
        VALUES
            ('public_incident', 'Public incident', 'public', true),
            ('public_law', 'Public law', 'public', true),
            ('public_guide', 'Public safety guide', 'public', true),
            ('public_media', 'Public safety media', 'public', true),
            ('company_policy', 'Company policy', 'company', false),
            ('equipment_manual', 'Equipment manual', 'company', false),
            ('component_manual', 'Component manual', 'company', false),
            ('legacy_document', 'Legacy document', 'company', false)
        """
    )
    op.execute(
        """
        UPDATE documents
        SET document_type_code = CASE
            WHEN source_type = 'incident' THEN 'public_incident'
            WHEN source_type = 'manual' THEN 'equipment_manual'
            ELSE 'legacy_document'
        END
        """
    )
    op.alter_column("documents", "document_type_code", nullable=False)

    op.create_table(
        "document_versions",
        _id_column(),
        sa.Column("document_id", UUID, nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("original_filename", sa.String(length=500), nullable=False),
        sa.Column("stored_filename", sa.String(length=100), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=30),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("uploaded_by_user_id", UUID, nullable=True),
        sa.Column("approved_by_user_id", UUID, nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_document_versions_document_id_documents",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by_user_id"],
            ["users.id"],
            name="fk_document_versions_uploaded_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["approved_by_user_id"],
            ["users.id"],
            name="fk_document_versions_approved_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_document_versions"),
        sa.UniqueConstraint(
            "document_id", "version_number", name="uq_document_versions_number"
        ),
        sa.UniqueConstraint("stored_filename", name="uq_document_versions_stored_filename"),
        sa.CheckConstraint(
            "version_number >= 1", name="ck_document_versions_version_number_positive"
        ),
        sa.CheckConstraint("file_size > 0", name="ck_document_versions_file_size_positive"),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'review_required', 'active', "
            "'superseded', 'failed', 'ocr_required', 'deleted')",
            name="ck_document_versions_status",
        ),
    )
    op.create_index("ix_document_versions_document_id", "document_versions", ["document_id"])
    op.create_index("ix_document_versions_sha256", "document_versions", ["sha256"])
    op.create_index("ix_document_versions_status", "document_versions", ["status"])
    op.create_index("ix_document_versions_is_active", "document_versions", ["is_active"])
    op.create_index(
        "uq_document_versions_one_active",
        "document_versions",
        ["document_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )

    op.add_column("documents", sa.Column("current_version_id", UUID, nullable=True))
    op.create_foreign_key(
        "fk_documents_current_version_id_document_versions",
        "documents",
        "document_versions",
        ["current_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_documents_current_version_id", "documents", ["current_version_id"]
    )

    op.add_column("document_chunks", sa.Column("document_version_id", UUID, nullable=True))
    op.create_foreign_key(
        "fk_document_chunks_document_version_id_document_versions",
        "document_chunks",
        "document_versions",
        ["document_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_document_chunks_document_version_id",
        "document_chunks",
        ["document_version_id"],
    )
    op.drop_constraint(
        "uq_document_chunks_document_index", "document_chunks", type_="unique"
    )
    op.create_unique_constraint(
        "uq_document_chunks_version_index",
        "document_chunks",
        ["document_version_id", "chunk_index"],
    )

    op.create_table(
        "document_processing_jobs",
        _id_column(),
        sa.Column("document_version_id", UUID, nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'queued'"),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.id"],
            name="fk_doc_jobs_version_id_versions",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_document_processing_jobs"),
        sa.UniqueConstraint(
            "document_version_id", name="uq_document_processing_jobs_version"
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'processing', 'completed', 'failed')",
            name="ck_document_processing_jobs_status",
        ),
        sa.CheckConstraint(
            "attempts >= 0", name="ck_document_processing_jobs_attempts_nonnegative"
        ),
    )
    op.create_index(
        "ix_document_processing_jobs_document_version_id",
        "document_processing_jobs",
        ["document_version_id"],
    )
    op.create_index(
        "ix_document_processing_jobs_status", "document_processing_jobs", ["status"]
    )

    op.add_column(
        "audit_events",
        sa.Column("success", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )
    op.add_column("audit_events", sa.Column("request_id", sa.String(length=100), nullable=True))
    op.add_column("audit_events", sa.Column("document_version_id", UUID, nullable=True))
    op.create_foreign_key(
        "fk_audit_events_document_version_id_document_versions",
        "audit_events",
        "document_versions",
        ["document_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_audit_events_request_id", "audit_events", ["request_id"])
    op.create_index(
        "ix_audit_events_document_version_id", "audit_events", ["document_version_id"]
    )

    op.execute(
        """
        INSERT INTO roles (code, name, description)
        VALUES ('document_manager', 'Document manager', 'Approves and manages document versions.')
        ON CONFLICT (code) DO UPDATE SET is_active = true, updated_at = now()
        """
    )
    op.execute(
        """
        INSERT INTO permissions (code, name, description)
        VALUES
            ('document.read', 'Read documents', 'Read active accessible documents.'),
            ('document.upload', 'Upload documents', 'Upload company documents.'),
            ('document.update', 'Update documents', 'Update document metadata.'),
            ('document.replace', 'Replace documents', 'Create replacement versions.'),
            ('document.delete', 'Delete documents', 'Soft-delete documents.'),
            ('document.approve', 'Approve documents', 'Activate reviewed versions.'),
            ('document.restore', 'Restore documents', 'Restore soft-deleted documents.'),
            ('role.manage', 'Manage roles', 'Manage user roles and permissions.'),
            ('audit.read', 'Read audit logs', 'Read audit events.'),
            ('public_package.import', 'Import public packages', 'Import signed public RAG packages.')
        ON CONFLICT (code) DO UPDATE
        SET name = EXCLUDED.name, description = EXCLUDED.description,
            is_active = true, updated_at = now()
        """
    )
    op.execute(
        """
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id
        FROM roles r
        CROSS JOIN permissions p
        WHERE
            (r.code = 'worker' AND p.code IN ('document.read')) OR
            (r.code = 'safety_manager' AND p.code IN
                ('document.read', 'document.upload', 'document.update', 'document.replace')) OR
            (r.code = 'document_manager' AND p.code IN
                ('document.read', 'document.upload', 'document.update', 'document.replace',
                 'document.delete', 'document.approve', 'document.restore', 'audit.read')) OR
            (r.code = 'admin')
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index("ix_audit_events_document_version_id", table_name="audit_events")
    op.drop_index("ix_audit_events_request_id", table_name="audit_events")
    op.drop_constraint(
        "fk_audit_events_document_version_id_document_versions",
        "audit_events",
        type_="foreignkey",
    )
    op.drop_column("audit_events", "document_version_id")
    op.drop_column("audit_events", "request_id")
    op.drop_column("audit_events", "success")
    op.drop_index("ix_document_processing_jobs_status", table_name="document_processing_jobs")
    op.drop_index(
        "ix_document_processing_jobs_document_version_id",
        table_name="document_processing_jobs",
    )
    op.drop_table("document_processing_jobs")
    op.drop_index("ix_document_chunks_document_version_id", table_name="document_chunks")
    op.drop_constraint(
        "uq_document_chunks_version_index", "document_chunks", type_="unique"
    )
    op.create_unique_constraint(
        "uq_document_chunks_document_index",
        "document_chunks",
        ["document_id", "chunk_index"],
    )
    op.drop_constraint(
        "fk_document_chunks_document_version_id_document_versions",
        "document_chunks",
        type_="foreignkey",
    )
    op.drop_column("document_chunks", "document_version_id")
    op.drop_index("ix_documents_current_version_id", table_name="documents")
    op.drop_constraint(
        "fk_documents_current_version_id_document_versions",
        "documents",
        type_="foreignkey",
    )
    op.drop_column("documents", "current_version_id")
    op.drop_index("uq_document_versions_one_active", table_name="document_versions")
    op.drop_index("ix_document_versions_is_active", table_name="document_versions")
    op.drop_index("ix_document_versions_status", table_name="document_versions")
    op.drop_index("ix_document_versions_sha256", table_name="document_versions")
    op.drop_index("ix_document_versions_document_id", table_name="document_versions")
    op.drop_table("document_versions")
    op.drop_constraint("ck_documents_lifecycle_status", "documents", type_="check")
    for index_name in (
        "ix_documents_public_package_id",
        "ix_documents_created_by_user_id",
        "ix_documents_site_id",
        "ix_documents_lifecycle_status",
        "ix_documents_document_type_code",
    ):
        op.drop_index(index_name, table_name="documents")
    for constraint_name in (
        "fk_documents_public_package_id_public_rag_packages",
        "fk_documents_created_by_user_id_users",
        "fk_documents_site_id_sites",
        "fk_documents_document_type_code_document_types",
    ):
        op.drop_constraint(constraint_name, "documents", type_="foreignkey")
    for column_name in (
        "deleted_at",
        "public_package_id",
        "created_by_user_id",
        "site_id",
        "lifecycle_status",
        "document_type_code",
    ):
        op.drop_column("documents", column_name)
    op.drop_index("uq_public_rag_packages_one_active", table_name="public_rag_packages")
    op.drop_index("ix_public_rag_packages_status", table_name="public_rag_packages")
    op.drop_table("public_rag_packages")
    op.drop_table("document_types")
    op.drop_index("ix_auth_sessions_expires_at", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_index("ix_role_permissions_permission_id", table_name="role_permissions")
    op.drop_table("role_permissions")
    op.drop_table("permissions")
