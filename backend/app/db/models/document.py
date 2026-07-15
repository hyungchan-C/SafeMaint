from datetime import date
from typing import Any
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    Date,
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


class Document(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(
            "access_level IN ('public', 'restricted', 'private')",
            name="access_level",
        ),
    )

    external_id: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    publisher: Mapped[str | None] = mapped_column(String(200))
    source_url: Mapped[str | None] = mapped_column(Text)
    revision: Mapped[str | None] = mapped_column(String(100))
    published_at: Mapped[date | None] = mapped_column(Date)
    access_level: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'public'")
    )
    file_sha256: Mapped[str | None] = mapped_column(String(64), unique=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )

    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class DocumentChunk(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint(
            "document_id", "chunk_index", name="uq_document_chunks_document_index"
        ),
        CheckConstraint("chunk_index >= 0", name="chunk_index_nonnegative"),
        CheckConstraint(
            "page_number IS NULL OR page_number >= 1", name="page_number_positive"
        ),
        CheckConstraint(
            "page_start IS NULL OR page_start >= 1", name="page_start_positive"
        ),
        CheckConstraint(
            "page_end IS NULL OR page_end >= page_start", name="page_range"
        ),
        CheckConstraint(
            "embedding_status IN ('pending', 'ready', 'failed', 'skipped')",
            name="embedding_status",
        ),
        Index(
            "ix_document_chunks_metadata_gin", "metadata", postgresql_using="gin"
        ),
    )

    document_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer)
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    section_path: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector())
    embedding_model: Mapped[str | None] = mapped_column(String(200))
    embedding_dimension: Mapped[int | None] = mapped_column(Integer)
    embedding_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'pending'")
    )

    document: Mapped[Document] = relationship(back_populates="chunks")
    assessment_links: Mapped[list["AssessmentEvidence"]] = relationship(
        back_populates="chunk", passive_deletes=True
    )
