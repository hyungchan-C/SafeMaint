from __future__ import annotations

from typing import Literal

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


class ClassifyRequest(BaseModel):
    question: str
    context: ChatContext = Field(default_factory=ChatContext)


class ClassifyResponse(BaseModel):
    occurrence_type: str
    confidence: float | None = None
    analysis: QueryAnalysis | None = None
    model: str


class AnswerRequest(BaseModel):
    question: str
    context: ChatContext = Field(default_factory=ChatContext)
    analysis: QueryAnalysis | None = None
    sources: list[ChatSource] = Field(default_factory=list)


class AnswerResponse(BaseModel):
    answer: str
    model: str
