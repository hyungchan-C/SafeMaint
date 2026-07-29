from __future__ import annotations

import asyncio
import json
import logging
import re
from time import perf_counter
from uuid import UUID, uuid4

import httpx
from openai import OpenAIError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import DocumentChunk
from app.repositories.document_chunks import DocumentChunkRepository
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
from app.services.pdf_outline import (
    OutlineChapter,
    chapter_title_for_page,
    document_outline_chapters,
)
from app.services.qwen import QwenAnswerFailure, QwenClient
from app.services.question_intent import classify_question_intent
from app.services.structured_answers import (
    checklist_items_from_pre_checks,
    clarification_answer,
    clarification_details,
    enriched_structured_answer,
    finalize_component_answer,
    finalize_document_answer,
    finalize_maintenance_answer,
    no_evidence_answer,
    no_evidence_details,
    repair_extracted_quantity_order,
    source_based_checklist_items,
    source_based_fallback,
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
    "광축",
    "투광부",
    "수광부",
    "투광기",
    "수광기",
    "방호장치",
    "검출",
    "안전거리",
    "방호구역",
    "OSSD",
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
# 질문의 핵심 설비명과 검색 문서의 표현이 다른 경우를 위한 검색 동의어다.
# 답변 문구를 고정하는 규칙이 아니라, 유사한 일반명 때문에 전혀 다른 설비
# (예: 라이트 커튼 ↔ 건축 커튼월)가 Qwen 근거로 전달되는 것을 막는 필터다.
QWEN_DOMAIN_PHRASE_GROUPS: tuple[
    tuple[tuple[str, ...], tuple[str, ...]], ...
] = (
    (
        (
            "라이트커튼",
            "라이트 커튼",
            "light curtain",
            "광전자식 방호장치",
            "광전자식방호장치",
        ),
        (
            "커튼월",
            "curtain wall",
            "벨트컨베이어",
            "벨트콘베이어",
            "컨베이어",
            "conveyor",
        ),
    ),
)
GENERIC_QWEN_FALLBACK_ANSWERS = frozenset(
    {
        "검색된 근거를 기준으로 작업 전 확인할 핵심 사항을 요약했습니다.",
        "검색된 문서 근거를 기준으로 유지보수 시 확인할 사항을 정리했습니다. 현장 안전관리자의 최종 확인 전에는 작업을 시작하지 마세요.",
        "검색된 문서 근거를 기준으로 확인 가능한 내용을 요약했습니다.",
        "검색된 문서 근거를 기준으로 질문과 관련된 내용을 요약했습니다.",
        "검색된 문서 근거에서 확인되는 부품 정보를 정리했습니다.",
        "검색된 문서 근거를 기준으로 부품 정보를 요약했습니다.",
    }
)

logger = logging.getLogger(__name__)


def _chat_source_from_chunk(
    chunk: DocumentChunk,
    *,
    outline_chapter_title: str | None = None,
) -> ChatSource:
    """Adapt a raw DB chunk into the shape the PDF page-matching helpers expect.

    Used only for the "search the whole document" pass in
    finalize_maintenance_answer (see _full_document_sources below) — this is
    never part of the retrieval result used to generate the natural-language
    answer, so score fields that don't apply here (similarity etc.) are left
    at 0.

    outline_chapter_title, when given, is the PDF's own bookmark title for the
    chapter this chunk's page falls under (see app.services.pdf_outline) — the
    chunk's own section_path only ever kept the leaf subsection heading, so
    without this a card-meaning search can never match a signal word that only
    appears in the parent chapter title.
    """

    document = chunk.document
    version = chunk.document_version
    leaf_section = " > ".join(chunk.section_path) if chunk.section_path else None
    section = (
        f"{outline_chapter_title} > {leaf_section}"
        if outline_chapter_title and leaf_section
        else outline_chapter_title or leaf_section
    )
    return ChatSource(
        document_id=str(chunk.document_id),
        document_version_id=(
            str(chunk.document_version_id) if chunk.document_version_id else None
        ),
        chunk_id=str(chunk.id),
        title=document.title if document else "",
        source_type=document.source_type if document else "",
        original_filename=version.original_filename if version else None,
        document_version=version.version_number if version else None,
        section=section,
        excerpt=chunk.content,
        page=chunk.page_number,
        page_start=chunk.page_start,
        page_end=chunk.page_end,
        similarity=0.0,
    )


def _with_page_reference_sources(
    sources: list[ChatSource],
    structured_answer: StructuredAnswer | None,
    full_document_sources: list[ChatSource] | None,
) -> list[ChatSource]:
    """Make sure PDF-page-card chunk ids resolve to a real source in the response.

    rating_performance_page_source_ids can point at chunks found by scanning the
    whole document (full_document_sources), which never went through retrieval
    and so are never in `sources`. The frontend looks up each id in `sources` to
    get a filename/page to render — without this, those ids resolve to nothing
    and the card renders empty even though the backend found the right pages.
    """

    if not full_document_sources:
        return sources
    page_ids = getattr(structured_answer, "rating_performance_page_source_ids", None)
    if not page_ids:
        return sources
    known_ids = {source.chunk_id for source in sources}
    by_id = {source.chunk_id: source for source in full_document_sources}
    extra = [
        by_id[page_id]
        for page_id in page_ids
        if page_id not in known_ids and page_id in by_id
    ]
    if not extra:
        return sources
    return [*sources, *extra]


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
        qwen_accident_classify_enabled: bool | None = None,
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
        self.qwen_accident_classify_enabled = (
            settings.qwen_accident_classify_enabled
            if qwen_accident_classify_enabled is None
            else qwen_accident_classify_enabled
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
        db: Session | None = None,
    ) -> ChatResponse:
        started_at = perf_counter()
        resolved_request_id = request_id or str(uuid4())
        explicit_question_intent = bool(
            request.analysis and request.analysis.question_intent
        )
        use_qwen = self.qwen_enabled and self.qwen_client is not None
        classification: AccidentClassification | None = None
        classifier_fell_back = False
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
        if (
            use_qwen
            and self.qwen_intent_classify_enabled
            and self._needs_qwen_intent_resolution(
                analyzed_request,
                explicit_question_intent=explicit_question_intent,
            )
        ):
            qwen_analysis = await self.qwen_client.classify_intent(analyzed_request)
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
        if answer_type == "maintenance_guide":
            classification, classifier_fell_back = await self._classify(request)
            if classification is not None:
                analyzed_request = self._apply_classification(
                    analyzed_request, classification
                )
            elif use_qwen and self.qwen_accident_classify_enabled:
                qwen_analysis = await self.qwen_client.classify(analyzed_request)
                if qwen_analysis is not None:
                    analyzed_request = analyzed_request.model_copy(
                        update={
                            "analysis": self._merge_qwen_analysis(
                                analyzed_request.analysis,
                                qwen_analysis,
                                preserve_intent=True,
                            )
                }
            )
        retrieval_response = await self._retrieve(
            analyzed_request,
            access_scope,
            request_id=resolved_request_id,
        )
        if self._should_reretrieve_with_primary_pdf(
            analyzed_request,
            retrieval_response,
        ):
            primary_pdf_request = self._request_scoped_to_primary_pdf(
                analyzed_request,
                retrieval_response,
            )
            if primary_pdf_request is not analyzed_request:
                primary_pdf_response = await self._retrieve(
                    primary_pdf_request,
                    access_scope,
                    request_id=resolved_request_id,
                )
                if primary_pdf_response.sources:
                    retrieval_response = primary_pdf_response
                    analyzed_request = primary_pdf_request
        retrieval_response = self._scope_pdf_sources_to_primary_document(
            retrieval_response,
            answer_type=answer_type,
        )
        full_document_sources: list[ChatSource] | None = None
        qwen_excluded_source_ids: frozenset[str] = frozenset()
        if not retrieval_response.sources:
            retrieval_response = self._no_evidence_response(
                retrieval_response,
                work_related=answer_type == "maintenance_guide",
            )
        else:
            structured_answer = source_based_fallback(
                answer_type,
                retrieval_response.sources,
                question=analyzed_request.question,
            )
            structured_answer = finalize_document_answer(
                structured_answer,
                sources=retrieval_response.sources,
                question=analyzed_request.question,
            )
            structured_answer = finalize_component_answer(
                structured_answer,
                sources=retrieval_response.sources,
                question=analyzed_request.question,
            )
            full_document_sources = await self._full_document_sources(
                retrieval_response.sources,
                answer_type,
                db,
            )
            structured_answer = finalize_maintenance_answer(
                structured_answer,
                sources=retrieval_response.sources,
                question=analyzed_request.question,
                analysis=analyzed_request.analysis,
                full_document_sources=full_document_sources,
            )
            narrow_source_ids = {
                source.chunk_id for source in retrieval_response.sources
            }
            qwen_excluded_source_ids = frozenset(
                page_id
                for page_id in getattr(
                    structured_answer, "rating_performance_page_source_ids", None
                )
                or ()
                if page_id not in narrow_source_ids
            )
            retrieval_response = retrieval_response.model_copy(
                update={
                    "answer_type": answer_type,
                    "structured_answer": structured_answer,
                    "sources": _with_page_reference_sources(
                        retrieval_response.sources,
                        structured_answer,
                        full_document_sources,
                    ),
                    "checklist_items": (
                        checklist_items_from_pre_checks(structured_answer)
                        if answer_type == "maintenance_guide"
                        else []
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
                db=db,
                full_document_sources=full_document_sources,
                qwen_excluded_source_ids=qwen_excluded_source_ids,
            )
            response = self._with_display_source_excerpts(
                response,
                analyzed_request.question,
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
            response = retrieval_response
            if retrieval_response.sources:
                response = retrieval_response.model_copy(
                    update={
                        "answer": self._short_grounded_answer(
                            analyzed_request,
                            retrieval_response,
                        ),
                        "generation_mode": "template",
                        "model": None,
                    }
                )
            response = self._with_display_source_excerpts(
                response,
                analyzed_request.question,
            )
            self._log_response(
                resolved_request_id,
                analyzed_request,
                response,
                started_at=started_at,
                qwen_used=False,
                fallback_reason=(
                    "no_evidence" if not retrieval_response.sources else "llm_disabled"
                ),
            )
            return response

        # visual_summary is produced locally but is still supplied by the client.
        # Never forward OCR, labels, or image-derived text to an external provider.
        if analyzed_request.context.visual_summary and not settings.llm_is_local:
            response = retrieval_response.model_copy(
                update={
                    "answer": self._short_grounded_answer(
                        analyzed_request,
                        retrieval_response,
                    ),
                    "warning": self._append_warning(
                        retrieval_response.warning, LOCAL_VISION_LLM_WARNING
                    )
                }
            )
            response = self._with_display_source_excerpts(
                response,
                analyzed_request.question,
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
                    "answer": self._short_grounded_answer(
                        analyzed_request,
                        retrieval_response,
                    ),
                    "warning": self._append_warning(
                        retrieval_response.warning, COMPANY_LLM_WARNING
                    )
                }
            )
            response = self._with_display_source_excerpts(
                response,
                analyzed_request.question,
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
                    "answer": self._short_grounded_answer(
                        analyzed_request,
                        retrieval_response,
                    ),
                    "warning": self._append_warning(
                        retrieval_response.warning, LLM_FALLBACK_WARNING
                    )
                }
            )
            response = self._with_display_source_excerpts(
                response,
                analyzed_request.question,
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
        response = self._with_display_source_excerpts(
            response,
            analyzed_request.question,
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

    async def _full_document_sources(
        self,
        sources: list[ChatSource],
        answer_type: AnswerType | None,
        db: Session | None,
    ) -> list[ChatSource] | None:
        """Every chunk of the manual(s) this maintenance answer's evidence came from.

        The "정격/성능" PDF-page card must find pages that genuinely cover
        ratings/performance, not just whatever happened to be in this
        question's retrieval result (`sources` is only the RAG top-k, a
        handful of chunks out of a document that can run to hundreds of
        pages). When a DB session is available, this fetches every chunk of
        the referenced manual document(s) directly so structured_answers can
        search the whole document instead. Returns None when there is
        nothing to scan or no DB session was provided — callers then fall
        back to searching only `sources`, exactly as before.
        """

        if db is None or answer_type != "maintenance_guide":
            return None
        candidates = [
            source
            for source in sources
            if canonical_document_type(source.source_type) in MANUAL_DOCUMENT_TYPES
        ]
        version_ids: set[UUID] = set()
        for source in candidates:
            if not source.document_version_id:
                continue
            try:
                version_ids.add(UUID(source.document_version_id))
            except ValueError:
                continue
        if not version_ids:
            return None
        chunks = await asyncio.to_thread(
            DocumentChunkRepository(db).list_for_document_versions, version_ids
        )
        if not chunks:
            return None
        outline_by_version: dict[UUID, list[OutlineChapter]] = {}
        for chunk in chunks:
            version_id = chunk.document_version_id
            version = chunk.document_version
            if version_id is None or version_id in outline_by_version or version is None:
                continue
            outline_by_version[version_id] = await asyncio.to_thread(
                document_outline_chapters, version.storage_path
            )
        return [
            _chat_source_from_chunk(
                chunk,
                outline_chapter_title=chapter_title_for_page(
                    outline_by_version.get(chunk.document_version_id, []),
                    chunk.page_number,
                ),
            )
            for chunk in chunks
        ]

    async def _answer_with_qwen(
        self,
        request: ChatRequest,
        retrieval_response: ChatResponse,
        *,
        request_id: str | None = None,
        db: Session | None = None,
        full_document_sources: list[ChatSource] | None = None,
        qwen_excluded_source_ids: frozenset[str] = frozenset(),
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
        qwen_response = self._qwen_context_response(
            request, retrieval_response, excluded_source_ids=qwen_excluded_source_ids
        )
        qwen_started_at = perf_counter()
        qwen_answer = await self.qwen_client.answer(request, qwen_response)
        if qwen_answer is None:
            self._log_qwen_stage(
                request_id,
                qwen_response,
                qwen_started_at,
                fallback_reason="unavailable",
            )
            return self._with_qwen_fallback(
                request,
                retrieval_response,
                QWEN_UNAVAILABLE_WARNING,
            )
        if isinstance(qwen_answer, QwenAnswerFailure):
            self._log_qwen_stage(
                request_id,
                qwen_response,
                qwen_started_at,
                fallback_reason=qwen_answer.reason,
            )
            return self._with_qwen_fallback(
                request,
                retrieval_response,
                self._qwen_failure_warning(qwen_answer),
            )
        if self._is_generic_qwen_answer(qwen_answer.answer):
            self._log_qwen_stage(
                request_id,
                qwen_response,
                qwen_started_at,
                fallback_reason="generic_fallback_answer",
                fallback_detail=qwen_answer.fallback_reason,
            )
            return self._with_qwen_fallback(
                request,
                retrieval_response,
                "Qwen이 일반 fallback 문장을 반환해 검색 근거 기반 짧은 안내로 대체했습니다.",
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
            return self._with_qwen_fallback(
                request,
                retrieval_response,
                "Qwen이 검색되지 않은 출처를 참조해 구조화 결과를 사용하지 않았습니다.",
            )
        if not qwen_answer.used_source_ids:
            self._log_qwen_stage(
                request_id,
                qwen_response,
                qwen_started_at,
                fallback_reason="missing_source_citation",
            )
            return self._with_qwen_fallback(
                request,
                retrieval_response,
                "Qwen 답변에 검증 가능한 출처 인용이 없어 자연어 답변을 사용하지 않았습니다.",
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
        structured_answer = finalize_document_answer(
            structured_answer,
            sources=retrieval_response.sources,
            question=request.question,
        )
        structured_answer = finalize_component_answer(
            structured_answer,
            sources=retrieval_response.sources,
            question=request.question,
        )
        if full_document_sources is None:
            # answer() already computes this for the template path when sources
            # are non-empty and passes it through — recomputing here would be an
            # identical, redundant DB scan (same document_version_ids either way).
            # Only fall back to computing it when called without that context.
            full_document_sources = await self._full_document_sources(
                retrieval_response.sources,
                retrieval_response.answer_type,
                db,
            )
        structured_answer = finalize_maintenance_answer(
            structured_answer,
            sources=retrieval_response.sources,
            question=request.question,
            analysis=request.analysis,
            full_document_sources=full_document_sources,
        )
        checklist_items = (
            checklist_items_from_pre_checks(structured_answer)
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
        if retrieval_response.answer_type in {
            "maintenance_guide",
            "component_info",
            "document_qa",
        }:
            answer = self._short_grounded_answer(
                request,
                retrieval_response.model_copy(
                    update={
                        "structured_answer": structured_answer,
                        "checklist_items": checklist_items,
                    }
                ),
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
                "sources": _with_page_reference_sources(
                    retrieval_response.sources,
                    structured_answer,
                    full_document_sources,
                ),
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
        fallback_detail: str | None = None,
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
                    # The specific reason qwen_service fell back to a canned answer
                    # (e.g. "maintenance compact answer has no card items.") — that
                    # service usually runs on a remote Colab host, so without this
                    # its own diagnostic print is invisible here.
                    "fallback_detail": fallback_detail,
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
    def _is_generic_qwen_answer(answer: str) -> bool:
        return " ".join(str(answer or "").split()) in GENERIC_QWEN_FALLBACK_ANSWERS

    def _with_qwen_fallback(
        self,
        request: ChatRequest,
        response: ChatResponse,
        warning: str,
    ) -> ChatResponse:
        return response.model_copy(
            update={
                "answer": self._short_grounded_answer(request, response),
                "generation_mode": "template",
                "model": None,
                "warning": self._append_warning(response.warning, warning),
            }
        )

    @staticmethod
    def _short_grounded_answer(
        request: ChatRequest,
        response: ChatResponse,
    ) -> str:
        answer_type = response.answer_type
        if answer_type == "document_qa":
            document_answer = ChatService._structured_document_answer(
                response.structured_answer
            )
            if document_answer:
                return document_answer
            return "선택한 문서에서 질문과 관련된 내용을 근거 기준으로 요약했습니다."
        if answer_type == "component_info":
            component_answer = ChatService._structured_component_answer(
                response.structured_answer
            )
            if component_answer:
                return component_answer
            return "검색된 근거를 기준으로 부품의 역할과 주의사항을 요약했습니다."
        if answer_type == "maintenance_guide":
            task_label = ChatService._question_task_label(request.question)
            manual_step_labels = ChatService._structured_manual_step_labels(
                response.structured_answer
            )
            pre_check_labels = ChatService._structured_pre_check_labels(
                response.structured_answer
            )
            stop_condition_labels = ChatService._structured_stop_condition_labels(
                response.structured_answer
            )
            core_warning = ChatService._structured_core_warning(
                response.structured_answer
            )
            target = task_label or "작업"
            if manual_step_labels:
                numbered_steps = " ".join(
                    f"{index}. {step}"
                    for index, step in enumerate(manual_step_labels[:3], start=1)
                )
                answer = f"매뉴얼에서 확인된 {target} 절차입니다. {numbered_steps}"
                return answer
            if pre_check_labels:
                joined = ", ".join(pre_check_labels[:3])
                answer = (
                    f"{target} 전에는 {joined} 항목을 먼저 확인해야 합니다. "
                    "현장 안전관리자 확인 후 진행하세요."
                )
                if core_warning:
                    answer = f"{answer} {core_warning}"
                if stop_condition_labels:
                    answer = (
                        f"{answer} 특히 "
                        f"{ChatService._natural_stop_condition_sentence(stop_condition_labels[0])}"
                    )
                return answer
            if core_warning:
                return f"{target} 전에는 검색된 근거 문서를 확인해야 합니다. {core_warning}"
            if task_label:
                return f"{task_label} 전에는 근거 문서의 확인사항을 먼저 점검해야 합니다."
            return "작업 전에는 검색된 근거의 확인사항을 먼저 점검해야 합니다."
        return response.answer

    @staticmethod
    def _structured_document_answer(value: StructuredAnswer | None) -> str:
        if getattr(value, "answer_type", None) != "document_qa":
            return ""
        topics: list[str] = []
        for item in getattr(value, "main_contents", []) or []:
            content = " ".join(str(getattr(item, "content", "")).split())
            if not content:
                continue
            label = ChatService._document_summary_label(content)
            if label and label not in topics:
                topics.append(label)
            if len(topics) >= 4:
                break
        filename = getattr(getattr(value, "overview", None), "filename", "") or ""
        filename = " ".join(str(filename).split())
        title = re.sub(r"\.pdf$", "", filename, flags=re.IGNORECASE)
        title = title or "선택한 문서"
        if topics:
            return f"{title}는 {', '.join(topics)}을 중심으로 확인할 수 있는 문서입니다."
        if filename:
            return f"{filename}에서 질문과 관련된 내용을 근거 기준으로 요약했습니다."
        return ""

    @staticmethod
    def _document_summary_label(content: str) -> str:
        text = content.casefold()
        if any(term in text for term in ("모델 구성", "주요 사양", "정격", "치수", "사양")):
            return "모델 구성과 주요 사양"
        if any(term in text for term in ("설치", "장착", "배선", "결선", "고정")):
            return "설치·장착·배선 조건"
        if any(term in text for term in ("오동작", "손상", "안전 주의사항", "주의사항", "위험")):
            return "오동작·손상 예방 주의사항"
        if any(term in text for term in ("기능", "설정", "동작", "모니터링")):
            return "기능 설정과 동작 확인 방법"
        if any(term in text for term in ("구성 요소", "관련 부품", "부품 정보")):
            return "구성 요소와 관련 부품"
        return ""

    @staticmethod
    def _structured_component_answer(value: StructuredAnswer | None) -> str:
        if getattr(value, "answer_type", None) != "component_info":
            return ""
        description = " ".join(
            str(getattr(value, "one_line_description", "")).split()
        )
        if description:
            return description
        labels: list[str] = []
        for item in getattr(value, "main_roles", []) or []:
            content = " ".join(str(getattr(item, "content", "")).split())
            if content and content not in labels:
                labels.append(content)
            if len(labels) >= 2:
                break
        if labels:
            return f"검색 근거상 {', '.join(labels)} 항목과 관련된 부품입니다."
        return ""

    @staticmethod
    def _natural_stop_condition_sentence(content: str) -> str:
        text = " ".join(str(content or "").split()).rstrip(".")
        if not text:
            return ""
        if text.endswith(("해야 합니다", "하세요", "됩니다", "입니다")):
            return f"{text}."
        if text.endswith("작업 중지"):
            text = text[: -len("작업 중지")].rstrip()
            return f"{text} 작업을 중지해야 합니다."
        if text.endswith("중지"):
            text = text[: -len("중지")].rstrip()
            if text.endswith("작업"):
                return f"{text}을 중지해야 합니다."
            return f"{text} 중지해야 합니다."
        if "중지" in text:
            return f"{text}해야 합니다."
        return f"{text}해야 합니다."

    @staticmethod
    def _structured_pre_check_labels(value: StructuredAnswer | None) -> list[str]:
        if getattr(value, "answer_type", None) != "maintenance_guide":
            return []
        labels: list[str] = []
        for item in getattr(value, "pre_checks", []) or []:
            content = " ".join(str(getattr(item, "content", "")).split())
            if content and content not in labels:
                labels.append(content)
            if len(labels) >= 3:
                break
        return labels

    @staticmethod
    def _structured_manual_step_labels(value: StructuredAnswer | None) -> list[str]:
        if getattr(value, "answer_type", None) != "maintenance_guide":
            return []
        labels: list[str] = []
        for item in getattr(value, "manual_steps", []) or []:
            content = " ".join(str(getattr(item, "content", "")).split())
            if content and content not in labels:
                labels.append(content)
            if len(labels) >= 3:
                break
        return labels

    @staticmethod
    def _structured_stop_condition_labels(value: StructuredAnswer | None) -> list[str]:
        if getattr(value, "answer_type", None) != "maintenance_guide":
            return []
        labels: list[str] = []
        for item in getattr(value, "stop_conditions", []) or []:
            content = " ".join(str(getattr(item, "content", "")).split()).rstrip(".")
            if content and content not in labels:
                labels.append(content)
            if len(labels) >= 2:
                break
        return labels

    @staticmethod
    def _structured_core_warning(value: StructuredAnswer | None) -> str:
        if getattr(value, "answer_type", None) != "maintenance_guide":
            return ""
        return " ".join(str(getattr(getattr(value, "summary", None), "core_warning", "")).split())

    @staticmethod
    def _question_task_label(question: str) -> str:
        subject = ChatService._question_subject(question)
        action = next(
            (
                value
                for value in (
                    "설치",
                    "교체",
                    "사용",
                    "재기동",
                    "변경",
                    "점검",
                    "청소",
                    "정비",
                    "수리",
                    "조정",
                    "분리",
                    "연결",
                )
                if value in question
            ),
            "",
        )
        if subject and action:
            return f"{subject} {action}"
        return subject or action

    _TRAILING_PARTICLE_PATTERN = re.compile(
        r"(?:으로부터|에서부터|이라도|라도|에서|으로|부터|까지|마저|조차|밖에|"
        r"이나|은|는|을|를|이|가|과|와|도|만|의|에|나|로)$"
    )

    @staticmethod
    def _strip_trailing_particle(token: str) -> str:
        stripped = ChatService._TRAILING_PARTICLE_PATTERN.sub("", token)
        return stripped if len(stripped) >= 2 else token

    @staticmethod
    def _question_subject(question: str) -> str:
        tokens = [
            token
            for token in (
                ChatService._strip_trailing_particle(raw_token)
                for raw_token in re.findall(r"[0-9A-Za-z가-힣_-]+", question)
            )
            if len(token) >= 2
            and token.casefold()
            not in QWEN_RELEVANCE_STOPWORDS
            and token.casefold()
            not in {
                "거야",
                "할거야",
                "예정",
                "예정이야",
                "하려고",
                "할게",
                "기능",
                "상태",
                "상태에서",
                "확인",
                "확인해야",
                "주의사항",
                "알려줘",
                "어떻게",
                "해야",
                "해야해",
                "해야할까",
                "할까",
                "뭐",
                "뭘",
            }
            and not any(
                action in token
                for action in (
                    "설치",
                    "교체",
                    "사용",
                    "운전",
                    "작동",
                    "재기동",
                    "리셋",
                    "해제",
                    "변경",
                    "확인",
                    "점검",
                    "청소",
                    "정비",
                    "수리",
                    "조정",
                    "작업",
                    "방법",
                    "절차",
                )
            )
        ]
        if tokens and len(tokens[-1]) > 2 and tokens[-1].endswith(("을", "를")):
            tokens[-1] = tokens[-1][:-1]
        return " ".join(tokens[:6]).strip()

    @staticmethod
    def _scope_pdf_sources_to_primary_document(
        response: ChatResponse,
        *,
        answer_type: AnswerType,
    ) -> ChatResponse:
        if not response.sources:
            return response
        if answer_type == "document_qa":
            primary_document_id = max(
                response.sources,
                key=lambda source: (
                    source.reranker_score,
                    source.retrieval_score,
                    source.similarity,
                ),
            ).document_id
            scoped_sources = [
                source
                for source in response.sources
                if source.document_id == primary_document_id
            ]
            return response.model_copy(update={"sources": scoped_sources})

        primary_manual_document_id = next(
            (
                source.document_id
                for source in response.sources
                if ChatService._is_pdf_rag_source(source)
            ),
            None,
        )
        if primary_manual_document_id is None:
            return response
        scoped_sources = [
            source
            for source in response.sources
            if (
                not ChatService._is_pdf_rag_source(source)
                or source.document_id == primary_manual_document_id
            )
        ]
        return response.model_copy(update={"sources": scoped_sources})

    @staticmethod
    def _should_reretrieve_with_primary_pdf(
        request: ChatRequest,
        response: ChatResponse,
    ) -> bool:
        if len(request.context.selected_document_ids) <= 1:
            return False
        selected_ids = {
            str(document_id) for document_id in request.context.selected_document_ids
        }
        manual_document_ids = {
            source.document_id
            for source in response.sources
            if ChatService._is_pdf_rag_source(source)
            and source.document_id in selected_ids
        }
        return len(manual_document_ids) > 1

    @staticmethod
    def _request_scoped_to_primary_pdf(
        request: ChatRequest,
        response: ChatResponse,
    ) -> ChatRequest:
        selected_ids = {
            str(document_id) for document_id in request.context.selected_document_ids
        }
        primary_source = next(
            (
                source
                for source in response.sources
                if ChatService._is_pdf_rag_source(source)
                and source.document_id in selected_ids
            ),
            None,
        )
        if primary_source is None:
            return request
        try:
            primary_document_id = UUID(primary_source.document_id)
        except ValueError:
            return request
        version_ids: list[UUID] = []
        if primary_source.document_version_id:
            try:
                version_ids = [UUID(primary_source.document_version_id)]
            except ValueError:
                version_ids = []
        return request.model_copy(
            update={
                "context": request.context.model_copy(
                    update={
                        "selected_document_ids": [primary_document_id],
                        "selected_document_version_ids": version_ids,
                    }
                )
            }
        )

    @staticmethod
    def _is_pdf_rag_source(source: ChatSource) -> bool:
        return canonical_document_type(source.source_type) in MANUAL_DOCUMENT_TYPES

    @staticmethod
    def _qwen_context_response(
        request: ChatRequest,
        response: ChatResponse,
        *,
        excluded_source_ids: frozenset[str] = frozenset(),
    ) -> ChatResponse:
        answer_type = response.answer_type or "no_evidence"
        limit = QWEN_SOURCE_LIMIT_BY_TYPE.get(answer_type, 1)
        if limit <= 0:
            return response.model_copy(update={"sources": []})
        # rating_performance_page_source_ids pages are found by scanning the whole
        # manual for the 정격/성능 card (see _full_document_sources / _card_meaning_pages)
        # independently of what the user actually asked. When one of those pages
        # isn't part of the real retrieval result, it's only relevant to the card
        # UI, not to Qwen's answer — including it here fed unrelated spec-table
        # pages into the generation prompt and made Qwen's JSON output unreliable.
        candidate_sources = [
            source
            for source in response.sources
            if source.chunk_id not in excluded_source_ids
        ]
        max_sentences = 2 if answer_type == "maintenance_guide" else 3
        if answer_type == "maintenance_guide":
            candidate_source_ids = ChatService._structured_evidence_ids(
                response.structured_answer,
                response.checklist_items,
            )
            selected_sources = ChatService._select_maintenance_qwen_sources(
                request.question,
                candidate_sources,
                limit=limit,
                priority_source_ids=candidate_source_ids,
            )
        else:
            selected_sources = candidate_sources[:limit]
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
        priority_source_ids: set[str] | None = None,
    ) -> list[ChatSource]:
        if limit <= 0:
            return []
        question_terms = ChatService._relevance_terms(question)
        target_phrase_indexes = ChatService._target_phrase_group_indexes(question)
        if target_phrase_indexes:
            phrase_matched_sources = [
                source
                for source in sources
                if ChatService._target_phrase_matches(
                    target_phrase_indexes,
                    ChatService._source_search_text(source),
                )
                and not ChatService._target_phrase_negative_matches(
                    target_phrase_indexes,
                    ChatService._source_search_text(source),
                )
            ]
            if phrase_matched_sources:
                sources = phrase_matched_sources
        if len(sources) <= limit:
            return sources
        priority_source_ids = priority_source_ids or set()
        selected: list[ChatSource] = []
        selected_indexes: set[int] = set()
        used_doc_pages: set[tuple[str, int | None, int | None]] = set()
        used_groups: set[str] = set()

        for _ in range(min(limit, len(sources))):
            best: tuple[float, int, ChatSource] | None = None
            for index, source in enumerate(sources):
                if index in selected_indexes:
                    continue
                score = ChatService._maintenance_source_score(
                    source,
                    question_terms,
                    target_phrase_indexes,
                )
                if ChatService._source_doc_page_key(source) in used_doc_pages:
                    score -= 0.35
                group = ChatService._source_group(source)
                if group in used_groups:
                    score -= 0.08
                if selected and group not in used_groups:
                    score += 0.12
                if source.chunk_id in priority_source_ids:
                    score += 0.28
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
    def _structured_evidence_ids(
        structured_answer: StructuredAnswer | None,
        checklist_items: list,
    ) -> set[str]:
        found: set[str] = set()

        def collect(value: object) -> None:
            if isinstance(value, dict):
                for key, nested in value.items():
                    if key == "evidence_chunk_ids" and isinstance(nested, list):
                        found.update(str(item) for item in nested if item)
                    else:
                        collect(nested)
            elif isinstance(value, list):
                for nested in value:
                    collect(nested)

        if structured_answer is not None:
            collect(structured_answer.model_dump(mode="python"))
        for item in checklist_items:
            if hasattr(item, "model_dump"):
                collect(item.model_dump(mode="python"))
            else:
                collect(item)
        return found

    @staticmethod
    def _maintenance_source_score(
        source: ChatSource,
        question_terms: set[str],
        target_phrase_indexes: tuple[int, ...] = (),
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
        phrase_bonus = 0.0
        phrase_match = ChatService._target_phrase_matches(
            target_phrase_indexes,
            text,
        )
        if (
            target_phrase_indexes
            and ChatService._target_phrase_negative_matches(target_phrase_indexes, text)
            and not phrase_match
        ):
            return -1000.0
        if target_phrase_indexes:
            if phrase_match:
                phrase_bonus = 0.55
            elif source_type in QWEN_PUBLIC_INCIDENT_TYPES:
                phrase_bonus = -0.35
            elif source_type in QWEN_PUBLIC_REFERENCE_TYPES:
                phrase_bonus = -0.18
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
            + phrase_bonus
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
    def _compact_target_phrase(value: str) -> str:
        return re.sub(r"[\s_-]+", "", value.casefold())

    @staticmethod
    def _target_phrase_group_indexes(value: str) -> tuple[int, ...]:
        compact_text = ChatService._compact_target_phrase(value)
        matched: list[int] = []
        for index, (aliases, _) in enumerate(QWEN_DOMAIN_PHRASE_GROUPS):
            if any(
                ChatService._compact_target_phrase(alias) in compact_text
                for alias in aliases
            ):
                matched.append(index)
        return tuple(matched)

    @staticmethod
    def _target_phrase_matches(indexes: tuple[int, ...], text: str) -> bool:
        compact_text = ChatService._compact_target_phrase(text)
        return any(
            any(
                ChatService._compact_target_phrase(alias) in compact_text
                for alias in QWEN_DOMAIN_PHRASE_GROUPS[index][0]
            )
            for index in indexes
        )

    @staticmethod
    def _target_phrase_negative_matches(indexes: tuple[int, ...], text: str) -> bool:
        compact_text = ChatService._compact_target_phrase(text)
        return any(
            any(
                ChatService._compact_target_phrase(term) in compact_text
                for term in QWEN_DOMAIN_PHRASE_GROUPS[index][1]
            )
            for index in indexes
        )

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
    def _with_display_source_excerpts(
        response: ChatResponse,
        question: str,
    ) -> ChatResponse:
        if not response.sources:
            return response
        return response.model_copy(
            update={
                "sources": [
                    ChatService._compact_display_source(source, question)
                    for source in response.sources
                ]
            }
        )

    @staticmethod
    def _compact_display_source(source: ChatSource, question: str) -> ChatSource:
        source_type = canonical_document_type(source.source_type)
        max_sentences = 1 if source_type in QWEN_PUBLIC_REFERENCE_TYPES else 2
        excerpt = ChatService._focused_excerpt(
            source.excerpt,
            question,
            max_sentences=max_sentences,
        )
        excerpt = ChatService._trim_display_excerpt(excerpt, question, max_chars=180)
        return source.model_copy(update={"excerpt": excerpt})

    @staticmethod
    def _trim_display_excerpt(excerpt: str, question: str, *, max_chars: int) -> str:
        if len(excerpt) <= max_chars:
            return excerpt
        lowered = excerpt.casefold()
        terms = ChatService._question_focus_terms(question)
        positions = [
            lowered.find(term)
            for term in sorted(terms, key=len, reverse=True)
            if term and lowered.find(term) >= 0
        ]
        start = max(0, min(positions) - 12) if positions else 0
        snippet = excerpt[start : start + max_chars].strip()
        if start > 0:
            snippet = "..." + snippet.lstrip(" ,.;:·-")
        if start + max_chars < len(excerpt):
            snippet = snippet.rstrip(" ,.;:·-") + "..."
        return snippet

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
        question_terms = ChatService._question_focus_terms(question)

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
    def _question_focus_terms(question: str) -> set[str]:
        terms = {
            token.casefold().strip("_-")
            for token in re.findall(r"[0-9A-Za-z가-힣_-]+", question)
            if len(token) >= 2
            and token.casefold().strip("_-") not in QWEN_RELEVANCE_STOPWORDS
        }
        lowered = question.casefold()
        if any(term in lowered for term in ("설치", "장착", "고정", "체결", "install")):
            terms.update(
                {
                    "설치",
                    "장착",
                    "고정",
                    "체결",
                    "위치",
                    "거리",
                    "간격",
                    "정격",
                    "전원",
                    "배선",
                    "주의",
                    "기준",
                }
            )
        if any(term in lowered for term in ("점검", "검사", "정비", "보수", "교체", "청소", "세척")):
            terms.update(
                {
                    "점검",
                    "검사",
                    "정비",
                    "정지",
                    "차단",
                    "잠금",
                    "재가동",
                    "방호",
                    "위험",
                    "사고",
                }
            )
        if any(term in lowered for term in ("뭐야", "무엇", "정의", "설명", "알려", "용도", "사용")):
            terms.update({"정의", "역할", "기능", "용도", "구성", "사양", "사용"})
        return terms

    @staticmethod
    def _clean_qwen_excerpt(excerpt: str) -> str:
        text = " ".join(excerpt.split())
        text = repair_extracted_quantity_order(text)
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
        if len(re.findall(r"\b[0-9A-Za-z가-힣]{1,10}:\s*", text)) >= 3:
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
    def _needs_qwen_intent_resolution(
        request: ChatRequest,
        *,
        explicit_question_intent: bool,
    ) -> bool:
        if explicit_question_intent:
            return False
        analysis = request.analysis
        if analysis is None or analysis.question_intent is None:
            return True
        if analysis.question_intent == "clarification_required":
            return True
        confidence = analysis.intent_confidence
        return (
            confidence is not None
            and confidence < settings.question_intent_confidence_threshold
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
            )
            if (value := getattr(qwen_analysis, field))
        }
        if qwen_analysis.search_keywords:
            updates["search_keywords"] = list(
                dict.fromkeys(
                    [
                        *qwen_analysis.search_keywords,
                        *base.search_keywords,
                    ]
                )
            )[:30]
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
            elif confidence is not None or (
                base.question_intent in {None, "clarification_required"}
            ):
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
