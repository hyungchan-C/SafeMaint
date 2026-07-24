from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class ChatContext(BaseModel):
    site_name: str | None = Field(default=None, max_length=100)
    equipment_name: str | None = Field(default=None, max_length=100)
    manufacturer: str | None = Field(default=None, max_length=100)
    model_number: str | None = Field(default=None, max_length=100)
    component_name: str | None = Field(default=None, max_length=100)
    task_type: str | None = Field(default=None, max_length=100)
    energy_source: str | None = Field(default=None, max_length=100)
    task_description: str | None = Field(default=None, max_length=2000)
    registered_manuals: list[str] = Field(default_factory=list, max_length=20)
    selected_document_ids: list[UUID] = Field(default_factory=list, max_length=20)
    selected_document_version_ids: list[UUID] = Field(
        default_factory=list, max_length=20
    )

    def effective_document_ids(self) -> tuple[UUID, ...]:
        values = list(self.selected_document_ids)
        for legacy_value in self.registered_manuals:
            try:
                parsed = UUID(legacy_value)
            except (TypeError, ValueError):
                continue
            if parsed not in values:
                values.append(parsed)
        return tuple(values)


class QueryAnalysis(BaseModel):
    question_intent: Literal[
        "document_qa",
        "maintenance_guide",
        "component_info",
        "clarification_required",
    ] | None = None
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


class AccessScope(BaseModel):
    requester_user_id: UUID | None = None
    site_ids: list[str] = Field(default_factory=list)
    all_sites: bool = False
    allow_private: bool = False
    allow_company: bool = False


class InternalChatRequest(ChatRequest):
    access_scope: AccessScope = Field(default_factory=AccessScope)
    request_id: str | None = Field(default=None, max_length=100)


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


class ChatResponse(BaseModel):
    answer: str
    sources: list[ChatSource]
    retrieval_mode: Literal["bge-m3", "hybrid"] = "hybrid"
    warning: str | None = None
