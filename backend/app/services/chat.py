from __future__ import annotations

import asyncio

import httpx
from openai import OpenAIError

from app.core.config import settings
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.ai import AIConfigurationError, AIService
from app.services.safety_guidance import format_safety_answer


RAG_UNAVAILABLE_WARNING = (
    "BGE-M3 검색 서비스에 연결하지 못해 공통 안전수칙만 표시했습니다. "
    "근거 문서가 없으므로 작업 승인 판단에 사용할 수 없습니다."
)
OPENAI_FALLBACK_WARNING = (
    "OpenAI 답변 생성에 실패해 검색 서비스의 기본 안전 안내를 표시했습니다."
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
            bool(settings.openai_api_key)
            if openai_enabled is None
            else openai_enabled
        )

    async def answer(self, request: ChatRequest) -> ChatResponse:
        retrieval_response = await self._retrieve(request)
        if not self.openai_enabled:
            return retrieval_response

        try:
            answer = await asyncio.to_thread(
                self.ai_service.answer,
                request.question,
                self._build_grounded_context(request, retrieval_response),
            )
        except (AIConfigurationError, OpenAIError, RuntimeError):
            return retrieval_response.model_copy(
                update={
                    "warning": self._append_warning(
                        retrieval_response.warning,
                        OPENAI_FALLBACK_WARNING,
                    )
                }
            )

        return retrieval_response.model_copy(
            update={
                "answer": answer,
                "generation_mode": "openai",
                "model": settings.openai_model,
            }
        )

    async def _retrieve(self, request: ChatRequest) -> ChatResponse:
        if not self.service_url:
            return self._fallback(request)

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                response = await client.post(
                    f"{self.service_url}/v1/chat",
                    json=request.model_dump(mode="json"),
                )
                response.raise_for_status()
                return ChatResponse.model_validate(response.json())
        except (httpx.HTTPError, ValueError):
            return self._fallback(request)

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
        if context.registered_manuals:
            lines.append(
                f"화면에서 선택한 매뉴얼: {len(context.registered_manuals)}개 "
                "(파일 내용은 아직 검색 근거에 포함되지 않음)"
            )

        lines.append("\n검색 근거:")
        if not response.sources:
            lines.append("- 검색된 근거 문서 없음")
        else:
            for index, source in enumerate(response.sources, start=1):
                lines.extend(
                    (
                        f"[{index}] 제목: {source.title}",
                        f"[{index}] 자료 유형: {source.source_type}",
                        f"[{index}] 유사도: {source.similarity:.3f}",
                        f"[{index}] 내용: {source.excerpt}",
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
