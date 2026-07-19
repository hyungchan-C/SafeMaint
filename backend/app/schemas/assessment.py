from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AssessmentStatus(str, Enum):
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class AssessmentRequest(BaseModel):
    site_id: UUID | None = None
    equipment_id: UUID | None = None
    component_id: UUID | None = None
    site_name: str = Field(min_length=1, max_length=100)
    equipment_name: str = Field(min_length=1, max_length=100)
    manufacturer: str | None = Field(default=None, max_length=100)
    model_number: str | None = Field(default=None, max_length=100)
    component_name: str | None = Field(default=None, max_length=100)
    task_type: str = Field(min_length=1, max_length=100)
    energy_sources: list[str] = Field(default_factory=list, max_length=10)
    description: str = Field(min_length=5, max_length=2000)


class HazardItem(BaseModel):
    name: str
    accident_type: str
    likelihood: int = Field(ge=1, le=4)
    severity: int = Field(ge=1, le=4)
    score: int = Field(ge=1, le=16)
    risk_level: RiskLevel
    safety_actions: list[str]


class EvidenceItem(BaseModel):
    document_id: str
    chunk_id: str
    title: str
    page: int | None = None
    page_start: int | None = None
    page_end: int | None = None
    section: str | None = None
    source_type: str
    document_scope: Literal["public", "company"] | None = None
    original_filename: str | None = None
    document_version: int | None = None
    excerpt: str
    url: str | None = None
    retrieval_rank: int = Field(ge=1)
    retrieval_score: float | None = None
    reranker_score: float | None = None
    used_in_answer: bool = False


class AssessmentResponse(BaseModel):
    assessment_id: str
    status: AssessmentStatus
    created_at: datetime
    hazards: list[HazardItem]
    tbm_checklist: list[str]
    evidence: list[EvidenceItem]
    evidence_status: Literal["not_connected", "connected"]
    disclaimer: str
