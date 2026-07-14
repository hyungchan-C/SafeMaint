from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UuidPrimaryKeyMixin


class Assessment(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "assessments"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'pending_review', 'approved', 'rejected')",
            name="status",
        ),
        Index("ix_assessments_created_at", "created_at"),
    )

    site_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("sites.id", ondelete="RESTRICT"),
        index=True,
    )
    equipment_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("equipment.id", ondelete="RESTRICT"),
        index=True,
    )
    component_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("components.id", ondelete="RESTRICT"),
        index=True,
    )
    site_name: Mapped[str] = mapped_column(String(200), nullable=False)
    equipment_name: Mapped[str] = mapped_column(String(200), nullable=False)
    component_name: Mapped[str | None] = mapped_column(String(200))
    task_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    energy_sources: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default=text("'draft'"), index=True
    )
    engine_version: Mapped[str] = mapped_column(String(100), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(100), nullable=False)
    request_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False
    )
    reviewed_by: Mapped[str | None] = mapped_column(String(200))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    hazards: Mapped[list["AssessmentHazard"]] = relationship(
        back_populates="assessment",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="AssessmentHazard.created_at",
    )
    checklist_items: Mapped[list["ChecklistItem"]] = relationship(
        back_populates="assessment",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ChecklistItem.sequence",
    )
    evidence_links: Mapped[list["AssessmentEvidence"]] = relationship(
        back_populates="assessment",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="AssessmentEvidence.retrieval_rank",
    )


class AssessmentHazard(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "assessment_hazards"
    __table_args__ = (
        CheckConstraint("likelihood BETWEEN 1 AND 4", name="likelihood_range"),
        CheckConstraint("severity BETWEEN 1 AND 4", name="severity_range"),
        CheckConstraint("score BETWEEN 1 AND 16", name="score_range"),
        CheckConstraint(
            "risk_level IN ('low', 'medium', 'high')", name="risk_level"
        ),
    )

    assessment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("assessments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    accident_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    likelihood: Mapped[int] = mapped_column(Integer, nullable=False)
    severity: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    safety_actions: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )

    assessment: Mapped[Assessment] = relationship(back_populates="hazards")


class ChecklistItem(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "checklist_items"
    __table_args__ = (
        UniqueConstraint(
            "assessment_id", "sequence", name="uq_checklist_items_assessment_sequence"
        ),
        CheckConstraint("sequence >= 1", name="sequence_positive"),
    )

    assessment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("assessments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    is_completed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), index=True
    )
    completed_by: Mapped[str | None] = mapped_column(String(200))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    assessment: Mapped[Assessment] = relationship(back_populates="checklist_items")


class AssessmentEvidence(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "assessment_evidence"
    __table_args__ = (
        UniqueConstraint(
            "assessment_id", "chunk_id", name="uq_assessment_evidence_assessment_chunk"
        ),
        CheckConstraint("retrieval_rank >= 1", name="retrieval_rank_positive"),
    )

    assessment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("assessments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chunk_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("document_chunks.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    retrieval_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    retrieval_score: Mapped[float | None] = mapped_column(Float)
    reranker_score: Mapped[float | None] = mapped_column(Float)
    used_in_answer: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    assessment: Mapped[Assessment] = relationship(back_populates="evidence_links")
    chunk: Mapped["DocumentChunk"] = relationship(back_populates="assessment_links")
