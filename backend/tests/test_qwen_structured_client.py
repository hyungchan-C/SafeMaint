import asyncio
import json

import httpx

from app.schemas.chat import ChatRequest, ChatResponse, ChatSource
from app.services.qwen import QwenAnswerFailure, QwenClient


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
