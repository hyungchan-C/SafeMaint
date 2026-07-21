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
    version_number: int
    status: str
    is_active: bool


class ApproveDocumentResponse(BaseModel):
    document_id: UUID
    document_version_id: UUID
    version_number: int
    status: str
    is_active: bool
