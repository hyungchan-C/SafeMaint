from typing import Literal

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


class ChatRequest(BaseModel):
    question: str = Field(min_length=2, max_length=1000)
    context: ChatContext = Field(default_factory=ChatContext)

    @field_validator("question")
    @classmethod
    def normalize_question(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 2:
            raise ValueError("question must contain at least two non-space characters")
        return normalized


class ChatSource(BaseModel):
    document_id: str
    chunk_id: str
    title: str
    source_type: str
    excerpt: str
    page: int | None = None
    url: str | None = None
    similarity: float = Field(ge=-1.0, le=1.0)


class ChatResponse(BaseModel):
    answer: str
    sources: list[ChatSource]
    retrieval_mode: Literal["bge-m3"] = "bge-m3"
    warning: str | None = None
