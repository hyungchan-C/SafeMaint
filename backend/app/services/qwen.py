from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import TypeAdapter, ValidationError

from app.core.config import settings
from app.schemas.chat import (
    ChatChecklistItem,
    ChatRequest,
    ChatResponse,
    QueryAnalysis,
    StructuredAnswer,
)


_STRUCTURED_ANSWER_ADAPTER = TypeAdapter(StructuredAnswer)


@dataclass(frozen=True, slots=True)
class QwenGeneratedAnswer:
    answer: str
    model: str | None = None
    structured_answer: StructuredAnswer | None = None
    checklist_items: tuple[ChatChecklistItem, ...] = ()
    used_source_ids: tuple[str, ...] = ()


class QwenClient:
    """HTTP client for the SafeMaint Qwen service.

    The service can run inside Docker on an on-prem GPU server or behind a Colab
    tunnel. The backend only depends on the stable HTTP contract.
    """

    def __init__(
        self,
        service_url: str | None = None,
        api_key: str | None = None,
        timeout_seconds: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.service_url = (service_url or settings.qwen_service_url or "").rstrip("/")
        self.api_key = settings.qwen_api_key if api_key is None else api_key
        self.timeout_seconds = timeout_seconds or settings.qwen_timeout_seconds
        self.transport = transport

    async def classify(self, request: ChatRequest) -> QueryAnalysis | None:
        if not self.service_url:
            return None
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                transport=self.transport,
                headers=self._headers(),
            ) as client:
                response = await client.post(
                    f"{self.service_url}/v1/classify",
                    json={
                        "question": request.question,
                        "context": self._safe_context(request),
                    },
                )
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError, TypeError):
            return None

        if isinstance(body.get("analysis"), dict):
            try:
                return QueryAnalysis.model_validate(body["analysis"])
            except ValueError:
                return None
        occurrence_type = str(body.get("occurrence_type") or "").strip()
        question_intent = str(body.get("question_intent") or "").strip() or None
        if not occurrence_type and not question_intent:
            return None
        try:
            return QueryAnalysis(
                occurrence_type=occurrence_type or None,
                question_intent=question_intent,
                intent_confidence=body.get("intent_confidence"),
                clarification_question=body.get("clarification_question"),
            )
        except ValueError:
            return QueryAnalysis(occurrence_type=occurrence_type or None)

    async def answer(
        self,
        request: ChatRequest,
        retrieval_response: ChatResponse,
    ) -> QwenGeneratedAnswer | None:
        if not self.service_url:
            return None
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                transport=self.transport,
                headers=self._headers(),
            ) as client:
                response = await client.post(
                    f"{self.service_url}/v1/answer",
                    json={
                        "question": request.question,
                        "context": self._safe_context(request),
                        "analysis": (
                            request.analysis.model_dump(mode="json")
                            if request.analysis
                            else None
                        ),
                        "answer_type": retrieval_response.answer_type,
                        "sources": [
                            self._source_payload(source)
                            for source in retrieval_response.sources
                        ],
                    },
                )
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError, TypeError):
            return None

        answer = str(body.get("answer") or "").strip()
        if not answer:
            return None
        model = str(body.get("model") or "").strip() or None
        structured_answer = None
        if isinstance(body.get("structured_answer"), dict):
            try:
                structured_answer = _STRUCTURED_ANSWER_ADAPTER.validate_python(
                    body["structured_answer"]
                )
            except ValidationError:
                structured_answer = None
        checklist_items: list[ChatChecklistItem] = []
        if isinstance(body.get("checklist_items"), list):
            for item in body["checklist_items"]:
                try:
                    checklist_items.append(ChatChecklistItem.model_validate(item))
                except ValidationError:
                    continue
        used_source_ids = tuple(
            str(value)
            for value in body.get("used_source_ids", [])
            if isinstance(value, str) and value.strip()
        )
        return QwenGeneratedAnswer(
            answer=answer,
            model=model,
            structured_answer=structured_answer,
            checklist_items=tuple(checklist_items),
            used_source_ids=used_source_ids,
        )

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    @staticmethod
    def _safe_context(request: ChatRequest) -> dict[str, Any]:
        return request.context.model_dump(
            mode="json",
            exclude={
                "registered_manuals",
                "selected_document_ids",
                "selected_document_version_ids",
                "visual_summary",
            },
        )

    @staticmethod
    def _source_payload(source: Any) -> dict[str, Any]:
        return {
            "document_id": source.document_id,
            "document_version_id": source.document_version_id,
            "chunk_id": source.chunk_id,
            "title": source.title,
            "source_type": source.source_type,
            "document_scope": source.document_scope,
            "original_filename": source.original_filename,
            "document_version": source.document_version,
            "section": source.section,
            "excerpt": source.excerpt,
            "page": source.page,
            "page_start": source.page_start,
            "page_end": source.page_end,
            "publisher": source.publisher,
            "url": source.url,
            "similarity": source.similarity,
            "keyword_score": source.keyword_score,
            "retrieval_score": source.retrieval_score,
            "reranker_score": source.reranker_score,
        }
