from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


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


class ChecklistItemResponse(BaseModel):
    id: UUID | None = None
    sequence: int = Field(ge=1)
    content: str
    is_completed: bool = False
    completed_by_user_id: UUID | None = None
    completed_at: datetime | None = None


class ChecklistItemUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_completed: bool


class ChecklistItemUpdateResponse(ChecklistItemResponse):
    id: UUID
    assessment_id: UUID


class ChatChecklistSaveRequest(BaseModel):
    """채팅이나 GPS 근접 안내에서 이미 만들어진 체크리스트를 그대로 저장할 때 쓰는 요청.

    site_name/equipment_name/task_type를 안 보내면(채팅처럼 정식 입력 폼이 없는
    경우) 고정 기본값으로 채우고, description(질문 원문 등)과 checklist_items
    (화면에 보이는 항목 그대로)만 필수로 받는다. GPS 근접 안내처럼 실제 사업장·설비
    정보가 있는 경우에는 이 필드들을 그대로 넘겨서 정확한 값으로 저장할 수 있다.
    규칙 엔진으로 새로 계산하지 않고 받은 값 그대로 저장한다.
    """

    site_name: str | None = Field(default=None, max_length=100)
    equipment_name: str | None = Field(default=None, max_length=100)
    task_type: str | None = Field(default=None, max_length=100)
    description: str = Field(min_length=5, max_length=2000)
    checklist_items: list[str] = Field(min_length=1, max_length=50)


class AssessmentResponse(BaseModel):
    assessment_id: str
    status: AssessmentStatus
    created_at: datetime
    hazards: list[HazardItem]
    tbm_checklist: list[str]
    checklist_items: list[ChecklistItemResponse] = Field(default_factory=list)
    evidence: list[EvidenceItem]
    evidence_status: Literal["not_connected", "connected"]
    disclaimer: str


class AssessmentSummaryResponse(BaseModel):
    """목록 화면용 요약. 체크리스트 상세 항목 대신 진행 건수만 담아 가볍게 유지한다."""

    assessment_id: str
    status: AssessmentStatus
    created_at: datetime
    created_by_user_id: UUID | None
    created_by_name: str | None
    site_name: str
    equipment_name: str
    task_type: str
    description: str
    checklist_total: int
    checklist_completed: int
