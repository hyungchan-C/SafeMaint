from __future__ import annotations

import httpx

from app.core.config import settings
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.safety_guidance import format_safety_answer


RAG_UNAVAILABLE_WARNING = (
    "BGE-M3 검색 서비스에 연결하지 못해 공통 안전수칙만 표시했습니다. "
    "근거 문서가 없으므로 작업 승인 판단에 사용할 수 없습니다."
)


class ChatService:
    def __init__(
        self,
        service_url: str | None = None,
        timeout_seconds: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.service_url = (service_url or "").rstrip("/")
        self.timeout_seconds = timeout_seconds or settings.rag_request_timeout_seconds
        self.transport = transport

    async def answer(self, request: ChatRequest) -> ChatResponse:
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
    def _fallback(request: ChatRequest) -> ChatResponse:
        context_text = " ".join(
            value
            for value in (
                request.context.equipment_name,
                request.context.component_name,
                request.context.task_type,
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
