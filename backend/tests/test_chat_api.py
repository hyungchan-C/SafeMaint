import asyncio

import httpx
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.chat import ChatService, get_chat_service


def test_chat_service_returns_task_specific_fallback() -> None:
    service = ChatService(service_url=None, openai_enabled=False)
    response = asyncio.run(
        service.answer(
            ChatRequest.model_validate(
                {
                    "question": "컨베이어밸트 베어링을 교체하려고 합니다",
                    "context": {
                        "equipment_name": "컨베이어 CV-203",
                        "component_name": "베어링",
                    },
                }
            )
        )
    )

    assert response.retrieval_mode == "safety-fallback"
    assert response.sources == []
    assert "베어링 교체" in response.answer
    assert "LOTO" in response.answer
    assert response.warning


def test_chat_service_accepts_grounded_rag_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat"
        return httpx.Response(
            200,
            json={
                "answer": "검색 근거가 연결된 답변",
                "sources": [
                    {
                        "document_id": "doc-1",
                        "chunk_id": "chunk-1",
                        "title": "컨베이어 정비 중 끼임 사고",
                        "source_type": "incident:domestic",
                        "excerpt": "정비 작업 중 설비가 기동되어 사고가 발생함",
                        "page": None,
                        "url": None,
                        "similarity": 0.71,
                    }
                ],
                "retrieval_mode": "bge-m3",
                "warning": None,
            },
        )

    service = ChatService(
        service_url="http://rag.test",
        transport=httpx.MockTransport(handler),
        openai_enabled=False,
    )
    response = asyncio.run(service.answer(ChatRequest(question="컨베이어 청소법")))

    assert response.retrieval_mode == "bge-m3"
    assert response.sources[0].similarity == 0.71


def test_chat_service_sends_retrieved_sources_to_openai() -> None:
    class FakeAIService:
        def answer(self, question: str, context: str | None = None) -> str:
            assert question == "컨베이어 청소법"
            assert context is not None
            assert "컨베이어 정비 중 끼임 사고" in context
            assert "설비가 기동되어 사고" in context
            return "검색 근거를 바탕으로 전원을 차단하고 LOTO를 적용하세요."

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "answer": "검색 서비스 기본 답변",
                "sources": [
                    {
                        "document_id": "doc-1",
                        "chunk_id": "chunk-1",
                        "title": "컨베이어 정비 중 끼임 사고",
                        "source_type": "incident:domestic",
                        "excerpt": "정비 중 설비가 기동되어 사고가 발생함",
                        "page": None,
                        "url": None,
                        "similarity": 0.71,
                    }
                ],
                "retrieval_mode": "bge-m3",
                "warning": None,
            },
        )

    service = ChatService(
        service_url="http://rag.test",
        transport=httpx.MockTransport(handler),
        ai_service=FakeAIService(),  # type: ignore[arg-type]
        openai_enabled=True,
    )
    response = asyncio.run(service.answer(ChatRequest(question="컨베이어 청소법")))

    assert response.generation_mode == "openai"
    assert response.model == "gpt-4o-mini"
    assert response.sources[0].title == "컨베이어 정비 중 끼임 사고"
    assert response.answer.startswith("검색 근거를 바탕으로")


def test_chat_service_keeps_grounded_fallback_when_openai_fails() -> None:
    class FailingAIService:
        def answer(self, question: str, context: str | None = None) -> str:
            raise RuntimeError("temporary failure")

    service = ChatService(
        service_url=None,
        ai_service=FailingAIService(),  # type: ignore[arg-type]
        openai_enabled=True,
    )
    response = asyncio.run(service.answer(ChatRequest(question="베어링 교체 방법")))

    assert response.generation_mode == "template"
    assert response.retrieval_mode == "safety-fallback"
    assert "OpenAI 답변 생성에 실패" in (response.warning or "")


def test_chat_api_uses_injected_service() -> None:
    class FakeChatService:
        async def answer(self, request: ChatRequest) -> ChatResponse:
            assert request.question == "컨베이어 청소법"
            return ChatResponse(
                answer="전원을 차단하고 LOTO를 적용하세요.",
                sources=[],
                retrieval_mode="safety-fallback",
                warning="테스트 경고",
            )

    async def request_chat() -> httpx.Response:
        app.dependency_overrides[get_chat_service] = lambda: FakeChatService()
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                return await client.post(
                    "/api/v1/chat",
                    json={"question": "컨베이어 청소법"},
                )
        finally:
            app.dependency_overrides.clear()

    response = asyncio.run(request_chat())

    assert response.status_code == 200
    assert response.json()["answer"].startswith("전원을 차단")


def test_chat_api_rejects_too_short_question() -> None:
    async def request_chat() -> httpx.Response:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post("/api/v1/chat", json={"question": "   "})

    response = asyncio.run(request_chat())

    assert response.status_code == 422
