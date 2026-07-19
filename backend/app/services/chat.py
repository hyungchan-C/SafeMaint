from __future__ import annotations

import asyncio
import json
import re

import httpx
from openai import OpenAIError

from app.core.config import settings
from app.schemas.chat import (
    ChatRequest,
    ChatResponse,
    QueryAnalysis,
    RetrievalAccessScope,
)
from app.services.ai import AIConfigurationError, AIService
from app.services.safety_guidance import format_safety_answer


RAG_UNAVAILABLE_WARNING = (
    "문서 검색 서비스에 연결하지 못해 공통 안전수칙만 표시합니다. "
    "근거 문서가 없으므로 작업 승인 판단에 사용하지 마세요."
)
ANALYZER_FALLBACK_WARNING = (
    "상황 분석 모델을 사용할 수 없어 입력값 기반 검색어로 안전하게 대체했습니다."
)
LLM_FALLBACK_WARNING = (
    "GPT-4o-mini 답변 생성에 실패해 검색 서비스의 근거 기반 기본 안내를 표시합니다."
)
COMPANY_LLM_WARNING = (
    "회사 문서 내용은 외부 GPT-4o-mini로 전송하지 않았습니다."
)


class ChatService:
    def __init__(
        self,
        service_url: str | None = None,
        timeout_seconds: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        ai_service: AIService | None = None,
        openai_enabled: bool | None = None,
    ) -> None:
        self.service_url = (service_url or "").rstrip("/")
        self.timeout_seconds = timeout_seconds or settings.rag_request_timeout_seconds
        self.transport = transport
        self.ai_service = ai_service or AIService()
        self.openai_enabled = (
            settings.allow_external_llm
            and bool(settings.openai_api_key or settings.llm_base_url)
            if openai_enabled is None
            else openai_enabled
        )

    async def answer(
        self,
        request: ChatRequest,
        access_scope: RetrievalAccessScope | None = None,
    ) -> ChatResponse:
        analyzed_request, analyzer_fell_back = await self._analyze(request)
        retrieval_response = await self._retrieve(analyzed_request, access_scope)
        if analyzer_fell_back and self.openai_enabled:
            retrieval_response = retrieval_response.model_copy(
                update={
                    "warning": self._append_warning(
                        retrieval_response.warning, ANALYZER_FALLBACK_WARNING
                    )
                }
            )

        if not retrieval_response.sources or not self.openai_enabled:
            return retrieval_response

        # This is intentionally unconditional: no company evidence is sent to
        # an external provider, even if a legacy environment flag says otherwise.
        if any(
            source.document_scope == "company"
            for source in retrieval_response.sources
        ):
            return retrieval_response.model_copy(
                update={
                    "warning": self._append_warning(
                        retrieval_response.warning, COMPANY_LLM_WARNING
                    )
                }
            )

        try:
            answer = await asyncio.to_thread(
                self.ai_service.answer,
                request.question,
                self._build_grounded_context(analyzed_request, retrieval_response),
            )
        except (AIConfigurationError, OpenAIError, RuntimeError, ValueError):
            return retrieval_response.model_copy(
                update={
                    "warning": self._append_warning(
                        retrieval_response.warning, LLM_FALLBACK_WARNING
                    )
                }
            )

        return retrieval_response.model_copy(
            update={
                "answer": answer,
                "generation_mode": "openai",
                "model": settings.llm_answer_model,
            }
        )

    async def _analyze(self, request: ChatRequest) -> tuple[ChatRequest, bool]:
        if request.analysis is not None:
            return request, False
        fallback = self._fallback_analysis(request)
        if not self.openai_enabled or not hasattr(self.ai_service, "analyze"):
            return request.model_copy(update={"analysis": fallback}), False
        try:
            analysis = await asyncio.to_thread(
                self.ai_service.analyze,
                request.question,
                self._analysis_context(request),
            )
            if not isinstance(analysis, QueryAnalysis):
                analysis = QueryAnalysis.model_validate(analysis)
            return request.model_copy(update={"analysis": analysis}), False
        except (AIConfigurationError, OpenAIError, RuntimeError, ValueError):
            return request.model_copy(update={"analysis": fallback}), True

    async def _retrieve(
        self,
        request: ChatRequest,
        access_scope: RetrievalAccessScope | None = None,
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
                response = await client.post(
                    f"{self.service_url}/v1/chat", json=request_body
                )
                response.raise_for_status()
                return ChatResponse.model_validate(response.json())
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
                },
            ),
            ensure_ascii=False,
        )

    @staticmethod
    def _fallback_analysis(request: ChatRequest) -> QueryAnalysis:
        context = request.context
        raw_keywords = " ".join(
            value
            for value in (
                context.equipment_name,
                context.manufacturer,
                context.model_number,
                context.component_name,
                context.task_type,
                context.energy_source,
                context.task_description,
                request.question,
            )
            if value
        )
        keywords = list(
            dict.fromkeys(
                token.casefold()
                for token in re.findall(r"[0-9A-Za-z가-힣_-]+", raw_keywords)
                if len(token) >= 2
            )
        )[:30]
        return QueryAnalysis(
            work_type=context.task_type,
            equipment=[context.equipment_name] if context.equipment_name else [],
            component=[context.component_name] if context.component_name else [],
            energy_sources=[context.energy_source] if context.energy_source else [],
            search_keywords=keywords,
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
        )
        lines = [f"{label}: {value}" for label, value in fields if value]
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
    def _fallback(request: ChatRequest) -> ChatResponse:
        context_text = " ".join(
            value
            for value in (
                request.context.equipment_name,
                request.context.component_name,
                request.context.task_type,
                request.context.energy_source,
                request.context.task_description,
                request.question,
            )
            if value
        )
        return ChatResponse(
            answer=format_safety_answer(context_text),
            sources=[],
            retrieval_mode="safety-fallback",
            warning=RAG_UNAVAILABLE_WARNING,
        )


chat_service = ChatService(service_url=settings.rag_service_url)


def get_chat_service() -> ChatService:
    return chat_service
