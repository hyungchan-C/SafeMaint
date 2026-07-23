import asyncio
import json
import httpx
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.schemas.chat import (
    ChatChecklistItem,
    ChatRequest,
    ChatResponse,
    ChatSource,
    ComponentAnswerDetails,
    EvidenceBackedItem,
    MaintenanceAnswerDetails,
    MaintenanceHazard,
    MaintenanceSummary,
    QueryAnalysis,
    RetrievalAccessScope,
)
from app.services.chat import ChatService, get_chat_service
from app.services.qwen import QwenAnswerFailure, QwenGeneratedAnswer
from app.services.accident_classifier import AccidentClassifierClient


def _grounded_source(source_type: str = "component_manual") -> dict[str, object]:
    return {
        "document_id": "doc-1",
        "document_version_id": "version-1",
        "chunk_id": "chunk-1",
        "title": "라이트커튼 매뉴얼",
        "source_type": source_type,
        "document_scope": "company" if "manual" in source_type else "public",
        "original_filename": "light-curtain.pdf",
        "document_version": 1,
        "section": "설치 및 개요",
        "excerpt": "라이트커튼의 용도와 설치 전 확인사항",
        "page_start": 12,
        "similarity": 0.84,
    }


def test_chat_service_returns_topic_neutral_fallback_without_evidence() -> None:
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
    assert "검증 가능한 문서 근거" in response.answer
    assert "베어링 교체" not in response.answer
    assert "LOTO" not in response.answer
    assert "TBM" not in response.answer
    assert response.warning


def test_chat_service_does_not_invent_tbm_checklist_without_manual_pdf() -> None:
    service = ChatService(service_url=None, openai_enabled=False)
    response = asyncio.run(
        service.answer(
            ChatRequest.model_validate(
                {
                    "question": "컨베이어 벨트 부품 교체할 거야. TBM 체크리스트 만들어줘.",
                    "context": {
                        "equipment_name": "컨베이어 CV-203",
                        "component_name": "벨트",
                        "task_type": "부품 교체",
                        "energy_source": "전기",
                    },
                }
            )
        )
    )

    assert response.retrieval_mode == "safety-fallback"
    assert "검증 가능한 문서 근거" in response.answer
    assert "컨베이어 부품 교체 작업" not in response.answer
    assert "TBM 체크리스트" not in response.answer
    assert "[ ]" not in response.answer
    assert "승인된 매뉴얼" in response.answer


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


def test_team_qwen_classification_is_used_for_retrieval() -> None:
    def classifier_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/classify"
        return httpx.Response(
            200,
            json={
                "label": "끼임",
                "model": "Qwen/Qwen3.5-9B",
                "adapter": "best_adapter",
            },
        )

    def rag_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["analysis"]["occurrence_type"] == "끼임"
        assert body["analysis"]["search_keywords"][0] == "끼임"
        return httpx.Response(
            200,
            json={
                "answer": "끼임 관련 검색 결과",
                "sources": [],
                "retrieval_mode": "hybrid",
            },
        )

    service = ChatService(
        service_url="http://rag.test",
        transport=httpx.MockTransport(rag_handler),
        openai_enabled=False,
        classifier_client=AccidentClassifierClient(
            "http://classifier.test",
            transport=httpx.MockTransport(classifier_handler),
        ),
        classifier_enabled=True,
    )
    response = asyncio.run(
        service.answer(ChatRequest(question="컨베이어 롤러를 점검합니다"))
    )

    assert response.accident_classification is not None
    assert response.accident_classification.label == "끼임"
    assert response.accident_classification.adapter == "best_adapter"


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
    assert "문서 검색 서비스에 연결하지 못했습니다" in (response.warning or "")


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
        assert body["analysis"]["component"] == []
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


def test_chat_service_uses_qwen_classification_and_answer() -> None:
    class FakeQwenClient:
        async def classify(self, request: ChatRequest) -> QueryAnalysis:
            assert request.analysis is not None
            assert "bearing" in request.analysis.search_keywords
            return QueryAnalysis(occurrence_type="caught-in")

        async def answer(
            self,
            request: ChatRequest,
            retrieval_response: ChatResponse,
        ) -> QwenGeneratedAnswer:
            assert request.analysis is not None
            assert request.analysis.occurrence_type == "caught-in"
            assert retrieval_response.sources
            return QwenGeneratedAnswer("Qwen grounded answer [1]", "qwen-test")

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["analysis"]["occurrence_type"] == "caught-in"
        return httpx.Response(
            200,
            json={
                "answer": "Local grounded template answer",
                "sources": [
                    {
                        "document_id": "public-doc",
                        "chunk_id": "public-chunk",
                        "title": "Public safety guide",
                        "source_type": "public_guide",
                        "document_scope": "public",
                        "excerpt": "Lock out the equipment before maintenance.",
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
            openai_enabled=False,
            qwen_client=FakeQwenClient(),  # type: ignore[arg-type]
            qwen_enabled=True,
            qwen_allow_company_context=True,
        ).answer(
            ChatRequest.model_validate(
                {
                    "question": "conveyor bearing replacement",
                    "context": {"component_name": "bearing"},
                }
            )
        )
    )

    assert response.generation_mode == "qwen"
    assert response.model == "qwen-test"
    assert response.answer == "Qwen grounded answer [1]"


def test_chat_service_can_skip_qwen_intent_classification() -> None:
    class AnswerOnlyQwenClient:
        async def classify(self, request: ChatRequest) -> QueryAnalysis:
            raise AssertionError("Qwen classify should be skipped")

        async def answer(
            self,
            request: ChatRequest,
            retrieval_response: ChatResponse,
        ) -> QwenGeneratedAnswer:
            assert request.analysis is not None
            assert request.analysis.question_intent == "maintenance_guide"
            return QwenGeneratedAnswer("Fast Qwen answer", "qwen-test")

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["analysis"]["question_intent"] == "maintenance_guide"
        return httpx.Response(
            200,
            json={
                "answer": "Local grounded template answer",
                "sources": [_grounded_source()],
                "retrieval_mode": "hybrid",
            },
        )

    response = asyncio.run(
        ChatService(
            service_url="http://rag.test",
            transport=httpx.MockTransport(handler),
            openai_enabled=False,
            qwen_client=AnswerOnlyQwenClient(),  # type: ignore[arg-type]
            qwen_enabled=True,
            qwen_allow_company_context=True,
            qwen_intent_classify_enabled=False,
        ).answer(ChatRequest(question="light curtain installation"))
    )

    assert response.generation_mode == "qwen"
    assert response.answer == "Fast Qwen answer"


def test_low_confidence_qwen_clarification_does_not_override_local_maintenance_intent() -> None:
    class MisclassifyingQwenClient:
        async def classify(self, request: ChatRequest) -> QueryAnalysis:
            return QueryAnalysis(
                question_intent="clarification_required",
                intent_confidence=0.0,
                clarification_question="어떤 위험 요소나 주의사항이 있나요?",
            )

        async def answer(
            self,
            request: ChatRequest,
            retrieval_response: ChatResponse,
        ) -> QwenGeneratedAnswer:
            assert request.analysis is not None
            assert request.analysis.question_intent == "maintenance_guide"
            return QwenGeneratedAnswer("유지보수 안내", "qwen-test")

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["analysis"]["question_intent"] == "maintenance_guide"
        return httpx.Response(
            200,
            json={
                "answer": "검색 기본 답변",
                "sources": [_grounded_source()],
                "retrieval_mode": "hybrid",
            },
        )

    response = asyncio.run(
        ChatService(
            service_url="http://rag.test",
            transport=httpx.MockTransport(handler),
            openai_enabled=False,
            qwen_client=MisclassifyingQwenClient(),  # type: ignore[arg-type]
            qwen_enabled=True,
            qwen_allow_company_context=True,
        ).answer(ChatRequest(question="라이트 커튼을 설치하려고 해."))
    )

    assert response.answer_type == "maintenance_guide"
    assert response.generation_mode == "qwen"
    assert response.answer == "유지보수 안내"


def test_chat_service_does_not_ask_qwen_to_invent_answer_without_evidence() -> None:
    class ClassificationOnlyQwenClient:
        async def classify(self, request: ChatRequest) -> QueryAnalysis:
            return QueryAnalysis(occurrence_type="caught-in")

        async def answer(
            self,
            request: ChatRequest,
            retrieval_response: ChatResponse,
        ) -> QwenGeneratedAnswer:
            raise AssertionError("Qwen answer generation requires retrieved evidence")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "answer": "질문과 일치하는 검증 가능한 문서 근거를 찾지 못했습니다.",
                "sources": [],
                "retrieval_mode": "hybrid",
                "warning": "no evidence",
            },
        )

    response = asyncio.run(
        ChatService(
            service_url="http://rag.test",
            transport=httpx.MockTransport(handler),
            openai_enabled=False,
            qwen_client=ClassificationOnlyQwenClient(),  # type: ignore[arg-type]
            qwen_enabled=True,
        ).answer(ChatRequest(question="light curtain installation"))
    )

    assert response.generation_mode == "template"
    assert response.sources == []
    assert "검증 가능한 문서 근거" in response.answer


def test_qwen_company_context_requires_explicit_allowance() -> None:
    class MustNotSendCompanyEvidenceToQwen:
        async def classify(self, request: ChatRequest) -> QueryAnalysis:
            return QueryAnalysis(occurrence_type="caught-in")

        async def answer(
            self,
            request: ChatRequest,
            retrieval_response: ChatResponse,
        ) -> QwenGeneratedAnswer:
            raise AssertionError("Company evidence must not be sent to Qwen")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "answer": "Local grounded template answer",
                "sources": [
                    {
                        "document_id": "company-doc",
                        "chunk_id": "company-chunk",
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
            openai_enabled=False,
            qwen_client=MustNotSendCompanyEvidenceToQwen(),  # type: ignore[arg-type]
            qwen_enabled=True,
            qwen_allow_company_context=False,
        ).answer(
            ChatRequest(question="bearing replacement"),
            RetrievalAccessScope(allow_company=True, all_sites=True),
        )
    )

    assert response.generation_mode == "template"
    assert response.answer == "Local grounded template answer"
    assert "Qwen company-context sharing is disabled" in (response.warning or "")


def test_local_vision_summary_is_never_sent_to_external_answer_model() -> None:
    class MustNotGenerateFromLocalVision:
        def analyze(self, question: str, context: str) -> QueryAnalysis:
            assert "현장 OCR 비밀값" not in context
            return QueryAnalysis(search_keywords=["bearing"])

        def answer(self, question: str, context: str | None = None) -> str:
            raise AssertionError("Local vision context must not leave the server")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "answer": "Local grounded template answer",
                "sources": [
                    {
                        "document_id": "public-doc",
                        "chunk_id": "public-chunk",
                        "title": "Public safety guide",
                        "source_type": "regulation",
                        "document_scope": "public",
                        "excerpt": "Lock out the equipment before maintenance.",
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
            ai_service=MustNotGenerateFromLocalVision(),  # type: ignore[arg-type]
            openai_enabled=True,
        ).answer(
            ChatRequest.model_validate(
                {
                    "question": "이 부품은 뭐야?",
                    "context": {"visual_summary": "현장 OCR 비밀값"},
                }
            )
        )
    )

    assert response.generation_mode == "template"
    assert response.answer == "Local grounded template answer"
    assert "로컬 이미지 분석" in (response.warning or "")


def test_document_question_returns_document_structure_without_tbm() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["analysis"]["question_intent"] == "document_qa"
        return httpx.Response(
            200,
            json={
                "answer": "문서 검색 결과",
                "sources": [_grounded_source()],
                "retrieval_mode": "hybrid",
            },
        )

    response = asyncio.run(
        ChatService(
            service_url="http://rag.test",
            transport=httpx.MockTransport(handler),
            openai_enabled=False,
        ).answer(ChatRequest(question="이 PDF를 요약해줘."))
    )

    assert response.answer_type == "document_qa"
    assert response.structured_answer is not None
    assert response.structured_answer.answer_type == "document_qa"
    assert response.checklist_items == []
    assert "hazards" not in response.structured_answer.model_dump()


def test_component_question_returns_component_structure_without_procedure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["analysis"]["question_intent"] == "component_info"
        return httpx.Response(
            200,
            json={
                "answer": "부품 정보 검색 결과",
                "sources": [_grounded_source()],
                "retrieval_mode": "hybrid",
            },
        )

    response = asyncio.run(
        ChatService(
            service_url="http://rag.test",
            transport=httpx.MockTransport(handler),
            openai_enabled=False,
        ).answer(ChatRequest(question="라이트커튼이 무슨 장비인지 알려줘."))
    )

    assert response.answer_type == "component_info"
    assert response.structured_answer is not None
    assert response.structured_answer.answer_type == "component_info"
    assert response.checklist_items == []
    assert "manual_steps" not in response.structured_answer.model_dump()


def test_ambiguous_question_returns_clarification_without_retrieval() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Ambiguous questions must be clarified before retrieval")

    response = asyncio.run(
        ChatService(
            service_url="http://rag.test",
            transport=httpx.MockTransport(handler),
            openai_enabled=False,
        ).answer(ChatRequest(question="라이트커튼 관련해서 알려줘."))
    )

    assert response.answer_type == "clarification_required"
    assert response.clarification_question
    assert response.sources == []
    assert response.checklist_items == []


def test_maintenance_qwen_structure_and_checklist_are_source_validated() -> None:
    class StructuredQwenClient:
        async def classify(self, request: ChatRequest) -> QueryAnalysis:
            return QueryAnalysis(
                question_intent="maintenance_guide",
                intent_confidence=0.98,
            )

        async def answer(
            self,
            request: ChatRequest,
            retrieval_response: ChatResponse,
        ) -> QwenGeneratedAnswer:
            details = MaintenanceAnswerDetails(
                summary=MaintenanceSummary(
                    status="안전관리자 확인 필요",
                    risk_level="높음",
                    risk_basis=[
                        EvidenceBackedItem(
                            content="매뉴얼 설치 전 확인사항",
                            evidence_chunk_ids=["chunk-1"],
                        )
                    ],
                    core_warning="설치 전 제조사 기준을 확인하세요.",
                ),
                pre_checks=[
                    EvidenceBackedItem(
                        content="설치 위치를 확인합니다.",
                        evidence_chunk_ids=["chunk-1"],
                    )
                ],
                hazards=[
                    MaintenanceHazard(
                        name="오검출",
                        content="설치 기준을 벗어나면 검출 성능이 저하될 수 있습니다.",
                        evidence_chunk_ids=["chunk-1"],
                    )
                ],
                manual_steps=[
                    EvidenceBackedItem(
                        content="매뉴얼의 설치 위치 기준을 적용합니다.",
                        evidence_chunk_ids=["chunk-1"],
                    )
                ],
                stop_conditions=[
                    EvidenceBackedItem(
                        content="모델별 설치 기준을 확인할 수 없을 때 중지합니다.",
                        evidence_chunk_ids=["chunk-1"],
                    )
                ],
                evidence_chunk_ids=["chunk-1"],
            )
            return QwenGeneratedAnswer(
                answer="구조화된 유지보수 안내",
                model="qwen-test",
                structured_answer=details,
                checklist_items=(
                    ChatChecklistItem(
                        content="설치 위치 기준 확인",
                        sequence=1,
                        evidence_chunk_ids=["chunk-1"],
                    ),
                ),
                used_source_ids=("chunk-1",),
            )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "answer": "검색 기본 답변",
                "sources": [_grounded_source()],
                "retrieval_mode": "hybrid",
            },
        )

    response = asyncio.run(
        ChatService(
            service_url="http://rag.test",
            transport=httpx.MockTransport(handler),
            openai_enabled=False,
            qwen_client=StructuredQwenClient(),  # type: ignore[arg-type]
            qwen_enabled=True,
            qwen_allow_company_context=True,
        ).answer(ChatRequest(question="라이트커튼 설치시 주의사항을 알려줘."))
    )

    assert response.answer_type == "maintenance_guide"
    assert response.structured_answer is not None
    assert response.structured_answer.answer_type == "maintenance_guide"
    assert len(response.structured_answer.hazards) == 1
    assert len(response.structured_answer.stop_conditions) == 1
    assert [item.content for item in response.checklist_items] == ["설치 위치 기준 확인"]


def test_qwen_empty_component_sections_are_enriched_before_response() -> None:
    class EmptySectionQwenClient:
        async def classify(self, request: ChatRequest) -> QueryAnalysis:
            return QueryAnalysis(
                question_intent="component_info",
                intent_confidence=0.98,
            )

        async def answer(
            self,
            request: ChatRequest,
            retrieval_response: ChatResponse,
        ) -> QwenGeneratedAnswer:
            return QwenGeneratedAnswer(
                answer="라이트 커튼 부품 정보",
                model="qwen-test",
                structured_answer=ComponentAnswerDetails(
                    one_line_description="PC 설정 툴을 통해 설정 및 변경이 가능한 안전 장치입니다.",
                    main_roles=[],
                    usage_locations=[],
                    precautions=[],
                    evidence_chunk_ids=["chunk-1"],
                ),
                used_source_ids=("chunk-1",),
            )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "answer": "검색 기본 답변",
                "sources": [
                    {
                        **_grounded_source(),
                        "excerpt": (
                            "라이트 커튼은 위험구역 접근과 광축 차단을 검출한다. "
                            "기능 설정 후 의도한 대로 동작하는지 확인한다."
                        ),
                    }
                ],
                "retrieval_mode": "hybrid",
            },
        )

    response = asyncio.run(
        ChatService(
            service_url="http://rag.test",
            transport=httpx.MockTransport(handler),
            openai_enabled=False,
            qwen_client=EmptySectionQwenClient(),  # type: ignore[arg-type]
            qwen_enabled=True,
            qwen_allow_company_context=True,
        ).answer(ChatRequest(question="라이트 커튼이 뭐야?"))
    )

    assert response.answer_type == "component_info"
    assert isinstance(response.structured_answer, ComponentAnswerDetails)
    assert "라이트커튼" in response.structured_answer.one_line_description.replace(" ", "")
    assert response.structured_answer.main_roles
    assert response.structured_answer.usage_locations
    assert response.structured_answer.precautions


def test_qwen_receives_compact_sources_to_avoid_colab_ngrok_timeout() -> None:
    long_excerpt = " ".join(
        [
            "프레스 설비 내부 청소 전 전원 차단과 재가동 방지 조치를 확인합니다."
            for _ in range(30)
        ]
    )

    class CompactSourceQwenClient:
        async def classify(self, request: ChatRequest) -> QueryAnalysis:
            return QueryAnalysis(
                question_intent="maintenance_guide",
                intent_confidence=0.98,
            )

        async def answer(
            self,
            request: ChatRequest,
            retrieval_response: ChatResponse,
        ) -> QwenGeneratedAnswer:
            assert len(retrieval_response.sources) == 2
            assert all(len(source.excerpt) <= 363 for source in retrieval_response.sources)
            return QwenGeneratedAnswer("Qwen compact answer", "qwen-test")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "answer": "검색 기본 답변",
                "sources": [
                    {
                        **_grounded_source("public_guide"),
                        "chunk_id": "chunk-1",
                        "excerpt": long_excerpt,
                    },
                    {
                        **_grounded_source("public_guide"),
                        "chunk_id": "chunk-2",
                        "excerpt": long_excerpt,
                    },
                    {
                        **_grounded_source("public_guide"),
                        "chunk_id": "chunk-3",
                        "excerpt": long_excerpt,
                    },
                ],
                "retrieval_mode": "hybrid",
            },
        )

    response = asyncio.run(
        ChatService(
            service_url="http://rag.test",
            transport=httpx.MockTransport(handler),
            openai_enabled=False,
            qwen_client=CompactSourceQwenClient(),  # type: ignore[arg-type]
            qwen_enabled=True,
            qwen_allow_company_context=True,
        ).answer(ChatRequest(question="프레스 설비 내부를 청소할 예정이야."))
    )

    assert response.generation_mode == "qwen"
    assert response.answer == "Qwen compact answer"
    assert len(response.sources) == 3


def test_qwen_focused_excerpt_prefers_question_related_sentences() -> None:
    excerpt = (
        "제품 외관과 포장 정보를 설명한다. "
        "설치 전에는 전원 차단과 재가동 방지 상태를 확인한다. "
        "청소 후 작업 구역 정리 상태를 확인한다. "
        "부록에는 보증 조건이 정리되어 있다."
    )

    focused = ChatService._focused_excerpt(excerpt, "설치 전 전원 차단 확인")

    assert "전원 차단" in focused
    assert "보증 조건" not in focused


def test_maintenance_qwen_source_selection_keeps_relevant_incident_with_limit_two() -> None:
    sources = [
        {
            **_grounded_source("public_guide"),
            "document_id": "noise-doc",
            "chunk_id": "noise-1",
            "title": "설비 소음 관리 지침",
            "excerpt": "작업장 소음 노출 구역을 평가하고 흡음 대책을 검토한다.",
            "similarity": 0.66,
            "reranker_score": 0.66,
        },
        {
            **_grounded_source("public_guide"),
            "document_id": "noise-doc",
            "chunk_id": "noise-2",
            "title": "설비 소음 관리 지침",
            "excerpt": "소음 저감용 차단막과 흡음 패널을 설치할 수 있다.",
            "similarity": 0.65,
            "reranker_score": 0.65,
        },
        {
            **_grounded_source("public_incident"),
            "document_id": "incident-doc",
            "chunk_id": "incident-1",
            "title": "설비 내부 이물질 제거 중 협착 사고",
            "excerpt": (
                "운전중인 설비 내부 이물질을 제거하다가 협착되었다. "
                "기계의 운전을 정지한 후 작업해야 한다."
            ),
            "similarity": 0.58,
            "reranker_score": 0.58,
        },
    ]
    selected = ChatService._select_maintenance_qwen_sources(
        "설비에 끼인 이물질을 제거하려고 해.",
        [ChatSource.model_validate(source) for source in sources],
        limit=2,
    )

    assert len(selected) == 2
    assert "incident-1" in {source.chunk_id for source in selected}
    assert len({source.document_id for source in selected}) == 2


def test_qwen_focused_excerpt_cleans_dataset_prefix_and_table_fragments() -> None:
    excerpt = (
        "[자료유형] KOSHA GUIDE [제목] 일반 지침 [내용] "
        "| No. | 점검 항목 | 확인 | | 1 | 기계의 위험 영역 근처에 "
        "작업자가 없는 상태에서 점검한다. | | "
        "제품 보증 조건은 별도 문서를 따른다."
    )

    focused = ChatService._focused_excerpt(excerpt, "기계 내부 점검 위험 영역")

    assert "[자료유형]" not in focused
    assert "|" not in focused
    assert "위험 영역" in focused


def test_qwen_timeout_warns_and_keeps_retrieved_template() -> None:
    class TimeoutQwenClient:
        async def classify(self, request: ChatRequest) -> QueryAnalysis:
            return QueryAnalysis(
                question_intent="maintenance_guide",
                intent_confidence=0.98,
            )

        async def answer(
            self,
            request: ChatRequest,
            retrieval_response: ChatResponse,
        ) -> QwenAnswerFailure:
            return QwenAnswerFailure(
                reason="timeout",
                detail="Qwen /v1/answer timed out after 600s.",
            )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "answer": "검색 기본 답변",
                "sources": [_grounded_source("public_guide")],
                "retrieval_mode": "hybrid",
            },
        )

    response = asyncio.run(
        ChatService(
            service_url="http://rag.test",
            transport=httpx.MockTransport(handler),
            openai_enabled=False,
            qwen_client=TimeoutQwenClient(),  # type: ignore[arg-type]
            qwen_enabled=True,
            qwen_allow_company_context=True,
        ).answer(ChatRequest(question="프레스 설비 내부를 청소할 예정이야."))
    )

    assert response.generation_mode == "template"
    assert "Qwen 답변 생성 실패(timeout)" in (response.warning or "")
    assert "BGE-M3" in (response.warning or "")
    assert response.structured_answer is not None
    assert response.checklist_items


def test_invalid_qwen_source_reference_falls_back_without_500() -> None:
    class InvalidStructuredQwenClient:
        async def classify(self, request: ChatRequest) -> QueryAnalysis:
            return QueryAnalysis(question_intent="component_info")

        async def answer(
            self,
            request: ChatRequest,
            retrieval_response: ChatResponse,
        ) -> QwenGeneratedAnswer:
            return QwenGeneratedAnswer(
                answer="검증되지 않은 구조",
                model="qwen-test",
                used_source_ids=("invented-chunk",),
            )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "answer": "검증된 검색 fallback",
                "sources": [_grounded_source("public_guide")],
                "retrieval_mode": "hybrid",
            },
        )

    response = asyncio.run(
        ChatService(
            service_url="http://rag.test",
            transport=httpx.MockTransport(handler),
            openai_enabled=False,
            qwen_client=InvalidStructuredQwenClient(),  # type: ignore[arg-type]
            qwen_enabled=True,
        ).answer(ChatRequest(question="라이트커튼이 무슨 장비야?"))
    )

    assert response.answer == "검증된 검색 fallback"
    assert response.answer_type == "component_info"
    assert "검색되지 않은 출처" in (response.warning or "")


def test_visual_question_uses_local_generic_shape_without_document_evidence() -> None:
    response = asyncio.run(
        ChatService(service_url=None, openai_enabled=False).answer(
            ChatRequest.model_validate(
                {
                    "question": "그럼 이건 뭐야?",
                    "context": {
                        "visual_categories": ["사진상 육각 머리 볼트"],
                        "visual_features": ["육각형 머리와 나사산이 보임"],
                    },
                }
            )
        )
    )

    assert response.generation_mode == "template"
    assert "육각 머리 볼트" in response.answer
    assert "육각형 머리" in response.answer
    assert "정확한 제품명" in response.answer


def test_visual_usage_question_returns_only_generic_usage() -> None:
    response = asyncio.run(
        ChatService(service_url=None, openai_enabled=False).answer(
            ChatRequest.model_validate(
                {
                    "question": "이건 어디에 쓰여?",
                    "context": {
                        "visual_categories": ["사진상 둥근 머리 내부 육각 소켓 나사"],
                    },
                }
            )
        )
    )

    assert "부품을 서로 체결" in response.answer
    assert "규격" in response.answer


def test_visual_question_does_not_retrieve_unrelated_selected_manual() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("RAG must not run for a locally analyzed photo question")

    response = asyncio.run(
        ChatService(
            service_url="http://rag.test",
            transport=httpx.MockTransport(handler),
            openai_enabled=False,
        ).answer(
            ChatRequest.model_validate(
                {
                    "question": "이 사진은 뭐야?",
                    "context": {
                        "visual_categories": ["USB 플래시 메모리"],
                        "visual_features": ["USB 단자와 16 GB 표기가 보임"],
                        "registered_manuals": ["NSK_ballbearing (1).pdf"],
                    },
                }
            )
        )
    )

    assert response.retrieval_mode == "safety-fallback"
    assert response.sources == []
    assert "USB 플래시 메모리" in response.answer
    assert "베어링" not in response.answer


def test_visual_question_abstains_when_no_verified_category_exists() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("RAG must not guess from unrelated manuals")

    response = asyncio.run(
        ChatService(
            service_url="http://rag.test",
            transport=httpx.MockTransport(handler),
            openai_enabled=False,
        ).answer(
            ChatRequest.model_validate(
                {
                    "question": "이건 뭐야?",
                    "context": {
                        "visual_summary": "신뢰 임계값을 넘는 카탈로그 후보 없음",
                        "visual_categories": [],
                        "registered_manuals": ["NSK_ballbearing (1).pdf"],
                    },
                }
            )
        )
    )

    assert response.retrieval_mode == "safety-fallback"
    assert response.sources == []
    assert "확인하지 못했습니다" in response.answer
    assert "임의의 제품명이나 용도를 안내하지 않습니다" in response.answer
    assert "베어링" not in response.answer
 
