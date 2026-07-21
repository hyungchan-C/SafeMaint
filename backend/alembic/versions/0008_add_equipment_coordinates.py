"""Add latitude/longitude coordinates to equipment.

Revision ID: 0008_add_equipment_coordinates
Revises: 0007_add_auth_sessions
Create Date: 2026-07-21
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0008_add_equipment_coordinates"
down_revision: Union[str, Sequence[str], None] = "0007_add_auth_sessions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("equipment", sa.Column("latitude", sa.Float(), nullable=True))
    op.add_column("equipment", sa.Column("longitude", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("equipment", "longitude")
    op.drop_column("equipment", "latitude")
