from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


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
