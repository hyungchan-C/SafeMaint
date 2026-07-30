"""Add durable PDF processing progress fields.

Revision ID: 0010_processing_progress
Revises: 0009_processing_metadata
Create Date: 2026-07-24
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0010_processing_progress"
down_revision: Union[str, Sequence[str], None] = "0009_processing_metadata"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "document_processing_jobs",
        sa.Column(
            "processing_stage",
            sa.String(length=30),
            nullable=False,
            server_default=sa.text("'queued'"),
        ),
    )
    op.add_column(
        "document_processing_jobs",
        sa.Column(
            "progress_percent",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("15"),
        ),
    )
    op.add_column(
        "document_processing_jobs",
        sa.Column(
            "progress_message",
            sa.String(length=500),
            nullable=False,
            server_default=sa.text("'PDF 처리 대기 중'"),
        ),
    )
    op.add_column(
        "document_processing_jobs",
        sa.Column(
            "progress_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "document_processing_jobs",
        sa.Column("progress_updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_document_processing_jobs_processing_stage",
        "document_processing_jobs",
        "processing_stage IN ('queued', 'inspecting', 'extracting', "
        "'chunking', 'embedding', 'persisting', 'validating', "
        "'review_required', 'completed', 'failed', 'ocr_required')",
    )
    op.create_check_constraint(
        "ck_document_processing_jobs_progress_percent_range",
        "document_processing_jobs",
        "progress_percent >= 0 AND progress_percent <= 100",
    )
    op.create_index(
        "ix_document_processing_jobs_processing_stage",
        "document_processing_jobs",
        ["processing_stage"],
    )
    op.execute(
        """
        UPDATE document_processing_jobs
        SET processing_stage = CASE
                WHEN status = 'completed' THEN 'review_required'
                WHEN status = 'failed' THEN 'failed'
                WHEN status = 'processing' THEN 'inspecting'
                ELSE 'queued'
            END,
            progress_percent = CASE
                WHEN status = 'completed' THEN 100
                ELSE 15
            END,
            progress_message = CASE
                WHEN status = 'completed'
                    THEN 'PDF 처리가 완료되었습니다. 관리자 승인이 필요합니다.'
                WHEN status = 'failed'
                    THEN 'PDF 처리에 실패했습니다.'
                WHEN status = 'processing'
                    THEN 'PDF 파일을 검사하고 있습니다.'
                ELSE 'PDF 처리 대기 중'
            END,
            progress_updated_at = updated_at
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_document_processing_jobs_processing_stage",
        table_name="document_processing_jobs",
    )
    op.drop_constraint(
        "ck_document_processing_jobs_progress_percent_range",
        "document_processing_jobs",
        type_="check",
    )
    op.drop_constraint(
        "ck_document_processing_jobs_processing_stage",
        "document_processing_jobs",
        type_="check",
    )
    op.drop_column("document_processing_jobs", "progress_updated_at")
    op.drop_column("document_processing_jobs", "progress_metadata")
    op.drop_column("document_processing_jobs", "progress_message")
    op.drop_column("document_processing_jobs", "progress_percent")
    op.drop_column("document_processing_jobs", "processing_stage")
