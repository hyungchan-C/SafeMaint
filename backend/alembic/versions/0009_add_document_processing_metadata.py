"""Add parser and fallback metadata to document versions.

Revision ID: 0009_processing_metadata
Revises: 0008_add_equipment_coordinates
Create Date: 2026-07-21
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0009_processing_metadata"
down_revision: Union[str, Sequence[str], None] = "0008_add_equipment_coordinates"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "document_versions",
        sa.Column(
            "processing_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("document_versions", "processing_metadata")
