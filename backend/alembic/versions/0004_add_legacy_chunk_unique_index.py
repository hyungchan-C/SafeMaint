"""Keep legacy incident chunk upserts unique after versioned documents.

Revision ID: 0004_legacy_chunk_index
Revises: 0003_secure_documents
Create Date: 2026-07-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004_legacy_chunk_index"
down_revision: Union[str, Sequence[str], None] = "0003_secure_documents"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "uq_document_chunks_legacy_index",
        "document_chunks",
        ["document_id", "chunk_index"],
        unique=True,
        postgresql_where=sa.text("document_version_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_document_chunks_legacy_index", table_name="document_chunks")
