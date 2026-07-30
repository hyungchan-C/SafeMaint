import asyncio
import json

import httpx

from app.schemas.chat import ChatRequest, ChatResponse, ChatSource
from app.services.qwen import QwenAnswerFailure, QwenClient


def test_qwen_client_sends_ngrok_skip_warning_header() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["ngrok-skip-browser-warning"] == "true"
        return httpx.Response(
            200,
            json={
                "answer": "근거 답변 [1]",
                "model": "qwen-test",
                "used_source_ids": ["chunk-1"],
            },
        )

    retrieval_response = ChatResponse(
        answer="검색 답변",
        answer_type="component_info",
        sources=[
            ChatSource(
                document_id="doc-1",
                chunk_id="chunk-1",
                title="검증 근거",
                source_type="public_guide",
                excerpt="검증된 내용",
                similarity=0.8,
            )
        ],
        retrieval_mode="hybrid",
    )

    result = asyncio.run(
        QwenClient(
            service_url="http://qwen.test",
            transport=httpx.MockTransport(handler),
        ).answer(
            ChatRequest(question="라이트커튼이 무슨 장비야?"),
            retrieval_response,
        )
    )

    assert result is not None
    assert result.answer == "근거 답변 [1]"


def test_qwen_client_parses_fallback_reason() -> None:
    # qwen_service usually runs on a remote host, so the specific reason it fell
    # back to a canned answer only reaches us if it's carried in the response
    # body — this checks QwenClient actually reads that field through.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "answer": "검색된 근거를 기준으로 작업 전 확인할 핵심 사항을 요약했습니다.",
                "model": "qwen-test",
                "used_source_ids": ["chunk-1"],
                "fallback_reason": "maintenance compact answer has no card items.",
            },
        )

    retrieval_response = ChatResponse(
        answer="검색 답변",
        answer_type="maintenance_guide",
        sources=[
            ChatSource(
                document_id="doc-1",
                chunk_id="chunk-1",
                title="검증 근거",
                source_type="equipment_manual",
                excerpt="검증된 내용",
                similarity=0.8,
            )
        ],
        retrieval_mode="hybrid",
    )

    result = asyncio.run(
        QwenClient(
            service_url="http://qwen.test",
            transport=httpx.MockTransport(handler),
        ).answer(
            ChatRequest(question="라이트커튼 점검 절차 알려줘"),
            retrieval_response,
        )
    )

    assert result is not None
    assert result.fallback_reason == "maintenance compact answer has no card items."


def test_invalid_qwen_structured_json_keeps_legacy_answer() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "answer": "기존 문자열 답변",
                "model": "qwen-test",
                "answer_type": "component_info",
                "structured_answer": {"answer_type": "component_info"},
                "checklist_items": [{"unexpected": "value"}],
                "used_source_ids": ["chunk-1"],
            },
        )

    retrieval_response = ChatResponse(
        answer="검색 답변",
        answer_type="component_info",
        sources=[
            ChatSource(
                document_id="doc-1",
                chunk_id="chunk-1",
                title="검증 근거",
                source_type="public_guide",
                excerpt="검증된 내용",
                similarity=0.8,
            )
        ],
        retrieval_mode="hybrid",
    )

    result = asyncio.run(
        QwenClient(
            service_url="http://qwen.test",
            transport=httpx.MockTransport(handler),
        ).answer(
            ChatRequest(question="라이트커튼이 무슨 장비야?"),
            retrieval_response,
        )
    )

    assert result is not None
    assert result.answer == "기존 문자열 답변"
    assert result.structured_answer is None
    assert result.checklist_items == ()


def test_qwen_client_returns_failure_details_for_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="maintenance schema exploded")

    retrieval_response = ChatResponse(
        answer="검색 답변",
        answer_type="maintenance_guide",
        sources=[
            ChatSource(
                document_id="doc-1",
                chunk_id="chunk-1",
                title="검증 근거",
                source_type="public_guide",
                excerpt="검증된 내용",
                similarity=0.8,
            )
        ],
        retrieval_mode="hybrid",
    )

    result = asyncio.run(
        QwenClient(
            service_url="http://qwen.test",
            transport=httpx.MockTransport(handler),
        ).answer(
            ChatRequest(question="프레스 설비 내부를 청소할 예정이야."),
            retrieval_response,
        )
    )

    assert isinstance(result, QwenAnswerFailure)
    assert result.reason == "http_500"
    assert "maintenance schema exploded" in (result.detail or "")


def test_qwen_client_returns_failure_details_for_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow generation", request=request)

    retrieval_response = ChatResponse(
        answer="검색 답변",
        answer_type="maintenance_guide",
        sources=[
            ChatSource(
                document_id="doc-1",
                chunk_id="chunk-1",
                title="검증 근거",
                source_type="public_guide",
                excerpt="검증된 내용",
                similarity=0.8,
            )
        ],
        retrieval_mode="hybrid",
    )

    result = asyncio.run(
        QwenClient(
            service_url="http://qwen.test",
            transport=httpx.MockTransport(handler),
            timeout_seconds=3,
        ).answer(
            ChatRequest(question="프레스 설비 내부를 청소할 예정이야."),
            retrieval_response,
        )
    )

    assert isinstance(result, QwenAnswerFailure)
    assert result.reason == "timeout"
    assert "3s" in (result.detail or "")


def test_qwen_client_unwraps_nested_json_and_normalizes_numbered_sources() -> None:
    nested_payload = {
        "answer": "라이트커튼은 광축 차단을 감지하는 안전장치입니다.",
        "answer_type": "component_info",
        "structured_answer": {
            "answer_type": "component_info",
            "one_line_description": "광축 차단 감지 안전장치",
            "main_roles": [
                {"content": "광축 차단 감지", "evidence_chunk_ids": [1]}
            ],
            "usage_locations": [],
            "precautions": [],
            "evidence_chunk_ids": ["1"],
            "conflicts": [],
            "additional_information_needed": [],
        },
        "checklist_items": [],
        "used_source_ids": [1],
        "model": "qwen-test",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "answer": json.dumps(nested_payload, ensure_ascii=False),
                "model": "qwen-test",
            },
        )

    retrieval_response = ChatResponse(
        answer="검색 답변",
        answer_type="component_info",
        sources=[
            ChatSource(
                document_id="doc-1",
                chunk_id="chunk-1",
                title="검증 근거",
                source_type="component_manual",
                excerpt="광축 차단을 감지하는 장치",
                similarity=0.8,
            )
        ],
        retrieval_mode="hybrid",
    )

    result = asyncio.run(
        QwenClient(
            service_url="http://qwen.test",
            transport=httpx.MockTransport(handler),
        ).answer(
            ChatRequest(question="라이트커튼이 무슨 장비야?"),
            retrieval_response,
        )
    )

    assert result is not None
    assert result.structured_answer is not None
    assert result.structured_answer.evidence_chunk_ids == ["chunk-1"]
    assert result.used_source_ids == ("chunk-1",)


def test_qwen_classify_normalizes_colab_alias_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/classify"
        return httpx.Response(
            200,
            json={
                "analysis": {
                    "answer_type": "maintenance_guide",
                    "confidence": 0.93,
                    "equipment_name": "컨베이어 CV-203",
                    "target_component": ["베어링", "축"],
                    "maintenance_action": "교체",
                    "hazards": ["협착"],
                    "energy_source": "전기",
                    "keywords": ["베어링 교체", "LOTO"],
                    "unknown_future_field": {"ignored": True},
                }
            },
        )

    result = asyncio.run(
        QwenClient(
            service_url="http://qwen.test",
            transport=httpx.MockTransport(handler),
        ).classify(ChatRequest(question="컨베이어 베어링을 교체하려고 해."))
    )

    assert result is not None
    assert result.question_intent == "maintenance_guide"
    assert result.intent_confidence == 0.93
    assert result.equipment == ["컨베이어 CV-203"]
    assert result.component == ["베어링", "축"]
    assert result.work_type == "교체"
    assert result.explicit_risk_factors == ["협착"]
    assert result.energy_sources == ["전기"]
    assert result.search_keywords == ["베어링 교체", "LOTO"]


def test_qwen_answer_infers_used_source_ids_from_numbered_citations() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "answer": "설치 전 안전거리를 확인합니다 [1].",
                "model": "qwen-test",
            },
        )

    retrieval_response = ChatResponse(
        answer="검색 답변",
        answer_type="maintenance_guide",
        sources=[
            ChatSource(
                document_id="doc-1",
                chunk_id="chunk-1",
                title="라이트커튼 매뉴얼",
                source_type="component_manual",
                excerpt="설치 전 안전거리를 확인한다.",
                similarity=0.8,
            )
        ],
        retrieval_mode="hybrid",
    )

    result = asyncio.run(
        QwenClient(
            service_url="http://qwen.test",
            transport=httpx.MockTransport(handler),
        ).answer(
            ChatRequest(question="라이트커튼 설치 방법을 알려줘."),
            retrieval_response,
        )
    )

    assert result is not None
    assert result.used_source_ids == ("chunk-1",)


def test_qwen_classify_rejects_non_object_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["unexpected", "list"])

    result = asyncio.run(
        QwenClient(
            service_url="http://qwen.test",
            transport=httpx.MockTransport(handler),
        ).classify(ChatRequest(question="라이트커튼 설치 방법"))
    )

    assert result is None


def test_qwen_client_extracts_answer_from_truncated_nested_json_string() -> None:
    truncated_nested_json = (
        '{"answer": "라이트 커튼 설치 후 기능 설정과 정상 동작 여부를 확인해야 합니다.", '
        '"answer_type": "maintenance_guide", "structured_answer": {"answer_type":'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "answer": truncated_nested_json,
                "model": "qwen-test",
            },
        )

    retrieval_response = ChatResponse(
        answer="검색 답변",
        answer_type="maintenance_guide",
        sources=[
            ChatSource(
                document_id="doc-1",
                chunk_id="chunk-1",
                title="검증 근거",
                source_type="component_manual",
                excerpt="설치 후 기능 설정과 정상 동작 여부를 확인한다.",
                similarity=0.8,
            )
        ],
        retrieval_mode="hybrid",
    )

    result = asyncio.run(
        QwenClient(
            service_url="http://qwen.test",
            transport=httpx.MockTransport(handler),
        ).answer(
            ChatRequest(question="라이트 커튼 설치할 거야."),
            retrieval_response,
        )
    )

    assert result is not None
    assert result.answer == "라이트 커튼 설치 후 기능 설정과 정상 동작 여부를 확인해야 합니다."
    assert result.structured_answer is None


def test_safe_context_excludes_document_scope_and_vision_observations() -> None:
    request = ChatRequest.model_validate({
        "question": "이 부품은 무엇인가요?",
        "context": {
            "site_name": "A공장",
            "registered_manuals": ["manual.pdf"],
            "selected_document_ids": ["11111111-1111-1111-1111-111111111111"],
            "visual_summary": "사진 분석 원문",
            "visual_categories": ["USB 플래시 메모리"],
            "visual_features": ["16 GB 표기"],
        },
    })

    context = QwenClient._safe_context(request)

    assert context["site_name"] == "A공장"
    assert "registered_manuals" not in context
    assert "selected_document_ids" not in context
    assert "visual_summary" not in context
    assert "visual_categories" not in context
    assert "visual_features" not in context
