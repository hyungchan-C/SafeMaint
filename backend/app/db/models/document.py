from datetime import date, datetime
from typing import Any
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
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
        CheckConstraint(
            "lifecycle_status IN ('pending', 'processing', 'review_required', "
            "'active', 'failed', 'deleted')",
            name="lifecycle_status",
        ),
    )

    external_id: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    document_type_code: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("document_types.code", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    lifecycle_status: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default=text("'active'"), index=True
    )
    site_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("sites.id", ondelete="RESTRICT"), index=True
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    current_version_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="SET NULL", use_alter=True),
        index=True,
    )
    public_package_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("public_rag_packages.id", ondelete="RESTRICT"),
        index=True,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
    versions: Mapped[list["DocumentVersion"]] = relationship(
        back_populates="document",
        foreign_keys="DocumentVersion.document_id",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="DocumentVersion.version_number",
    )
    document_type: Mapped["DocumentType"] = relationship()
    current_version: Mapped["DocumentVersion | None"] = relationship(
        foreign_keys=[current_version_id], post_update=True
    )


class DocumentChunk(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint(
            "document_version_id",
            "chunk_index",
            name="uq_document_chunks_version_index",
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
        Index(
            "uq_document_chunks_legacy_index",
            "document_id",
            "chunk_index",
            unique=True,
            postgresql_where=text("document_version_id IS NULL"),
        ),
    )

    document_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_version_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="RESTRICT"),
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
    document_version: Mapped["DocumentVersion | None"] = relationship(
        back_populates="chunks"
    )
    assessment_links: Mapped[list["AssessmentEvidence"]] = relationship(
        back_populates="chunk", passive_deletes=True
    )


class DocumentType(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "document_types"
    __table_args__ = (
        UniqueConstraint("code", name="uq_document_types_code"),
        CheckConstraint("scope IN ('public', 'company')", name="scope"),
    )

    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    scope: Mapped[str] = mapped_column(String(20), nullable=False)
    is_exportable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )


class PublicRagPackage(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "public_rag_packages"
    __table_args__ = (
        UniqueConstraint("package_version", name="uq_public_rag_packages_version"),
        CheckConstraint(
            "status IN ('importing', 'active', 'superseded', 'failed')",
            name="status",
        ),
        Index(
            "uq_public_rag_packages_one_active",
            "status",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    package_version: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(200), nullable=False)
    embedding_dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default=text("'importing'"), index=True
    )
    imported_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentVersion(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint(
            "document_id", "version_number", name="uq_document_versions_number"
        ),
        CheckConstraint("version_number >= 1", name="version_number_positive"),
        CheckConstraint("file_size > 0", name="file_size_positive"),
        CheckConstraint(
            "status IN ('pending', 'processing', 'review_required', 'active', "
            "'superseded', 'failed', 'ocr_required', 'deleted')",
            name="status",
        ),
        Index(
            "uq_document_versions_one_active",
            "document_id",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

    document_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    stored_filename: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default=text("'pending'"), index=True
    )
    failure_reason: Mapped[str | None] = mapped_column(Text)
    processing_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), index=True
    )
    uploaded_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    approved_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    document: Mapped[Document] = relationship(
        back_populates="versions", foreign_keys=[document_id]
    )
    chunks: Mapped[list[DocumentChunk]] = relationship(
        back_populates="document_version", passive_deletes=True
    )
    processing_job: Mapped["DocumentProcessingJob | None"] = relationship(
        back_populates="document_version",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )


class DocumentProcessingJob(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "document_processing_jobs"
    __table_args__ = (
        UniqueConstraint(
            "document_version_id", name="uq_document_processing_jobs_version"
        ),
        CheckConstraint(
            "status IN ('queued', 'processing', 'completed', 'failed')",
            name="status",
        ),
        CheckConstraint("attempts >= 0", name="attempts_nonnegative"),
    )

    document_version_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'queued'"), index=True
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    document_version: Mapped[DocumentVersion] = relationship(
        back_populates="processing_job"
    )
