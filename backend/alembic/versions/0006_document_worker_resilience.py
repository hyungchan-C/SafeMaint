"""Add retry scheduling and heartbeat fields to document jobs.

Revision ID: 0006_worker_resilience
Revises: 0005_defer_auth_integration
Create Date: 2026-07-19
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006_worker_resilience"
down_revision: Union[str, Sequence[str], None] = "0005_defer_auth_integration"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "document_processing_jobs",
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "document_processing_jobs",
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_document_processing_jobs_heartbeat_at",
        "document_processing_jobs",
        ["heartbeat_at"],
    )
    op.create_index(
        "ix_document_processing_jobs_next_attempt_at",
        "document_processing_jobs",
        ["next_attempt_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_document_processing_jobs_next_attempt_at",
        table_name="document_processing_jobs",
    )
    op.drop_index(
        "ix_document_processing_jobs_heartbeat_at",
        table_name="document_processing_jobs",
    )
    op.drop_column("document_processing_jobs", "next_attempt_at")
    op.drop_column("document_processing_jobs", "heartbeat_at")
