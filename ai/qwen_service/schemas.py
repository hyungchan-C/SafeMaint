from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ChatContext(BaseModel):
    model_config = ConfigDict(extra="ignore")

    site_name: str | None = None
    equipment_name: str | None = None
    manufacturer: str | None = None
    model_number: str | None = None
    component_name: str | None = None
    task_type: str | None = None
    energy_source: str | None = None
    task_description: str | None = None


class QueryAnalysis(BaseModel):
    question_intent: Literal[
        "document_qa",
        "maintenance_guide",
        "component_info",
        "clarification_required",
    ] | None = None
    intent_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    clarification_question: str | None = None
    occurrence_type: str | None = None
    work_type: str | None = None
    equipment: list[str] = Field(default_factory=list)
    component: list[str] = Field(default_factory=list)
    explicit_risk_factors: list[str] = Field(default_factory=list)
    energy_sources: list[str] = Field(default_factory=list)
    search_keywords: list[str] = Field(default_factory=list)


class ChatSource(BaseModel):
    model_config = ConfigDict(extra="ignore")

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
    similarity: float | None = None
    keyword_score: float | None = None
    retrieval_score: float | None = None
    reranker_score: float | None = None
    document_profile: dict[str, Any] | None = None


class ClassifyRequest(BaseModel):
    question: str
    context: ChatContext = Field(default_factory=ChatContext)


class ClassifyResponse(BaseModel):
    occurrence_type: str
    confidence: float | None = None
    question_intent: Literal[
        "document_qa",
        "maintenance_guide",
        "component_info",
        "clarification_required",
    ] | None = None
    intent_confidence: float | None = None
    clarification_question: str | None = None
    analysis: QueryAnalysis | None = None
    model: str


class IntentClassifyResponse(BaseModel):
    question_intent: Literal[
        "document_qa",
        "maintenance_guide",
        "component_info",
        "clarification_required",
    ]
    intent_confidence: float | None = None
    clarification_question: str | None = None
    analysis: QueryAnalysis | None = None
    model: str


class EvidenceBackedItem(BaseModel):
    content: str
    evidence_chunk_ids: list[str] = Field(default_factory=list)


class EvidenceConflict(BaseModel):
    content: str
    evidence_chunk_ids: list[str] = Field(min_length=2)


class MaintenanceHazard(EvidenceBackedItem):
    name: str


class DocumentOverview(BaseModel):
    filename: str | None = None
    document_type: str | None = None
    manufacturer: str | None = None
    model_name: str | None = None
    version: str | None = None
    authored_at: str | None = None


class DocumentAnswerDetails(BaseModel):
    answer_type: Literal["document_qa"] = "document_qa"
    overview: DocumentOverview = Field(default_factory=DocumentOverview)
    main_contents: list[EvidenceBackedItem] = Field(default_factory=list)
    related_equipment: list[str] = Field(default_factory=list)
    related_components: list[str] = Field(default_factory=list)
    supported_tasks: list[str] = Field(default_factory=list)
    evidence_chunk_ids: list[str] = Field(default_factory=list)
    conflicts: list[EvidenceConflict] = Field(default_factory=list)
    unverified_information: list[str] = Field(default_factory=list)


class MaintenanceSummary(BaseModel):
    status: Literal["안전관리자 확인 필요", "작업 중지 권고", "근거 부족"]
    risk_level: Literal["낮음", "보통", "높음", "매우 높음", "판단 불가"]
    risk_basis: list[EvidenceBackedItem] = Field(default_factory=list)
    core_warning: str


class MaintenanceAnswerDetails(BaseModel):
    answer_type: Literal["maintenance_guide"] = "maintenance_guide"
    summary: MaintenanceSummary
    pre_checks: list[EvidenceBackedItem] = Field(default_factory=list)
    hazards: list[MaintenanceHazard] = Field(default_factory=list, max_length=3)
    manual_steps: list[EvidenceBackedItem] = Field(default_factory=list)
    precautions: list[EvidenceBackedItem] = Field(default_factory=list)
    stop_conditions: list[EvidenceBackedItem] = Field(default_factory=list)
    related_regulations_and_incidents: list[EvidenceBackedItem] = Field(
        default_factory=list
    )
    evidence_chunk_ids: list[str] = Field(default_factory=list)
    conflicts: list[EvidenceConflict] = Field(default_factory=list)
    additional_information_needed: list[str] = Field(default_factory=list)


class ComponentAnswerDetails(BaseModel):
    answer_type: Literal["component_info"] = "component_info"
    one_line_description: str
    main_roles: list[EvidenceBackedItem] = Field(default_factory=list)
    usage_locations: list[EvidenceBackedItem] = Field(default_factory=list)
    precautions: list[EvidenceBackedItem] = Field(default_factory=list)
    evidence_chunk_ids: list[str] = Field(default_factory=list)
    conflicts: list[EvidenceConflict] = Field(default_factory=list)
    additional_information_needed: list[str] = Field(default_factory=list)


StructuredAnswer = Annotated[
    DocumentAnswerDetails | MaintenanceAnswerDetails | ComponentAnswerDetails,
    Field(discriminator="answer_type"),
]


class ChatChecklistItem(BaseModel):
    id: None = None
    content: str
    sequence: int = Field(ge=1)
    is_required: bool = True
    is_completed: bool = False
    completed_by_user_id: None = None
    completed_at: None = None
    evidence_chunk_ids: list[str] = Field(default_factory=list)


class AnswerRequest(BaseModel):
    question: str
    context: ChatContext = Field(default_factory=ChatContext)
    analysis: QueryAnalysis | None = None
    answer_type: Literal["document_qa", "maintenance_guide", "component_info"]
    sources: list[ChatSource] = Field(default_factory=list)
    candidate_structured_answer: dict[str, Any] | None = None


class AnswerResponse(BaseModel):
    answer: str
    answer_type: Literal["document_qa", "maintenance_guide", "component_info"]
    structured_answer: StructuredAnswer | None = None
    checklist_items: list[ChatChecklistItem] = Field(default_factory=list)
    used_source_ids: list[str] = Field(default_factory=list)
    model: str
    fallback_reason: str | None = None


class DocumentProfileRequest(BaseModel):
    title: str
    original_filename: str | None = None
    manufacturer: str | None = None
    product_type: str | None = None
    model_name: str | None = None
    document_type: str | None = None
    sample_text: str = Field(max_length=20000)


class DocumentProfile(BaseModel):
    product_names: list[str] = Field(default_factory=list, max_length=12)
    model_names: list[str] = Field(default_factory=list, max_length=12)
    aliases: list[str] = Field(default_factory=list, max_length=20)
    equipment: list[str] = Field(default_factory=list, max_length=20)
    components: list[str] = Field(default_factory=list, max_length=30)
    supported_tasks: list[str] = Field(default_factory=list, max_length=20)
    safety_topics: list[str] = Field(default_factory=list, max_length=20)
    summary_points: list[str] = Field(default_factory=list, max_length=8)
    document_keywords: list[str] = Field(default_factory=list, max_length=30)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    extraction_notes: list[str] = Field(default_factory=list, max_length=8)


class DocumentProfileResponse(BaseModel):
    document_profile: DocumentProfile
    model: str
