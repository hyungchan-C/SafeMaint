from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class UploadDocumentResponse(BaseModel):
    document_id: UUID
    document_version_id: UUID
    version_number: int
    status: str


class UserDocumentSummary(BaseModel):
    document_id: UUID
    document_version_id: UUID
    original_filename: str
    title: str
    version_number: int
    document_type_code: str
    source_type: str
    access_level: str
    lifecycle_status: str
    status: str
    is_active: bool
    extractor: str | None = None
    fallback_used: bool = False
    processing_warning: str | None = None
    failure_reason: str | None = None
    page_count: int | None = None
    processing_stage: str | None = None
    progress_percent: int | None = None
    progress_message: str | None = None
    progress_metadata: dict[str, int] = Field(default_factory=dict)
    processing_attempt: int = 0
    created_at: datetime


class ReviewQueueDocumentSummary(BaseModel):
    document_id: UUID
    document_version_id: UUID
    original_filename: str
    title: str
    version_number: int
    document_type_code: str
    source_type: str
    access_level: str
    lifecycle_status: str
    version_status: str
    is_active: bool
    uploaded_by_user_id: UUID | None = None
    uploader_name: str | None = None
    created_at: datetime
    extractor: str | None = None
    fallback_used: bool = False
    processing_warning: str | None = None
    failure_reason: str | None = None
    page_count: int | None = None


class ApproveDocumentResponse(BaseModel):
    document_id: UUID
    document_version_id: UUID
    version_number: int
    status: str
    is_active: bool


class DocumentProcessingProgressResponse(BaseModel):
    document_id: UUID
    document_version_id: UUID
    filename: str
    status: str
    stage: str
    attempt: int = 0
    progress_percent: int
    message: str
    processed_pages: int = 0
    total_pages: int = 0
    processed_chunks: int = 0
    total_chunks: int = 0
    embedded_chunks: int = 0
    updated_at: datetime
    is_terminal: bool
    rag_ready: bool
