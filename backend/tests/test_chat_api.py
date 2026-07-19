import asyncio
import json
import httpx
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.schemas.chat import (
    ChatRequest,
    ChatResponse,
    QueryAnalysis,
    RetrievalAccessScope,
)
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


def test_chat_service_does_not_call_openai_without_retrieved_evidence() -> None:
    class MustNotBeCalledAIService:
        def answer(self, question: str, context: str | None = None) -> str:
            raise AssertionError("LLM must not run without retrieved evidence")

    service = ChatService(
        service_url=None,
        ai_service=MustNotBeCalledAIService(),  # type: ignore[arg-type]
        openai_enabled=True,
    )
    response = asyncio.run(service.answer(ChatRequest(question="베어링 교체 방법")))

    assert response.generation_mode == "template"
    assert response.retrieval_mode == "safety-fallback"
    assert "근거 문서가 없으므로" in (response.warning or "")


def test_chat_api_uses_injected_service() -> None:
    class FakeChatService:
        async def answer(self, request: ChatRequest, access_scope=None) -> ChatResponse:
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


def test_analyzer_failure_sends_deterministic_fallback_to_retrieval() -> None:
    class FailingAnalyzer:
        def analyze(self, question: str, context: str) -> QueryAnalysis:
            raise RuntimeError("invalid JSON")

        def answer(self, question: str, context: str | None = None) -> str:
            raise AssertionError("No evidence means no answer generation")

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["analysis"]["component"] == ["bearing"]
        assert "bearing" in body["analysis"]["search_keywords"]
        return httpx.Response(
            200,
            json={
                "answer": "No matching evidence was found.",
                "sources": [],
                "retrieval_mode": "hybrid",
                "warning": "no evidence",
            },
        )

    service = ChatService(
        service_url="http://rag.test",
        transport=httpx.MockTransport(handler),
        ai_service=FailingAnalyzer(),  # type: ignore[arg-type]
        openai_enabled=True,
    )
    response = asyncio.run(
        service.answer(
            ChatRequest.model_validate(
                {
                    "question": "conveyor bearing replacement",
                    "context": {
                        "equipment_name": "conveyor",
                        "component_name": "bearing",
                    },
                }
            )
        )
    )

    assert response.retrieval_mode == "hybrid"
    assert "분석 모델" in (response.warning or "")


def test_company_evidence_is_never_sent_to_external_model() -> None:
    class MustNotGenerateFromCompanyEvidence:
        def analyze(self, question: str, context: str) -> QueryAnalysis:
            raise AssertionError("Company queries must not be externally analyzed")

        def answer(self, question: str, context: str | None = None) -> str:
            raise AssertionError("Company evidence must not leave the server")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "answer": "Local grounded template answer",
                "sources": [
                    {
                        "document_id": "doc-company",
                        "chunk_id": "chunk-company",
                        "title": "Company manual",
                        "source_type": "equipment_manual",
                        "document_scope": "company",
                        "excerpt": "Private maintenance instructions",
                        "similarity": 0.8,
                    }
                ],
                "retrieval_mode": "hybrid",
            },
        )

    response = asyncio.run(
        ChatService(
            service_url="http://rag.test",
            transport=httpx.MockTransport(handler),
            ai_service=MustNotGenerateFromCompanyEvidence(),  # type: ignore[arg-type]
            openai_enabled=True,
        ).answer(
            ChatRequest(question="bearing replacement"),
            RetrievalAccessScope(allow_company=True, all_sites=True),
        )
    )

    assert response.generation_mode == "template"
    assert response.answer == "Local grounded template answer"
    assert "회사 문서" in (response.warning or "")
