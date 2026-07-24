from __future__ import annotations

import asyncio
import json
import logging
import re
from time import perf_counter
from uuid import uuid4

import httpx
from openai import OpenAIError

from app.core.config import settings
from app.schemas.chat import (
    AccidentClassification,
    AnswerType,
    ChatRequest,
    ChatResponse,
    ChatSource,
    QueryAnalysis,
    RetrievalAccessScope,
    StructuredAnswer,
)
from app.services.accident_classifier import (
    AccidentClassifierClient,
    AccidentClassifierError,
)
from app.services.ai import AIConfigurationError, AIService
from app.services.evidence_policy import RAG_UNAVAILABLE_WARNING
from app.services.document_types import (
    MANUAL_DOCUMENT_TYPES,
    PUBLIC_REFERENCE_DOCUMENT_TYPES,
    canonical_document_type,
)
from app.services.qwen import QwenAnswerFailure, QwenClient
from app.services.question_intent import classify_question_intent
from app.services.structured_answers import (
    clarification_answer,
    clarification_details,
    enriched_structured_answer,
    no_evidence_answer,
    no_evidence_details,
    source_based_checklist_items,
    source_based_fallback,
    validated_checklist_items,
    validated_structured_answer,
)


ANALYZER_FALLBACK_WARNING = (
    "상황 분석 모델을 사용할 수 없어 입력값 기반 검색어로 안전하게 대체했습니다."
)
LLM_FALLBACK_WARNING = (
    "외부 LLM 답변 생성에 실패해 검색 서비스의 근거 기반 기본 안내를 표시합니다."
)
COMPANY_LLM_WARNING = (
    "회사 문서 내용은 외부 LLM으로 전송하지 않았습니다."
)
LOCAL_VISION_LLM_WARNING = (
    "로컬 이미지 분석 내용은 외부 LLM으로 전송하지 않았습니다."
)
QWEN_UNAVAILABLE_WARNING = (
    "Qwen 답변 생성을 사용할 수 없어 BGE-M3 검색 근거 기반 안내로 대체했습니다."
)
QWEN_COMPANY_CONTEXT_WARNING = (
    "Qwen company-context sharing is disabled; using the retrieved evidence template."
)
CLASSIFIER_FALLBACK_WARNING = (
    "팀 Qwen 사고유형 분류기를 사용할 수 없어 기존 검색 분석으로 대체했습니다."
)
QWEN_SOURCE_LIMIT_BY_TYPE: dict[AnswerType, int] = {
    "maintenance_guide": settings.qwen_maintenance_source_limit,
    "document_qa": settings.qwen_document_source_limit,
    "component_info": settings.qwen_component_source_limit,
    "no_evidence": 0,
    "clarification_required": 0,
}
QWEN_CONTEXT_SIGNAL_TERMS = (
    "위험",
    "안전",
    "주의",
    "경고",
    "금지",
    "확인",
    "점검",
    "검사",
    "설치",
    "교체",
    "청소",
    "세척",
    "정렬",
    "설정",
    "차단",
    "격리",
    "재가동",
    "인터락",
    "정지",
    "hazard",
    "safety",
    "warning",
    "caution",
    "inspect",
    "check",
    "install",
    "replace",
    "clean",
    "lockout",
    "tagout",
    "interlock",
)
QWEN_MAINTENANCE_SOURCE_TYPES = MANUAL_DOCUMENT_TYPES
QWEN_PUBLIC_REFERENCE_TYPES = PUBLIC_REFERENCE_DOCUMENT_TYPES
QWEN_PUBLIC_INCIDENT_TYPES = frozenset({"public_incident"})
QWEN_RELEVANCE_STOPWORDS = frozenset(
    {
        "그거",
        "관련",
        "방법",
        "내용",
        "알려줘",
        "해야",
        "하려고",
        "예정",
        "어떻게",
        "무슨",
        "확인",
        "작업",
        "설비",
        "기계",
        "the",
        "and",
        "for",
        "with",
    }
)
QWEN_MAINTENANCE_SOURCE_SIGNAL_TERMS = (
    "정지",
    "차단",
    "격리",
    "잠금",
    "재가동",
    "운전",
    "위험구역",
    "위험 지역",
    "방호",
    "비상정지",
    "인터락",
    "끼임",
    "협착",
    "감전",
    "추락",
    "낙하",
    "화재",
    "폭발",
    "질식",
    "회전",
    "잔류",
    "압력",
    "lockout",
    "tagout",
    "interlock",
)
QWEN_MAINTENANCE_ACTION_SIGNAL_TERMS = (
    "설치",
    "점검",
    "검사",
    "청소",
    "세척",
    "제거",
    "교체",
    "정비",
    "보수",
    "조정",
    "분리",
    "연결",
    "inspect",
    "check",
    "clean",
    "replace",
    "repair",
)

logger = logging.getLogger(__name__)


class ChatService:
    def __init__(
        self,
        service_url: str | None = None,
        timeout_seconds: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        ai_service: AIService | None = None,
        openai_enabled: bool | None = None,
        qwen_client: QwenClient | None = None,
        qwen_enabled: bool | None = None,
        qwen_allow_company_context: bool | None = None,
        qwen_intent_classify_enabled: bool | None = None,
        classifier_client: AccidentClassifierClient | None = None,
        classifier_enabled: bool | None = None,
    ) -> None:
        self.service_url = (service_url or "").rstrip("/")
        self.timeout_seconds = timeout_seconds or settings.rag_request_timeout_seconds
        self.transport = transport
        self.ai_service = ai_service or AIService()
        self.openai_enabled = (
            (settings.allow_external_llm or settings.llm_is_local)
            and bool(settings.openai_api_key or settings.llm_base_url)
            if openai_enabled is None
            else openai_enabled
        )
        self.qwen_enabled = (
            settings.qwen_enabled and bool(settings.qwen_service_url)
            if qwen_enabled is None
            else qwen_enabled
        )
        self.qwen_client = (
            qwen_client
            if qwen_client is not None
            else (QwenClient() if self.qwen_enabled else None)
        )
        self.qwen_allow_company_context = (
            settings.qwen_allow_company_context
            if qwen_allow_company_context is None
            else qwen_allow_company_context
        )
        self.qwen_intent_classify_enabled = (
            settings.qwen_intent_classify_enabled
            if qwen_intent_classify_enabled is None
            else qwen_intent_classify_enabled
        )
        self.classifier_client = classifier_client or AccidentClassifierClient(
            settings.qwen_classifier_url
        )
        self.classifier_enabled = (
            settings.qwen_classifier_enabled
            and bool(settings.qwen_classifier_url)
            if classifier_enabled is None
            else classifier_enabled
        )

    async def answer(
        self,
        request: ChatRequest,
        access_scope: RetrievalAccessScope | None = None,
        request_id: str | None = None,
    ) -> ChatResponse:
        started_at = perf_counter()
        resolved_request_id = request_id or str(uuid4())
        explicit_question_intent = bool(
            request.analysis and request.analysis.question_intent
        )
        use_qwen = self.qwen_enabled and self.qwen_client is not None
        classification, classifier_fell_back = await self._classify(request)
        analyzed_request, analyzer_fell_back = await self._analyze(
            request,
            allow_external=(
                not use_qwen
                and (
                    settings.llm_is_local
                    or not bool(access_scope and access_scope.allow_company)
                )
            ),
        )
        if classification is not None:
            analyzed_request = self._apply_classification(
                analyzed_request, classification
            )
        elif use_qwen and self.qwen_intent_classify_enabled:
            qwen_analysis = await self.qwen_client.classify(analyzed_request)
            if qwen_analysis is not None:
                analyzed_request = analyzed_request.model_copy(
                    update={
                        "analysis": self._merge_qwen_analysis(
                            analyzed_request.analysis,
                            qwen_analysis,
                            preserve_intent=explicit_question_intent,
                        )
                    }
                )
        # Image-identification questions must use the latest local Vision result.
        # Do this before RAG so an unrelated selected manual cannot replace the
        # current photo with stale document evidence (for example, a bearing manual).
        visual_answer = self._visual_answer(analyzed_request)
        if visual_answer:
            response = ChatResponse(
                answer=visual_answer,
                sources=[],
                retrieval_mode="safety-fallback",
                generation_mode="template",
                warning=(
                    "사진에서 직접 관찰한 일반 형상과 로컬 분석 결과이며, "
                    "정확한 제품·모델·규격을 확정한 결과가 아닙니다."
                ),
                accident_classification=classification,
            )
            self._log_response(
                resolved_request_id,
                analyzed_request,
                response,
                started_at=started_at,
                qwen_used=False,
                fallback_reason="vision_answer",
            )
            return response

        answer_type = self._resolved_answer_type(analyzed_request)
        if answer_type == "clarification_required":
            response = self._clarification_response(analyzed_request)
            self._log_response(
                resolved_request_id,
                analyzed_request,
                response,
                started_at=started_at,
                qwen_used=use_qwen,
                fallback_reason="low_intent_confidence",
            )
            return response
        retrieval_response = await self._retrieve(
            analyzed_request,
            access_scope,
            request_id=resolved_request_id,
        )
        if not retrieval_response.sources:
            retrieval_response = self._no_evidence_response(
                retrieval_response,
                work_related=answer_type == "maintenance_guide",
            )
        else:
            retrieval_response = retrieval_response.model_copy(
                update={
                    "answer_type": answer_type,
                    "structured_answer": source_based_fallback(
                        answer_type,
                        retrieval_response.sources,
                        question=analyzed_request.question,
                    ),
                    "checklist_items": source_based_checklist_items(
                        answer_type,
                        retrieval_response.sources,
                        question=analyzed_request.question,
                    ),
                }
            )
        if classification is not None:
            retrieval_response = retrieval_response.model_copy(
                update={"accident_classification": classification}
            )
        if classifier_fell_back:
            retrieval_response = retrieval_response.model_copy(
                update={
                    "warning": self._append_warning(
                        retrieval_response.warning, CLASSIFIER_FALLBACK_WARNING
                    )
                }
            )
        if analyzer_fell_back and self.openai_enabled and not use_qwen:
            retrieval_response = retrieval_response.model_copy(
                update={
                    "warning": self._append_warning(
                        retrieval_response.warning, ANALYZER_FALLBACK_WARNING
                    )
                }
            )

        if use_qwen:
            response = await self._answer_with_qwen(
                analyzed_request,
                retrieval_response,
                request_id=resolved_request_id,
            )
            self._log_response(
                resolved_request_id,
                analyzed_request,
                response,
                started_at=started_at,
                qwen_used=response.generation_mode == "qwen",
                fallback_reason=(
                    None if response.generation_mode == "qwen" else "qwen_fallback"
                ),
            )
            return response

        if not retrieval_response.sources or not self.openai_enabled:
            self._log_response(
                resolved_request_id,
                analyzed_request,
                retrieval_response,
                started_at=started_at,
                qwen_used=False,
                fallback_reason=(
                    "no_evidence" if not retrieval_response.sources else "llm_disabled"
                ),
            )
            return retrieval_response

        # visual_summary is produced locally but is still supplied by the client.
        # Never forward OCR, labels, or image-derived text to an external provider.
        if analyzed_request.context.visual_summary and not settings.llm_is_local:
            response = retrieval_response.model_copy(
                update={
                    "warning": self._append_warning(
                        retrieval_response.warning, LOCAL_VISION_LLM_WARNING
                    )
                }
            )
            self._log_response(
                resolved_request_id,
                analyzed_request,
                response,
                started_at=started_at,
                qwen_used=False,
                fallback_reason="external_vision_context_blocked",
            )
            return response

        # This is intentionally unconditional: no company evidence is sent to
        # an external provider, even if a legacy environment flag says otherwise.
        if not settings.llm_is_local and any(
            source.document_scope == "company"
            for source in retrieval_response.sources
        ):
            response = retrieval_response.model_copy(
                update={
                    "warning": self._append_warning(
                        retrieval_response.warning, COMPANY_LLM_WARNING
                    )
                }
            )
            self._log_response(
                resolved_request_id,
                analyzed_request,
                response,
                started_at=started_at,
                qwen_used=False,
                fallback_reason="external_company_context_blocked",
            )
            return response

        try:
            answer = await asyncio.to_thread(
                self.ai_service.answer,
                request.question,
                self._build_grounded_context(analyzed_request, retrieval_response),
            )
        except (AIConfigurationError, OpenAIError, RuntimeError, ValueError):
            response = retrieval_response.model_copy(
                update={
                    "warning": self._append_warning(
                        retrieval_response.warning, LLM_FALLBACK_WARNING
                    )
                }
            )
            self._log_response(
                resolved_request_id,
                analyzed_request,
                response,
                started_at=started_at,
                qwen_used=False,
                fallback_reason="openai_failure",
            )
            return response

        response = retrieval_response.model_copy(
            update={
                "answer": answer,
                "generation_mode": "openai",
                "model": settings.llm_answer_model,
            }
        )
        self._log_response(
            resolved_request_id,
            analyzed_request,
            response,
            started_at=started_at,
            qwen_used=False,
            fallback_reason=None,
        )
        return response

    async def _answer_with_qwen(
        self,
        request: ChatRequest,
        retrieval_response: ChatResponse,
        *,
        request_id: str | None = None,
    ) -> ChatResponse:
        if not retrieval_response.sources or self.qwen_client is None:
            return retrieval_response
        if (
            not self.qwen_allow_company_context
            and any(
                source.document_scope == "company"
                for source in retrieval_response.sources
            )
        ):
            return retrieval_response.model_copy(
                update={
                    "warning": self._append_warning(
                        retrieval_response.warning, QWEN_COMPANY_CONTEXT_WARNING
                    )
                }
            )
        qwen_response = self._qwen_context_response(request, retrieval_response)
        qwen_started_at = perf_counter()
        qwen_answer = await self.qwen_client.answer(request, qwen_response)
        if qwen_answer is None:
            self._log_qwen_stage(
                request_id,
                qwen_response,
                qwen_started_at,
                fallback_reason="unavailable",
            )
            return retrieval_response.model_copy(
                update={
                    "warning": self._append_warning(
                        retrieval_response.warning, QWEN_UNAVAILABLE_WARNING
                    )
                }
            )
        if isinstance(qwen_answer, QwenAnswerFailure):
            self._log_qwen_stage(
                request_id,
                qwen_response,
                qwen_started_at,
                fallback_reason=qwen_answer.reason,
            )
            return retrieval_response.model_copy(
                update={
                    "warning": self._append_warning(
                        retrieval_response.warning,
                        self._qwen_failure_warning(qwen_answer),
                    )
                }
            )
        allowed_source_ids = {source.chunk_id for source in retrieval_response.sources}
        if qwen_answer.used_source_ids and not set(
            qwen_answer.used_source_ids
        ).issubset(allowed_source_ids):
            self._log_qwen_stage(
                request_id,
                qwen_response,
                qwen_started_at,
                fallback_reason="invalid_source_reference",
            )
            return retrieval_response.model_copy(
                update={
                    "warning": self._append_warning(
                        retrieval_response.warning,
                        "Qwen이 검색되지 않은 출처를 참조해 구조화 결과를 사용하지 않았습니다.",
                    )
                }
            )
        if not qwen_answer.used_source_ids:
            self._log_qwen_stage(
                request_id,
                qwen_response,
                qwen_started_at,
                fallback_reason="missing_source_citation",
            )
            return retrieval_response.model_copy(
                update={
                    "warning": self._append_warning(
                        retrieval_response.warning,
                        "Qwen 답변에 검증 가능한 출처 인용이 없어 자연어 답변을 사용하지 않았습니다.",
                    )
                }
            )
        structured_answer = validated_structured_answer(
            qwen_answer.structured_answer,
            expected_type=retrieval_response.answer_type or "no_evidence",
            sources=retrieval_response.sources,
            question=request.question,
        )
        structured_answer = enriched_structured_answer(
            structured_answer,
            retrieval_response.structured_answer,
            expected_type=retrieval_response.answer_type or "no_evidence",
        )
        checklist_items = (
            validated_checklist_items(
                qwen_answer.checklist_items,
                sources=retrieval_response.sources,
            )
            if retrieval_response.answer_type == "maintenance_guide"
            else []
        )
        unvalidated_item_count = self._structured_item_count(
            qwen_answer.structured_answer
        ) + len(qwen_answer.checklist_items)
        validated_item_count = self._structured_item_count(
            structured_answer
        ) + len(checklist_items)
        answer = self._remap_answer_citations(
            qwen_answer.answer,
            qwen_response.sources,
            retrieval_response.sources,
        )
        self._log_qwen_stage(
            request_id,
            qwen_response,
            qwen_started_at,
            fallback_reason=None,
            removed_item_count=max(
                0, unvalidated_item_count - validated_item_count
            ),
        )
        return retrieval_response.model_copy(
            update={
                "answer": answer,
                "generation_mode": "qwen",
                "model": qwen_answer.model or "qwen",
                "structured_answer": structured_answer,
                "checklist_items": checklist_items,
            }
        )

    @staticmethod
    def _structured_item_count(value: StructuredAnswer | None) -> int:
        if value is None:
            return 0
        dumped = value.model_dump(mode="python")
        count = 0
        for field_value in dumped.values():
            if isinstance(field_value, list):
                count += len(field_value)
            elif isinstance(field_value, dict):
                count += sum(
                    len(nested)
                    for nested in field_value.values()
                    if isinstance(nested, list)
                )
        return count

    @staticmethod
    def _log_qwen_stage(
        request_id: str | None,
        response: ChatResponse,
        started_at: float,
        *,
        fallback_reason: str | None,
        removed_item_count: int = 0,
    ) -> None:
        logger.info(
            "chat_qwen %s",
            json.dumps(
                {
                    "request_id": request_id,
                    "provider": settings.qwen_provider,
                    "answer_type": response.answer_type,
                    "evidence_count": len(response.sources),
                    "response_ms": round(
                        (perf_counter() - started_at) * 1000, 2
                    ),
                    "validation_removed_item_count": removed_item_count,
                    "fallback": fallback_reason is not None,
                    "fallback_reason": fallback_reason,
                },
                ensure_ascii=False,
            ),
        )

    @staticmethod
    def _qwen_failure_warning(failure: QwenAnswerFailure) -> str:
        message = (
            f"Qwen 답변 생성 실패({failure.reason})로 "
            "BGE-M3 검색 근거 기반 안내로 대체했습니다."
        )
        if failure.detail:
            message = f"{message} 상세: {failure.detail}"
        return message

    @staticmethod
    def _qwen_context_response(
        request: ChatRequest,
        response: ChatResponse,
    ) -> ChatResponse:
        answer_type = response.answer_type or "no_evidence"
        limit = QWEN_SOURCE_LIMIT_BY_TYPE.get(answer_type, 1)
        if limit <= 0:
            return response.model_copy(update={"sources": []})
        max_sentences = 2 if answer_type == "maintenance_guide" else 3
        if answer_type == "maintenance_guide":
            selected_sources = ChatService._select_maintenance_qwen_sources(
                request.question,
                response.sources,
                limit=limit,
            )
        else:
            selected_sources = response.sources[:limit]
        return response.model_copy(
            update={
                "sources": [
                    ChatService._compact_qwen_source(
                        source,
                        request.question,
                        max_sentences=max_sentences,
                    )
                    for source in selected_sources
                ]
            }
        )

    @staticmethod
    def _select_maintenance_qwen_sources(
        question: str,
        sources: list[ChatSource],
        *,
        limit: int,
    ) -> list[ChatSource]:
        if limit <= 0:
            return []
        if len(sources) <= limit:
            return sources
        question_terms = ChatService._relevance_terms(question)
        selected: list[ChatSource] = []
        selected_indexes: set[int] = set()
        used_doc_pages: set[tuple[str, int | None, int | None]] = set()
        used_groups: set[str] = set()

        for _ in range(min(limit, len(sources))):
            best: tuple[float, int, ChatSource] | None = None
            for index, source in enumerate(sources):
                if index in selected_indexes:
                    continue
                score = ChatService._maintenance_source_score(source, question_terms)
                if ChatService._source_doc_page_key(source) in used_doc_pages:
                    score -= 0.35
                group = ChatService._source_group(source)
                if group in used_groups:
                    score -= 0.08
                if selected and group not in used_groups:
                    score += 0.12
                candidate = (score, -index, source)
                if best is None or candidate > best:
                    best = candidate
            if best is None:
                break
            _, negative_index, source = best
            selected.append(source)
            selected_indexes.add(-negative_index)
            used_doc_pages.add(ChatService._source_doc_page_key(source))
            used_groups.add(ChatService._source_group(source))
        return selected

    @staticmethod
    def _maintenance_source_score(
        source: ChatSource,
        question_terms: set[str],
    ) -> float:
        text = ChatService._source_search_text(source)
        source_terms = ChatService._relevance_terms(text)
        overlap = len(question_terms & source_terms) / max(len(question_terms), 1)
        base_score = max(
            ChatService._normalized_score(source.reranker_score),
            ChatService._normalized_score(source.retrieval_score),
            ChatService._normalized_score(source.similarity),
        )
        signal_hits = sum(
            1 for term in QWEN_MAINTENANCE_SOURCE_SIGNAL_TERMS if term.casefold() in text
        )
        action_hits = sum(
            1 for term in QWEN_MAINTENANCE_ACTION_SIGNAL_TERMS if term.casefold() in text
        )
        source_type = source.source_type.casefold()
        source_type = canonical_document_type(source_type)
        type_bonus = 0.0
        if source_type in QWEN_PUBLIC_INCIDENT_TYPES:
            type_bonus = 0.18
        elif source_type in QWEN_MAINTENANCE_SOURCE_TYPES:
            type_bonus = 0.12
        elif source_type in QWEN_PUBLIC_REFERENCE_TYPES:
            type_bonus = 0.08
        return (
            base_score
            + overlap * 0.35
            + min(signal_hits, 4) * 0.035
            + min(action_hits, 3) * 0.025
            + type_bonus
            + ChatService._source_excerpt_quality(source.excerpt)
        )

    @staticmethod
    def _normalized_score(value: float | None) -> float:
        if value is None:
            return 0.0
        score = float(value)
        if score > 1:
            score = score / 100
        return max(min(score, 1.0), -1.0)

    @staticmethod
    def _source_group(source: ChatSource) -> str:
        source_type = canonical_document_type(source.source_type)
        if source_type in QWEN_PUBLIC_INCIDENT_TYPES:
            return "incident"
        if source_type in QWEN_MAINTENANCE_SOURCE_TYPES:
            return "manual"
        if source_type in QWEN_PUBLIC_REFERENCE_TYPES:
            return "public_reference"
        return source_type or "other"

    @staticmethod
    def _source_doc_page_key(
        source: ChatSource,
    ) -> tuple[str, int | None, int | None]:
        return (
            source.document_id,
            source.page_start or source.page,
            source.page_end,
        )

    @staticmethod
    def _source_search_text(source: ChatSource) -> str:
        return " ".join(
            str(value)
            for value in (
                source.title,
                source.source_type,
                source.section,
                source.original_filename,
                source.excerpt,
            )
            if value
        ).casefold()

    @staticmethod
    def _relevance_terms(text: str) -> set[str]:
        terms: set[str] = set()
        for token in re.findall(r"[0-9A-Za-z가-힣_-]+", text.casefold()):
            token = token.strip("_-")
            if len(token) < 2 or token in QWEN_RELEVANCE_STOPWORDS:
                continue
            terms.add(token)
        return terms

    @staticmethod
    def _source_excerpt_quality(excerpt: str) -> float:
        cleaned = ChatService._clean_qwen_excerpt(excerpt)
        if not cleaned:
            return -0.2
        penalty = 0.0
        if "|" in excerpt:
            penalty += 0.08
        if "[자료유형]" in excerpt or "[내용]" in excerpt:
            penalty += 0.04
        if len(cleaned) < 24:
            penalty += 0.08
        return -penalty

    @staticmethod
    def _compact_qwen_source(
        source: ChatSource,
        question: str,
        *,
        max_sentences: int = 3,
    ) -> ChatSource:
        excerpt = ChatService._focused_excerpt(
            source.excerpt,
            question,
            max_sentences=max_sentences,
        )
        if len(excerpt) > settings.qwen_source_excerpt_chars:
            excerpt = excerpt[: settings.qwen_source_excerpt_chars].rstrip() + "..."
        return source.model_copy(update={"excerpt": excerpt})

    @staticmethod
    def _focused_excerpt(
        excerpt: str,
        question: str,
        *,
        max_sentences: int = 3,
    ) -> str:
        normalized = ChatService._clean_qwen_excerpt(excerpt)
        if not normalized:
            return normalized
        sentences = [
            sentence.strip()
            for sentence in ChatService._split_qwen_sentences(normalized)
            if ChatService._is_useful_qwen_sentence(sentence.strip())
        ]
        if not sentences:
            return normalized
        question_terms = {
            token.casefold()
            for token in re.findall(r"[0-9A-Za-z가-힣_-]+", question)
            if len(token) >= 2
        }

        def score(indexed_sentence: tuple[int, str]) -> tuple[float, int]:
            index, sentence = indexed_sentence
            text = sentence.casefold()
            term_hits = sum(1 for term in question_terms if term in text)
            signal_hits = sum(
                1 for term in QWEN_CONTEXT_SIGNAL_TERMS if term.casefold() in text
            )
            length_penalty = 0.0 if len(sentence) <= 240 else 0.5
            return (term_hits * 2.0 + signal_hits - length_penalty, -index)

        ranked = sorted(enumerate(sentences), key=score, reverse=True)
        selected_indexes = sorted(index for index, _ in ranked[:max_sentences])
        focused = " ".join(sentences[index] for index in selected_indexes).strip()
        return focused or normalized

    @staticmethod
    def _clean_qwen_excerpt(excerpt: str) -> str:
        text = " ".join(excerpt.split())
        text = re.sub(r"\[자료유형\]\s*.*?\[내용\]\s*", "", text)
        text = re.sub(r"\[제목\]\s*", "", text)
        text = re.sub(r"\s*\|\s*", " ", text)
        text = re.sub(r"\bNo\.\s*점검\s*항목\s*확인\b", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\b점검\s*항목\s*확인\b", "", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    @staticmethod
    def _split_qwen_sentences(text: str) -> list[str]:
        normalized = re.sub(r"(?<=[다요함음됨임])\.\s+", ".\n", text)
        normalized = re.sub(r"(?<=[.!?。])\s+", "\n", normalized)
        pieces = re.split(r"\n+|(?<=다\.)\s+|(?<=요\.)\s+", normalized)
        return [piece.strip() for piece in pieces if piece.strip()]

    @staticmethod
    def _is_useful_qwen_sentence(sentence: str) -> bool:
        text = " ".join(sentence.split())
        if len(text) < 10:
            return False
        if re.fullmatch(r"[\d\s.\-()]+", text):
            return False
        if re.match(r"^(?:및|또는|이|그|해당|검색된|있어|되어|하여야)\s+", text):
            return False
        if text.count("|") >= 2:
            return False
        return bool(re.search(r"[A-Za-z가-힣]", text))

    async def _classify(
        self,
        request: ChatRequest,
    ) -> tuple[AccidentClassification | None, bool]:
        if not self.classifier_enabled:
            return None, False
        try:
            return await self.classifier_client.classify(request), False
        except AccidentClassifierError:
            return None, True

    @staticmethod
    def _apply_classification(
        request: ChatRequest,
        classification: AccidentClassification,
    ) -> ChatRequest:
        analysis = request.analysis or QueryAnalysis()
        keywords = list(
            dict.fromkeys([classification.label, *analysis.search_keywords])
        )[:30]
        return request.model_copy(
            update={
                "analysis": analysis.model_copy(
                    update={
                        "occurrence_type": classification.label,
                        "search_keywords": keywords,
                    }
                )
            }
        )

    async def _analyze(
        self,
        request: ChatRequest,
        *,
        allow_external: bool = True,
    ) -> tuple[ChatRequest, bool]:
        fallback = self._fallback_analysis(request)
        if request.analysis is not None:
            return request.model_copy(
                update={
                    "analysis": self._merge_analysis(fallback, request.analysis)
                }
            ), False
        if (
            not allow_external
            or not self.openai_enabled
            or not hasattr(self.ai_service, "analyze")
        ):
            return request.model_copy(update={"analysis": fallback}), False
        try:
            analysis = await asyncio.to_thread(
                self.ai_service.analyze,
                request.question,
                self._analysis_context(request),
            )
            if not isinstance(analysis, QueryAnalysis):
                analysis = QueryAnalysis.model_validate(analysis)
            return request.model_copy(
                update={"analysis": self._merge_analysis(fallback, analysis)}
            ), False
        except (AIConfigurationError, OpenAIError, RuntimeError, ValueError):
            return request.model_copy(update={"analysis": fallback}), True

    async def _retrieve(
        self,
        request: ChatRequest,
        access_scope: RetrievalAccessScope | None = None,
        *,
        request_id: str | None = None,
    ) -> ChatResponse:
        if not self.service_url:
            return self._fallback(request)
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                request_body = request.model_dump(mode="json")
                request_body["access_scope"] = (
                    access_scope or RetrievalAccessScope()
                ).model_dump(mode="json")
                request_body["request_id"] = request_id
                response = await client.post(
                    f"{self.service_url}/v1/chat", json=request_body
                )
                response.raise_for_status()
                parsed = ChatResponse.model_validate(response.json())
                return parsed.model_copy(
                    update={
                        "sources": [
                            source.model_copy(
                                update={
                                    "source_type": canonical_document_type(
                                        source.source_type
                                    )
                                }
                            )
                            for source in parsed.sources
                        ]
                    }
                )
        except (httpx.HTTPError, ValueError):
            return self._fallback(request)

    @staticmethod
    def _analysis_context(request: ChatRequest) -> str:
        return json.dumps(
            request.context.model_dump(
                mode="json",
                exclude={
                    "registered_manuals",
                    "selected_document_ids",
                    "selected_document_version_ids",
                    "visual_summary",
                    "visual_categories",
                    "visual_features",
                },
            ),
            ensure_ascii=False,
        )

    @staticmethod
    def _fallback_analysis(request: ChatRequest) -> QueryAnalysis:
        raw_keywords = request.question
        keywords = list(
            dict.fromkeys(
                token.casefold()
                for token in re.findall(r"[0-9A-Za-z가-힣_-]+", raw_keywords)
                if len(token) >= 2
            )
        )[:30]
        intent = classify_question_intent(request)
        return QueryAnalysis(
            question_intent=intent.intent,
            intent_confidence=intent.confidence,
            clarification_question=intent.clarification_question,
            search_keywords=keywords,
        )

    @staticmethod
    def _merge_qwen_analysis(
        fallback: QueryAnalysis | None,
        qwen_analysis: QueryAnalysis,
        *,
        preserve_intent: bool = False,
    ) -> QueryAnalysis:
        base = fallback or QueryAnalysis()
        updates: dict[str, object] = {
            field: value
            for field in (
                "occurrence_type",
                "work_type",
                "equipment",
                "component",
                "explicit_risk_factors",
                "energy_sources",
                "search_keywords",
            )
            if (value := getattr(qwen_analysis, field))
        }
        if not preserve_intent and qwen_analysis.question_intent:
            confidence = qwen_analysis.intent_confidence
            if (
                confidence is not None
                and confidence < settings.question_intent_confidence_threshold
            ):
                updates.update(
                    {
                        "question_intent": "clarification_required",
                        "intent_confidence": confidence,
                        "clarification_question": (
                            qwen_analysis.clarification_question
                            or "질문의 목적을 정확히 확인해야 합니다. 부품 정보, 문서 내용, 설치·점검 방법 중 무엇이 필요한가요?"
                        ),
                    }
                )
            elif confidence is not None:
                updates.update(
                    {
                        "question_intent": qwen_analysis.question_intent,
                        "intent_confidence": confidence,
                        "clarification_question": qwen_analysis.clarification_question,
                    }
                )
        return base.model_copy(update=updates) if updates else base

    @staticmethod
    def _remap_answer_citations(
        answer: str,
        sent_sources: list[ChatSource],
        all_sources: list[ChatSource],
    ) -> str:
        global_numbers = {
            source.chunk_id: index
            for index, source in enumerate(all_sources, start=1)
        }
        local_to_global = {
            index: global_numbers[source.chunk_id]
            for index, source in enumerate(sent_sources, start=1)
            if source.chunk_id in global_numbers
        }
        return re.sub(
            r"\[\s*(\d+)\s*\]",
            lambda match: (
                f"[{local_to_global[int(match.group(1))]}]"
                if int(match.group(1)) in local_to_global
                else match.group(0)
            ),
            answer,
        )

    @staticmethod
    def _log_response(
        request_id: str,
        request: ChatRequest,
        response: ChatResponse,
        *,
        started_at: float,
        qwen_used: bool,
        fallback_reason: str | None,
    ) -> None:
        analysis = request.analysis or QueryAnalysis()
        bucket_counts: dict[str, int] = {}
        for source in response.sources:
            source_type = canonical_document_type(source.source_type)
            bucket = (
                "manual"
                if source_type in QWEN_MAINTENANCE_SOURCE_TYPES
                else source_type
            )
            bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
        logger.info(
            "chat_pipeline %s",
            json.dumps(
                {
                    "request_id": request_id,
                    "question_intent": response.answer_type,
                    "intent_confidence": analysis.intent_confidence,
                    "qwen_used": qwen_used,
                    "qwen_provider": settings.qwen_provider if qwen_used else None,
                    "equipment": analysis.equipment,
                    "component": analysis.component,
                    "work_type": analysis.work_type,
                    "selected_bucket_counts": bucket_counts,
                    "selected_sources": [
                        {
                            "document_id": source.document_id,
                            "chunk_id": source.chunk_id,
                            "document_type": canonical_document_type(
                                source.source_type
                            ),
                            "score": source.reranker_score,
                        }
                        for source in response.sources
                    ],
                    "evidence_count": len(response.sources),
                    "generation_mode": response.generation_mode,
                    "fallback": fallback_reason is not None,
                    "fallback_reason": fallback_reason,
                    "total_ms": round((perf_counter() - started_at) * 1000, 2),
                },
                ensure_ascii=False,
            ),
        )

    @staticmethod
    def _merge_analysis(
        fallback: QueryAnalysis,
        supplied: QueryAnalysis,
    ) -> QueryAnalysis:
        values = supplied.model_dump(mode="python")
        if not supplied.question_intent:
            values.update(
                {
                    "question_intent": fallback.question_intent,
                    "intent_confidence": fallback.intent_confidence,
                    "clarification_question": fallback.clarification_question,
                }
            )
        if not supplied.search_keywords:
            values["search_keywords"] = fallback.search_keywords
        return QueryAnalysis.model_validate(values)

    @staticmethod
    def _resolved_answer_type(request: ChatRequest) -> AnswerType:
        if request.analysis and request.analysis.question_intent:
            return request.analysis.question_intent
        return classify_question_intent(request).intent

    @staticmethod
    def _clarification_response(request: ChatRequest) -> ChatResponse:
        question = (
            request.analysis.clarification_question
            if request.analysis
            else None
        )
        details = clarification_details(question)
        return ChatResponse(
            answer=clarification_answer(details),
            answer_type="clarification_required",
            structured_answer=details,
            clarification_question=details.question,
            sources=[],
            retrieval_mode="safety-fallback",
        )

    @staticmethod
    def _no_evidence_response(
        response: ChatResponse,
        *,
        work_related: bool,
    ) -> ChatResponse:
        details = no_evidence_details(work_related=work_related)
        return response.model_copy(
            update={
                "answer": no_evidence_answer(work_related=work_related),
                "answer_type": "no_evidence",
                "structured_answer": details,
                "checklist_items": [],
            }
        )

    @staticmethod
    def _build_grounded_context(
        request: ChatRequest,
        response: ChatResponse,
    ) -> str:
        context = request.context
        fields = (
            ("사업장", context.site_name),
            ("설비", context.equipment_name),
            ("제조사", context.manufacturer),
            ("모델·부품번호", context.model_number),
            ("부품", context.component_name),
            ("작업 종류", context.task_type),
            ("에너지원", context.energy_source),
            ("작업 설명", context.task_description),
            (
                "로컬 이미지 분석(외형 후보이며 모델·규격 확정 근거가 아님)",
                context.visual_summary,
            ),
            (
                "사진 지시어 해석",
                "'이건 뭐야', '어디에 쓰여' 같은 짧은 질문은 최근 첨부 사진을 가리킴"
                if context.visual_summary
                else None,
            ),
        )
        lines = [f"{label}: {value}" for label, value in fields if value]
        if request.analysis and request.analysis.question_intent:
            lines.insert(0, f"답변 유형: {request.analysis.question_intent}")
        if request.analysis and request.analysis.occurrence_type:
            lines.append(
                "팀 Qwen LoRA 사고유형 예측(실험용): "
                f"{request.analysis.occurrence_type}"
            )
        lines.append("\n검증된 검색 근거:")
        for index, source in enumerate(response.sources, start=1):
            location = source.section or "section unknown"
            if source.page_start:
                location = f"{location}, page {source.page_start}"
                if source.page_end and source.page_end != source.page_start:
                    location += f"-{source.page_end}"
            lines.extend(
                (
                    f"[{index}] title: {source.title}",
                    f"[{index}] type/version: {source.source_type} / {source.document_version or 'legacy'}",
                    f"[{index}] location: {location}",
                    f"[{index}] evidence: {source.excerpt}",
                )
            )
        return "\n".join(lines)

    @staticmethod
    def _append_warning(current: str | None, additional: str) -> str:
        return f"{current} {additional}" if current else additional

    @staticmethod
    def _visual_answer(request: ChatRequest) -> str | None:
        question = request.question.casefold()
        is_photo_question = any(
            token in question
            for token in ("이건", "이것", "뭐", "무엇", "사진", "어디에 쓰", "용도", "부품")
        )
        if not is_photo_question:
            return None
        if not request.context.visual_categories:
            visual_summary = request.context.visual_summary or ""
            no_verified_result = any(
                marker in visual_summary
                for marker in (
                    "신뢰 임계값을 넘는 카탈로그 후보 없음",
                    "제품 종류를 확인하지 못",
                    "종류 확인 불가",
                )
            )
            if not no_verified_result:
                return None
            return (
                "현재 사진만으로는 제품 종류를 신뢰할 수 있게 확인하지 못했습니다.\n\n"
                "카탈로그 또는 정밀 비전 분석에서 검증된 후보가 없으므로 임의의 제품명이나 용도를 안내하지 않습니다. "
                "대상을 더 가까이 촬영하거나, 여러 각도의 사진과 제품 각인·라벨이 보이는 사진을 추가해 주세요."
            )
        categories = list(
            dict.fromkeys(
                value.strip()
                for value in request.context.visual_categories
                if value.strip()
            )
        )[:3]
        if not categories:
            return None
        primary = categories[0]
        features = list(
            dict.fromkeys(
                value.strip()
                for value in request.context.visual_features
                if value.strip()
            )
        )[:3]
        lines = [f"사진에서 보이는 일반 형상은 **{primary}**로 추정됩니다."]
        if features:
            lines.append("관찰 근거: " + ", ".join(features) + ".")
        if "어디에 쓰" in question or "용도" in question:
            usage = "부품을 서로 체결하는 용도"
            if "너트" in primary:
                usage = "볼트와 함께 부품을 조여 고정하는 용도"
            elif "와셔" in primary:
                usage = "체결 하중을 분산하거나 표면 손상을 줄이는 용도"
            elif "베어링" in primary:
                usage = "회전축을 지지하고 마찰을 줄이는 용도"
            elif "센서" in primary:
                usage = "상태나 물리량을 감지하는 용도"
            elif "카메라" in primary:
                usage = "대상을 촬영하거나 검사하는 용도"
            elif "usb" in primary.casefold() or "메모리" in primary:
                usage = "파일과 데이터를 저장하고 USB 포트가 있는 장치 사이에서 옮기는 용도"
            lines.append(f"일반적으로는 {usage}에 사용됩니다.")
        lines.append(
            "다만 현재 결과는 사진 형상에 대한 추정이므로, 정확한 제품명·규격·적용 위치는 각인과 카탈로그 표의 일치 여부를 추가로 확인해야 합니다."
        )
        return "\n\n".join(lines)

    @staticmethod
    def _fallback(request: ChatRequest) -> ChatResponse:
        intent = classify_question_intent(request).intent
        details = no_evidence_details(work_related=intent == "maintenance_guide")
        return ChatResponse(
            answer=no_evidence_answer(work_related=intent == "maintenance_guide"),
            answer_type="no_evidence",
            structured_answer=details,
            sources=[],
            retrieval_mode="safety-fallback",
            warning=RAG_UNAVAILABLE_WARNING,
        )


chat_service = ChatService(service_url=settings.rag_service_url)


def get_chat_service() -> ChatService:
    return chat_service
