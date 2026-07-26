from __future__ import annotations

from dataclasses import dataclass
import json
import re
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
from app.services.document_types import canonical_document_type


_STRUCTURED_ANSWER_ADAPTER = TypeAdapter(StructuredAnswer)


@dataclass(frozen=True, slots=True)
class QwenGeneratedAnswer:
    answer: str
    model: str | None = None
    structured_answer: StructuredAnswer | None = None
    checklist_items: tuple[ChatChecklistItem, ...] = ()
    used_source_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class QwenAnswerFailure:
    reason: str
    detail: str | None = None


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

        if not isinstance(body, dict):
            return None
        payload = body.get("analysis") if isinstance(body.get("analysis"), dict) else body
        normalized = self._normalize_analysis_payload(payload)
        if not any(
            normalized.get(field)
            for field in (
                "question_intent",
                "occurrence_type",
                "work_type",
                "equipment",
                "component",
                "explicit_risk_factors",
                "energy_sources",
                "search_keywords",
            )
        ):
            return None
        try:
            return QueryAnalysis.model_validate(normalized)
        except ValueError:
            return None

    async def classify_intent(self, request: ChatRequest) -> QueryAnalysis | None:
        if not self.service_url:
            return None
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                transport=self.transport,
                headers=self._headers(),
            ) as client:
                response = await client.post(
                    f"{self.service_url}/v1/intent",
                    json={
                        "question": request.question,
                        "context": self._safe_context(request),
                    },
                )
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError, TypeError):
            return None

        if not isinstance(body, dict):
            return None
        payload = body.get("analysis") if isinstance(body.get("analysis"), dict) else body
        normalized = self._normalize_analysis_payload(payload)
        if not normalized.get("question_intent"):
            return None
        try:
            return QueryAnalysis.model_validate(normalized)
        except ValueError:
            return None

    async def answer(
        self,
        request: ChatRequest,
        retrieval_response: ChatResponse,
    ) -> QwenGeneratedAnswer | QwenAnswerFailure | None:
        if not self.service_url:
            return QwenAnswerFailure(
                reason="not_configured",
                detail="Qwen service URL is not configured.",
            )
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
                        "candidate_structured_answer": (
                            retrieval_response.structured_answer.model_dump(
                                mode="json"
                            )
                            if retrieval_response.structured_answer is not None
                            else None
                        ),
                    },
                )
        except httpx.TimeoutException as exc:
            return QwenAnswerFailure(
                reason="timeout",
                detail=f"Qwen /v1/answer timed out after {self.timeout_seconds:g}s: {exc}",
            )
        except httpx.RequestError as exc:
            return QwenAnswerFailure(
                reason="connection_error",
                detail=str(exc),
            )
        except TypeError as exc:
            return QwenAnswerFailure(
                reason="request_error",
                detail=str(exc),
            )

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            return QwenAnswerFailure(
                reason=f"http_{exc.response.status_code}",
                detail=self._compact_error_text(exc.response.text),
            )

        try:
            raw_body = response.json()
        except ValueError as exc:
            return QwenAnswerFailure(
                reason="invalid_json",
                detail=str(exc),
            )
        if not isinstance(raw_body, dict):
            return QwenAnswerFailure(
                reason="invalid_response",
                detail=f"Expected JSON object, got {type(raw_body).__name__}.",
            )
        body = self._extract_answer_payload(raw_body)

        answer = str(body.get("answer") or "").strip()
        if not answer:
            return QwenAnswerFailure(
                reason="empty_answer",
                detail="Qwen /v1/answer returned an empty answer.",
            )
        model = str(body.get("model") or "").strip() or None
        structured_answer = None
        if isinstance(body.get("structured_answer"), dict):
            structured_payload = self._normalize_evidence_references(
                body["structured_answer"],
                retrieval_response.sources,
            )
            try:
                structured_answer = _STRUCTURED_ANSWER_ADAPTER.validate_python(
                    structured_payload
                )
            except ValidationError:
                structured_answer = None
        checklist_items: list[ChatChecklistItem] = []
        if isinstance(body.get("checklist_items"), list):
            for index, item in enumerate(body["checklist_items"], start=1):
                if isinstance(item, dict):
                    item = {
                        **item,
                        "id": None,
                        "sequence": index,
                        "is_completed": False,
                        "completed_by_user_id": None,
                        "completed_at": None,
                        "evidence_chunk_ids": self._normalize_source_id_list(
                            item.get("evidence_chunk_ids"),
                            retrieval_response.sources,
                        ),
                    }
                try:
                    checklist_items.append(ChatChecklistItem.model_validate(item))
                except ValidationError:
                    continue
        used_source_ids = tuple(
            self._normalize_source_id_list(
                body.get("used_source_ids"),
                retrieval_response.sources,
            )
        )
        if not used_source_ids and structured_answer is not None:
            used_source_ids = tuple(
                sorted(
                    self._collect_evidence_ids(
                        structured_answer.model_dump(mode="python")
                    )
                )
            )
        if not used_source_ids:
            used_source_ids = tuple(
                self._source_ids_from_citations(answer, retrieval_response.sources)
            )
        return QwenGeneratedAnswer(
            answer=answer,
            model=model,
            structured_answer=structured_answer,
            checklist_items=tuple(checklist_items),
            used_source_ids=used_source_ids,
        )

    @classmethod
    def _extract_answer_payload(cls, body: dict[str, Any]) -> dict[str, Any]:
        nested = body.get("answer")
        if isinstance(nested, str):
            nested_payload = cls._extract_json(nested)
            if nested_payload and (
                isinstance(nested_payload.get("structured_answer"), dict)
                or "checklist_items" in nested_payload
                or "used_source_ids" in nested_payload
                or any(
                    key in nested_payload
                    for key in (
                        "main_contents",
                        "one_line_description",
                        "main_roles",
                        "pre_checks",
                        "hazards",
                        "manual_steps",
                        "core_warning",
                    )
                )
            ):
                merged = dict(nested_payload)
                if body.get("model") and not merged.get("model"):
                    merged["model"] = body["model"]
                return merged
            nested_answer = cls._extract_json_string_field(nested, "answer")
            if nested_answer:
                merged = dict(body)
                merged["answer"] = nested_answer
                return merged
        return body

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any] | None:
        stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
        decoder = json.JSONDecoder()
        for match in re.finditer(r"\{", stripped):
            try:
                value, _ = decoder.raw_decode(stripped[match.start() :])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
        return None

    @staticmethod
    def _extract_json_string_field(text: str, key: str) -> str | None:
        stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
        match = re.search(rf'"{re.escape(key)}"\s*:\s*', stripped)
        if not match:
            return None
        try:
            value, _ = json.JSONDecoder().raw_decode(stripped[match.end() :])
        except json.JSONDecodeError:
            return None
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return None

    @classmethod
    def _normalize_evidence_references(
        cls,
        value: Any,
        sources: list[Any],
    ) -> Any:
        if isinstance(value, dict):
            normalized: dict[str, Any] = {}
            for key, nested in value.items():
                if key in {"evidence_chunk_ids", "used_source_ids"}:
                    normalized[key] = cls._normalize_source_id_list(nested, sources)
                else:
                    normalized[key] = cls._normalize_evidence_references(
                        nested,
                        sources,
                    )
            return normalized
        if isinstance(value, list):
            return [cls._normalize_evidence_references(item, sources) for item in value]
        return value

    @classmethod
    def _normalize_source_id_list(
        cls,
        value: Any,
        sources: list[Any],
    ) -> list[str]:
        values = value if isinstance(value, list) else []
        normalized: list[str] = []
        for item in values:
            chunk_id = cls._normalize_source_id(item, sources)
            if chunk_id and chunk_id not in normalized:
                normalized.append(chunk_id)
        return normalized

    @staticmethod
    def _normalize_source_id(value: Any, sources: list[Any]) -> str | None:
        source_ids = [str(source.chunk_id) for source in sources]
        if isinstance(value, int):
            index = value
        elif isinstance(value, str):
            stripped = value.strip()
            if stripped in source_ids:
                return stripped
            match = re.fullmatch(r"\[?\s*(\d+)\s*\]?", stripped)
            if not match:
                return None
            index = int(match.group(1))
        else:
            return None
        if 1 <= index <= len(source_ids):
            return source_ids[index - 1]
        return None

    @classmethod
    def _collect_evidence_ids(cls, value: Any) -> set[str]:
        found: set[str] = set()
        if isinstance(value, dict):
            for key, nested in value.items():
                if key == "evidence_chunk_ids" and isinstance(nested, list):
                    found.update(str(item) for item in nested if item)
                else:
                    found.update(cls._collect_evidence_ids(nested))
        elif isinstance(value, list):
            for nested in value:
                found.update(cls._collect_evidence_ids(nested))
        return found

    @classmethod
    def _normalize_analysis_payload(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}

        def first(*names: str) -> Any:
            return next(
                (
                    value[name]
                    for name in names
                    if name in value and value[name] is not None
                ),
                None,
            )

        intent = str(
            first("question_intent", "intent", "answer_type") or ""
        ).strip()
        if intent not in {
            "document_qa",
            "maintenance_guide",
            "component_info",
            "clarification_required",
        }:
            intent = ""

        confidence_value = first(
            "intent_confidence",
            "confidence",
            "intent_score",
        )
        try:
            confidence = (
                min(max(float(confidence_value), 0.0), 1.0)
                if confidence_value is not None
                else None
            )
        except (TypeError, ValueError):
            confidence = None

        return {
            "question_intent": intent or None,
            "intent_confidence": confidence,
            "clarification_question": cls._optional_text(
                first("clarification_question", "follow_up_question")
            ),
            "occurrence_type": cls._optional_text(
                first("occurrence_type", "accident_type", "incident_type")
            ),
            "work_type": cls._optional_text(
                first("work_type", "task_type", "maintenance_action")
            ),
            "equipment": cls._string_list(
                first("equipment", "equipments", "equipment_name", "target_equipment"),
                limit=20,
            ),
            "component": cls._string_list(
                first("component", "components", "component_name", "target_component"),
                limit=20,
            ),
            "explicit_risk_factors": cls._string_list(
                first(
                    "explicit_risk_factors",
                    "risk_factors",
                    "hazards",
                    "explicit_hazards",
                ),
                limit=20,
            ),
            "energy_sources": cls._string_list(
                first("energy_sources", "energy_source"),
                limit=20,
            ),
            "search_keywords": cls._string_list(
                first("search_keywords", "keywords", "retrieval_keywords"),
                limit=30,
            ),
        }

    @staticmethod
    def _optional_text(value: Any) -> str | None:
        if value is None:
            return None
        normalized = " ".join(str(value).split())
        return normalized or None

    @staticmethod
    def _string_list(value: Any, *, limit: int) -> list[str]:
        values = value if isinstance(value, (list, tuple, set)) else [value]
        normalized: list[str] = []
        for item in values:
            if item is None or isinstance(item, (dict, list, tuple, set)):
                continue
            text = " ".join(str(item).split())
            if text and text not in normalized:
                normalized.append(text)
            if len(normalized) >= limit:
                break
        return normalized

    @classmethod
    def _source_ids_from_citations(
        cls,
        answer: str,
        sources: list[Any],
    ) -> list[str]:
        return cls._normalize_source_id_list(
            [
                int(match)
                for match in re.findall(r"\[\s*(\d+)\s*\]", answer)
            ],
            sources,
        )

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "ngrok-skip-browser-warning": "true",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    @staticmethod
    def _compact_error_text(text: str, limit: int = 300) -> str | None:
        compacted = " ".join(text.split())
        if not compacted:
            return None
        if len(compacted) > limit:
            return compacted[: limit - 3].rstrip() + "..."
        return compacted

    @staticmethod
    def _safe_context(request: ChatRequest) -> dict[str, Any]:
        return request.context.model_dump(
            mode="json",
            exclude={
                "registered_manuals",
                "selected_document_ids",
                "selected_document_version_ids",
                "visual_summary",
                "visual_categories",
                "visual_features",
            },
        )

    @staticmethod
    def _source_payload(source: Any) -> dict[str, Any]:
        return {
            "document_id": source.document_id,
            "document_version_id": source.document_version_id,
            "chunk_id": source.chunk_id,
            "title": source.title,
            "source_type": canonical_document_type(source.source_type),
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
            "document_profile": getattr(source, "document_profile", None),
        }
