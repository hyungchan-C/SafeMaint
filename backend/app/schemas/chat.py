from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


QuestionIntent = Literal[
    "document_qa",
    "maintenance_guide",
    "component_info",
    "clarification_required",
]
AnswerType = Literal[
    "document_qa",
    "maintenance_guide",
    "component_info",
    "no_evidence",
    "clarification_required",
]


class ChatContext(BaseModel):
    site_name: str | None = Field(default=None, max_length=100)
    equipment_name: str | None = Field(default=None, max_length=100)
    manufacturer: str | None = Field(default=None, max_length=100)
    model_number: str | None = Field(default=None, max_length=100)
    component_name: str | None = Field(default=None, max_length=100)
    task_type: str | None = Field(default=None, max_length=100)
    energy_source: str | None = Field(default=None, max_length=100)
    task_description: str | None = Field(default=None, max_length=2000)
    visual_summary: str | None = Field(default=None, max_length=30000)
    registered_manuals: list[str] = Field(default_factory=list, max_length=20)
    selected_document_ids: list[UUID] = Field(default_factory=list, max_length=20)
    selected_document_version_ids: list[UUID] = Field(
        default_factory=list, max_length=20
    )


class QueryAnalysis(BaseModel):
    question_intent: QuestionIntent | None = None
    intent_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    clarification_question: str | None = Field(default=None, max_length=500)
    occurrence_type: str | None = None
    work_type: str | None = None
    equipment: list[str] = Field(default_factory=list, max_length=20)
    component: list[str] = Field(default_factory=list, max_length=20)
    explicit_risk_factors: list[str] = Field(default_factory=list, max_length=20)
    energy_sources: list[str] = Field(default_factory=list, max_length=20)
    search_keywords: list[str] = Field(default_factory=list, max_length=30)


class ChatRequest(BaseModel):
    question: str = Field(min_length=2, max_length=1000)
    context: ChatContext = Field(default_factory=ChatContext)
    analysis: QueryAnalysis | None = None

    @field_validator("question")
    @classmethod
    def normalize_question(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 2:
            raise ValueError("question must contain at least two non-space characters")
        return normalized


class RetrievalAccessScope(BaseModel):
    requester_user_id: UUID | None = None
    site_ids: list[str] = Field(default_factory=list)
    all_sites: bool = False
    allow_private: bool = False
    allow_company: bool = False


class ChatSource(BaseModel):
    document_id: str
    document_version_id: str | None = None
    chunk_id: str
    title: str
    source_type: str
    document_scope: Literal["public", "company"] | None = None
    original_filename: str | None = None
    document_version: int | None = None
    section: str | None = None
    excerpt: str
    page: int | None = None
    page_start: int | None = None
    page_end: int | None = None
    publisher: str | None = None
    url: str | None = None
    similarity: float = Field(ge=-1.0, le=1.0)
    keyword_score: float = Field(default=0.0, ge=0.0)
    retrieval_score: float = Field(default=0.0, ge=0.0)
    reranker_score: float = Field(default=0.0, ge=0.0)


class AccidentClassification(BaseModel):
    label: str
    model: str
    adapter: str


class DocumentOverview(BaseModel):
    filename: str | None = None
    document_type: str | None = None
    manufacturer: str | None = None
    model_name: str | None = None
    version: str | None = None
    authored_at: str | None = None


class EvidenceBackedItem(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    evidence_chunk_ids: list[str] = Field(default_factory=list, max_length=20)


class EvidenceConflict(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    evidence_chunk_ids: list[str] = Field(min_length=2, max_length=20)

    @field_validator("evidence_chunk_ids")
    @classmethod
    def require_two_distinct_sources(cls, value: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(item for item in value if item))
        if len(normalized) < 2:
            raise ValueError("a conflict requires at least two distinct evidence chunks")
        return normalized


class MaintenanceHazard(EvidenceBackedItem):
    name: str = Field(min_length=1, max_length=300)


class MaintenanceSummary(BaseModel):
    status: Literal["안전관리자 확인 필요", "작업 중지 권고", "근거 부족"]
    risk_level: Literal["낮음", "보통", "높음", "매우 높음", "판단 불가"]
    risk_basis: list[EvidenceBackedItem] = Field(default_factory=list, max_length=10)
    core_warning: str = Field(min_length=1, max_length=2000)


class DocumentAnswerDetails(BaseModel):
    answer_type: Literal["document_qa"] = "document_qa"
    overview: DocumentOverview = Field(default_factory=DocumentOverview)
    main_contents: list[EvidenceBackedItem] = Field(default_factory=list, max_length=20)
    related_equipment: list[str] = Field(default_factory=list, max_length=20)
    related_components: list[str] = Field(default_factory=list, max_length=20)
    supported_tasks: list[str] = Field(default_factory=list, max_length=20)
    evidence_chunk_ids: list[str] = Field(default_factory=list, max_length=50)
    conflicts: list[EvidenceConflict] = Field(default_factory=list, max_length=10)
    unverified_information: list[str] = Field(default_factory=list, max_length=20)


class MaintenanceAnswerDetails(BaseModel):
    answer_type: Literal["maintenance_guide"] = "maintenance_guide"
    summary: MaintenanceSummary
    pre_checks: list[EvidenceBackedItem] = Field(default_factory=list, max_length=20)
    hazards: list[MaintenanceHazard] = Field(default_factory=list, max_length=3)
    manual_steps: list[EvidenceBackedItem] = Field(default_factory=list, max_length=30)
    stop_conditions: list[EvidenceBackedItem] = Field(default_factory=list, max_length=20)
    related_regulations_and_incidents: list[EvidenceBackedItem] = Field(
        default_factory=list, max_length=20
    )
    evidence_chunk_ids: list[str] = Field(default_factory=list, max_length=50)
    conflicts: list[EvidenceConflict] = Field(default_factory=list, max_length=10)
    additional_information_needed: list[str] = Field(default_factory=list, max_length=20)


class ComponentAnswerDetails(BaseModel):
    answer_type: Literal["component_info"] = "component_info"
    one_line_description: str = Field(min_length=1, max_length=2000)
    main_roles: list[EvidenceBackedItem] = Field(default_factory=list, max_length=20)
    usage_locations: list[EvidenceBackedItem] = Field(default_factory=list, max_length=20)
    precautions: list[EvidenceBackedItem] = Field(default_factory=list, max_length=20)
    evidence_chunk_ids: list[str] = Field(default_factory=list, max_length=50)
    conflicts: list[EvidenceConflict] = Field(default_factory=list, max_length=10)
    additional_information_needed: list[str] = Field(default_factory=list, max_length=20)


class NoEvidenceDetails(BaseModel):
    answer_type: Literal["no_evidence"] = "no_evidence"
    message: str
    required_information: list[str] = Field(default_factory=list, max_length=20)
    required_documents: list[str] = Field(default_factory=list, max_length=20)
    work_safety_notice: str | None = None


class ClarificationDetails(BaseModel):
    answer_type: Literal["clarification_required"] = "clarification_required"
    question: str
    options: list[str] = Field(default_factory=list, max_length=10)


StructuredAnswer = Annotated[
    DocumentAnswerDetails
    | MaintenanceAnswerDetails
    | ComponentAnswerDetails
    | NoEvidenceDetails
    | ClarificationDetails,
    Field(discriminator="answer_type"),
]


class ChatChecklistItem(BaseModel):
    id: UUID | None = None
    content: str = Field(min_length=1, max_length=2000)
    sequence: int = Field(ge=1)
    is_required: bool = True
    is_completed: bool = False
    completed_by_user_id: UUID | None = None
    completed_at: datetime | None = None
    evidence_chunk_ids: list[str] = Field(default_factory=list, max_length=20)


class ChatResponse(BaseModel):
    answer: str
    answer_type: AnswerType | None = None
    structured_answer: StructuredAnswer | None = None
    checklist_items: list[ChatChecklistItem] = Field(default_factory=list)
    clarification_question: str | None = None
    sources: list[ChatSource] = Field(default_factory=list)
    retrieval_mode: Literal["bge-m3", "hybrid", "safety-fallback"]
    generation_mode: Literal["openai", "template", "qwen"] = "template"
    model: str | None = None
    warning: str | None = None
    accident_classification: AccidentClassification | None = None
