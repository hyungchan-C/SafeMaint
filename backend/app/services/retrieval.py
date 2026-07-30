from __future__ import annotations

from typing import Protocol

import httpx

from app.core.config import settings
from app.schemas.assessment import AssessmentRequest, EvidenceItem
from app.schemas.chat import ChatResponse, RetrievalAccessScope


class RetrievalService(Protocol):
    def search(
        self,
        request: AssessmentRequest,
        access_scope: RetrievalAccessScope,
        limit: int = 5,
    ) -> list[EvidenceItem]: ...


class RagRetrievalService:
    """Assessment adapter for the same hybrid RAG endpoint used by chat."""

    def __init__(
        self,
        service_url: str | None = None,
        *,
        timeout_seconds: float | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.service_url = (service_url or "").rstrip("/")
        self.timeout_seconds = timeout_seconds or settings.rag_request_timeout_seconds
        self.transport = transport

    def search(
        self,
        request: AssessmentRequest,
        access_scope: RetrievalAccessScope,
        limit: int = 5,
    ) -> list[EvidenceItem]:
        if not self.service_url:
            return []
        energy_source = ", ".join(request.energy_sources) or None
        body = {
            "question": (
                f"{request.equipment_name} {request.component_name or ''} "
                f"{request.task_type} 작업의 위험요인과 안전조치"
            ).strip(),
            "context": {
                "site_name": request.site_name,
                "equipment_name": request.equipment_name,
                "manufacturer": request.manufacturer,
                "model_number": request.model_number,
                "component_name": request.component_name,
                "task_type": request.task_type,
                "energy_source": energy_source,
                "task_description": request.description,
            },
            "access_scope": access_scope.model_dump(mode="json"),
        }
        try:
            with httpx.Client(
                timeout=self.timeout_seconds, transport=self.transport
            ) as client:
                response = client.post(f"{self.service_url}/v1/chat", json=body)
                response.raise_for_status()
            rag_response = ChatResponse.model_validate(response.json())
        except (httpx.HTTPError, ValueError):
            return []

        return [
            EvidenceItem(
                document_id=source.document_id,
                chunk_id=source.chunk_id,
                title=source.title,
                page=source.page,
                page_start=source.page_start,
                page_end=source.page_end,
                section=source.section,
                source_type=source.source_type,
                document_scope=source.document_scope,
                original_filename=source.original_filename,
                document_version=source.document_version,
                excerpt=source.excerpt,
                url=source.url,
                retrieval_rank=index,
                retrieval_score=source.retrieval_score or source.similarity,
                reranker_score=source.reranker_score,
                used_in_answer=False,
            )
            for index, source in enumerate(rag_response.sources[:limit], start=1)
        ]
