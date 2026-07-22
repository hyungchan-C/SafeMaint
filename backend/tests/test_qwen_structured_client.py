import asyncio

import httpx

from app.schemas.chat import ChatRequest, ChatResponse, ChatSource
from app.services.qwen import QwenClient


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
