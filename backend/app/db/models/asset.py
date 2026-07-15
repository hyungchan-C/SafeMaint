from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UuidPrimaryKeyMixin


class Site(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "sites"

    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )

    equipment: Mapped[list["Equipment"]] = relationship(
        back_populates="site", passive_deletes=True
    )


class Equipment(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "equipment"
    __table_args__ = (
        UniqueConstraint("site_id", "code", name="uq_equipment_site_code"),
    )

    site_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("sites.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    equipment_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    manufacturer: Mapped[str | None] = mapped_column(String(200))
    model_number: Mapped[str | None] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )

    site: Mapped[Site] = relationship(back_populates="equipment")
    components: Mapped[list["Component"]] = relationship(
        back_populates="equipment", passive_deletes=True
    )


class Component(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "components"
    __table_args__ = (
        UniqueConstraint("equipment_id", "code", name="uq_components_equipment_code"),
    )

    equipment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("equipment.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    component_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    manufacturer: Mapped[str | None] = mapped_column(String(200))
    part_number: Mapped[str | None] = mapped_column(String(200), index=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )

    equipment: Mapped[Equipment] = relationship(back_populates="components")
