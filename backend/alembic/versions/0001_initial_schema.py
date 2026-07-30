"""Create the initial SafeMaint schema.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-07-14
"""
from typing import Sequence, Union

from alembic import op
from pgvector.sqlalchemy import Vector
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0001_initial_schema"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


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
    # This also covers an existing PostgreSQL volume whose init scripts ran before
    # pgvector was configured. Table and index ownership stays with Alembic.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "reference_codes",
        _id_column(),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        *_timestamp_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_reference_codes"),
        sa.UniqueConstraint(
            "category", "code", name="uq_reference_codes_category_code"
        ),
    )
    op.create_index(
        "ix_reference_codes_category", "reference_codes", ["category"]
    )

    op.create_table(
        "sites",
        _id_column(),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("metadata", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        *_timestamp_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_sites"),
        sa.UniqueConstraint("code", name="uq_sites_code"),
    )

    op.create_table(
        "documents",
        _id_column(),
        sa.Column("external_id", sa.String(length=200), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column("publisher", sa.String(length=200), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("revision", sa.String(length=100), nullable=True),
        sa.Column("published_at", sa.Date(), nullable=True),
        sa.Column("access_level", sa.String(length=20), server_default=sa.text("'public'"), nullable=False),
        sa.Column("file_sha256", sa.String(length=64), nullable=True),
        sa.Column("metadata", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        *_timestamp_columns(),
        sa.CheckConstraint(
            "access_level IN ('public', 'restricted', 'private')",
            name="access_level",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_documents"),
        sa.UniqueConstraint("external_id", name="uq_documents_external_id"),
        sa.UniqueConstraint("file_sha256", name="uq_documents_file_sha256"),
    )
    op.create_index("ix_documents_source_type", "documents", ["source_type"])

    op.create_table(
        "audit_events",
        _id_column(),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("actor_id", sa.String(length=200), nullable=True),
        sa.Column("entity_type", sa.String(length=100), nullable=False),
        sa.Column("entity_id", UUID, nullable=True),
        sa.Column("payload", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_events"),
    )
    op.create_index("ix_audit_events_created_at", "audit_events", ["created_at"])
    op.create_index("ix_audit_events_event_type", "audit_events", ["event_type"])
    op.create_index(
        "ix_audit_events_entity", "audit_events", ["entity_type", "entity_id"]
    )

    op.create_table(
        "equipment",
        _id_column(),
        sa.Column("site_id", UUID, nullable=False),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("equipment_type", sa.String(length=100), nullable=False),
        sa.Column("manufacturer", sa.String(length=200), nullable=True),
        sa.Column("model_number", sa.String(length=200), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("metadata", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        *_timestamp_columns(),
        sa.ForeignKeyConstraint(
            ["site_id"], ["sites.id"], name="fk_equipment_site_id_sites", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_equipment"),
        sa.UniqueConstraint("site_id", "code", name="uq_equipment_site_code"),
    )
    op.create_index("ix_equipment_equipment_type", "equipment", ["equipment_type"])
    op.create_index("ix_equipment_site_id", "equipment", ["site_id"])

    op.create_table(
        "document_chunks",
        _id_column(),
        sa.Column("document_id", UUID, nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("section_path", JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("metadata", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("embedding", Vector(), nullable=True),
        sa.Column("embedding_model", sa.String(length=200), nullable=True),
        sa.Column("embedding_dimension", sa.Integer(), nullable=True),
        sa.Column("embedding_status", sa.String(length=20), server_default=sa.text("'pending'"), nullable=False),
        *_timestamp_columns(),
        sa.CheckConstraint(
            "chunk_index >= 0", name="chunk_index_nonnegative"
        ),
        sa.CheckConstraint(
            "page_number IS NULL OR page_number >= 1",
            name="page_number_positive",
        ),
        sa.CheckConstraint(
            "page_start IS NULL OR page_start >= 1",
            name="page_start_positive",
        ),
        sa.CheckConstraint(
            "page_end IS NULL OR page_end >= page_start",
            name="page_range",
        ),
        sa.CheckConstraint(
            "embedding_status IN ('pending', 'ready', 'failed', 'skipped')",
            name="embedding_status",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_document_chunks_document_id_documents",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_document_chunks"),
        sa.UniqueConstraint(
            "document_id", "chunk_index", name="uq_document_chunks_document_index"
        ),
    )
    op.create_index("ix_document_chunks_content_hash", "document_chunks", ["content_hash"])
    op.create_index("ix_document_chunks_document_id", "document_chunks", ["document_id"])
    op.create_index(
        "ix_document_chunks_metadata_gin",
        "document_chunks",
        ["metadata"],
        postgresql_using="gin",
    )

    op.create_table(
        "components",
        _id_column(),
        sa.Column("equipment_id", UUID, nullable=False),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("component_type", sa.String(length=100), nullable=False),
        sa.Column("manufacturer", sa.String(length=200), nullable=True),
        sa.Column("part_number", sa.String(length=200), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("metadata", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        *_timestamp_columns(),
        sa.ForeignKeyConstraint(
            ["equipment_id"],
            ["equipment.id"],
            name="fk_components_equipment_id_equipment",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_components"),
        sa.UniqueConstraint(
            "equipment_id", "code", name="uq_components_equipment_code"
        ),
    )
    op.create_index("ix_components_component_type", "components", ["component_type"])
    op.create_index("ix_components_equipment_id", "components", ["equipment_id"])
    op.create_index("ix_components_part_number", "components", ["part_number"])

    op.create_table(
        "assessments",
        _id_column(),
        sa.Column("site_id", UUID, nullable=True),
        sa.Column("equipment_id", UUID, nullable=True),
        sa.Column("component_id", UUID, nullable=True),
        sa.Column("site_name", sa.String(length=200), nullable=False),
        sa.Column("equipment_name", sa.String(length=200), nullable=False),
        sa.Column("component_name", sa.String(length=200), nullable=True),
        sa.Column("task_type", sa.String(length=100), nullable=False),
        sa.Column("energy_sources", JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=30), server_default=sa.text("'draft'"), nullable=False),
        sa.Column("engine_version", sa.String(length=100), nullable=False),
        sa.Column("rule_version", sa.String(length=100), nullable=False),
        sa.Column("request_snapshot", JSONB, nullable=False),
        sa.Column("reviewed_by", sa.String(length=200), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamp_columns(),
        sa.CheckConstraint(
            "status IN ('draft', 'pending_review', 'approved', 'rejected')",
            name="status",
        ),
        sa.ForeignKeyConstraint(
            ["site_id"], ["sites.id"], name="fk_assessments_site_id_sites", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["equipment_id"],
            ["equipment.id"],
            name="fk_assessments_equipment_id_equipment",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["component_id"],
            ["components.id"],
            name="fk_assessments_component_id_components",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_assessments"),
    )
    op.create_index("ix_assessments_component_id", "assessments", ["component_id"])
    op.create_index("ix_assessments_equipment_id", "assessments", ["equipment_id"])
    op.create_index("ix_assessments_site_id", "assessments", ["site_id"])
    op.create_index("ix_assessments_status", "assessments", ["status"])
    op.create_index("ix_assessments_task_type", "assessments", ["task_type"])
    op.create_index("ix_assessments_created_at", "assessments", ["created_at"])

    op.create_table(
        "assessment_hazards",
        _id_column(),
        sa.Column("assessment_id", UUID, nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("accident_type", sa.String(length=100), nullable=False),
        sa.Column("likelihood", sa.Integer(), nullable=False),
        sa.Column("severity", sa.Integer(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("risk_level", sa.String(length=20), nullable=False),
        sa.Column("safety_actions", JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
        *_timestamp_columns(),
        sa.CheckConstraint(
            "likelihood BETWEEN 1 AND 4",
            name="likelihood_range",
        ),
        sa.CheckConstraint(
            "severity BETWEEN 1 AND 4",
            name="severity_range",
        ),
        sa.CheckConstraint(
            "score BETWEEN 1 AND 16", name="score_range"
        ),
        sa.CheckConstraint(
            "risk_level IN ('low', 'medium', 'high')",
            name="risk_level",
        ),
        sa.ForeignKeyConstraint(
            ["assessment_id"],
            ["assessments.id"],
            name="fk_assessment_hazards_assessment_id_assessments",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_assessment_hazards"),
    )
    op.create_index(
        "ix_assessment_hazards_accident_type", "assessment_hazards", ["accident_type"]
    )
    op.create_index(
        "ix_assessment_hazards_assessment_id", "assessment_hazards", ["assessment_id"]
    )
    op.create_index(
        "ix_assessment_hazards_risk_level", "assessment_hazards", ["risk_level"]
    )

    op.create_table(
        "checklist_items",
        _id_column(),
        sa.Column("assessment_id", UUID, nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("is_completed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("completed_by", sa.String(length=200), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamp_columns(),
        sa.CheckConstraint(
            "sequence >= 1", name="sequence_positive"
        ),
        sa.ForeignKeyConstraint(
            ["assessment_id"],
            ["assessments.id"],
            name="fk_checklist_items_assessment_id_assessments",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_checklist_items"),
        sa.UniqueConstraint(
            "assessment_id", "sequence", name="uq_checklist_items_assessment_sequence"
        ),
    )
    op.create_index("ix_checklist_items_assessment_id", "checklist_items", ["assessment_id"])
    op.create_index("ix_checklist_items_is_completed", "checklist_items", ["is_completed"])

    op.create_table(
        "assessment_evidence",
        _id_column(),
        sa.Column("assessment_id", UUID, nullable=False),
        sa.Column("chunk_id", UUID, nullable=False),
        sa.Column("retrieval_rank", sa.Integer(), nullable=False),
        sa.Column("retrieval_score", sa.Float(), nullable=True),
        sa.Column("reranker_score", sa.Float(), nullable=True),
        sa.Column("used_in_answer", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        *_timestamp_columns(),
        sa.CheckConstraint(
            "retrieval_rank >= 1",
            name="retrieval_rank_positive",
        ),
        sa.ForeignKeyConstraint(
            ["assessment_id"],
            ["assessments.id"],
            name="fk_assessment_evidence_assessment_id_assessments",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id"],
            ["document_chunks.id"],
            name="fk_assessment_evidence_chunk_id_document_chunks",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_assessment_evidence"),
        sa.UniqueConstraint(
            "assessment_id", "chunk_id", name="uq_assessment_evidence_assessment_chunk"
        ),
    )
    op.create_index(
        "ix_assessment_evidence_assessment_id", "assessment_evidence", ["assessment_id"]
    )
    op.create_index("ix_assessment_evidence_chunk_id", "assessment_evidence", ["chunk_id"])


def downgrade() -> None:
    op.drop_index("ix_assessment_evidence_chunk_id", table_name="assessment_evidence")
    op.drop_index("ix_assessment_evidence_assessment_id", table_name="assessment_evidence")
    op.drop_table("assessment_evidence")
    op.drop_index("ix_checklist_items_is_completed", table_name="checklist_items")
    op.drop_index("ix_checklist_items_assessment_id", table_name="checklist_items")
    op.drop_table("checklist_items")
    op.drop_index("ix_assessment_hazards_risk_level", table_name="assessment_hazards")
    op.drop_index("ix_assessment_hazards_assessment_id", table_name="assessment_hazards")
    op.drop_index("ix_assessment_hazards_accident_type", table_name="assessment_hazards")
    op.drop_table("assessment_hazards")
    op.drop_index("ix_assessments_created_at", table_name="assessments")
    op.drop_index("ix_assessments_task_type", table_name="assessments")
    op.drop_index("ix_assessments_status", table_name="assessments")
    op.drop_index("ix_assessments_site_id", table_name="assessments")
    op.drop_index("ix_assessments_equipment_id", table_name="assessments")
    op.drop_index("ix_assessments_component_id", table_name="assessments")
    op.drop_table("assessments")
    op.drop_index("ix_components_part_number", table_name="components")
    op.drop_index("ix_components_equipment_id", table_name="components")
    op.drop_index("ix_components_component_type", table_name="components")
    op.drop_table("components")
    op.drop_index("ix_document_chunks_metadata_gin", table_name="document_chunks")
    op.drop_index("ix_document_chunks_document_id", table_name="document_chunks")
    op.drop_index("ix_document_chunks_content_hash", table_name="document_chunks")
    op.drop_table("document_chunks")
    op.drop_index("ix_equipment_site_id", table_name="equipment")
    op.drop_index("ix_equipment_equipment_type", table_name="equipment")
    op.drop_table("equipment")
    op.drop_index("ix_audit_events_entity", table_name="audit_events")
    op.drop_index("ix_audit_events_event_type", table_name="audit_events")
    op.drop_index("ix_audit_events_created_at", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_documents_source_type", table_name="documents")
    op.drop_table("documents")
    op.drop_table("sites")
    op.drop_index("ix_reference_codes_category", table_name="reference_codes")
    op.drop_table("reference_codes")
    # Deliberately keep the shared vector extension installed.
