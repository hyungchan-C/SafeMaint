import json

from qwen_service.config import Settings
from qwen_service.model import QwenEngine
from qwen_service.schemas import AnswerRequest, ChatSource, ClassifyRequest


def test_qwen_classifier_returns_question_intent(monkeypatch) -> None:
    engine = QwenEngine(Settings())
    monkeypatch.setattr(
        engine,
        "_generate",
        lambda *args, **kwargs: json.dumps(
            {
                "occurrence_type": "기타",
                "confidence": 0.6,
                "question_intent": "component_info",
                "intent_confidence": 0.98,
                "clarification_question": None,
            },
            ensure_ascii=False,
        ),
    )

    response = engine._classify_sync(
        ClassifyRequest(question="라이트커튼이 무슨 장비야?")
    )

    assert response.question_intent == "component_info"
    assert response.analysis is not None
    assert response.analysis.question_intent == "component_info"


def test_qwen_component_answer_has_no_checklist(monkeypatch) -> None:
    engine = QwenEngine(Settings())
    payload = {
        "answer": "라이트커튼 부품 정보 [1]",
        "answer_type": "component_info",
        "structured_answer": {
            "answer_type": "component_info",
            "one_line_description": "광축 차단을 감지하는 장치입니다.",
            "main_roles": [
                {"content": "광축 차단 감지", "evidence_chunk_ids": ["chunk-1"]}
            ],
            "usage_locations": [],
            "precautions": [],
            "evidence_chunk_ids": ["chunk-1"],
            "additional_information_needed": [],
        },
        "checklist_items": [],
        "used_source_ids": ["chunk-1"],
    }
    monkeypatch.setattr(
        engine,
        "_generate",
        lambda *args, **kwargs: json.dumps(payload, ensure_ascii=False),
    )

    response = engine._answer_sync(
        AnswerRequest(
            question="라이트커튼이 무슨 장비야?",
            answer_type="component_info",
            sources=[
                ChatSource(
                    document_id="doc-1",
                    chunk_id="chunk-1",
                    title="매뉴얼",
                    source_type="component_manual",
                    excerpt="광축 차단을 감지하는 장치",
                )
            ],
        )
    )

    assert response.answer_type == "component_info"
    assert response.structured_answer is not None
    assert response.checklist_items == []


def test_qwen_malformed_json_returns_legacy_fallback_without_exception(monkeypatch) -> None:
    engine = QwenEngine(Settings())
    monkeypatch.setattr(
        engine,
        "_generate",
        lambda *args, **kwargs: "JSON이 아닌 일반 답변",
    )

    response = engine._answer_sync(
        AnswerRequest(
            question="베어링 교체 방법",
            answer_type="maintenance_guide",
            sources=[],
        )
    )

    assert response.answer == "JSON이 아닌 일반 답변"
    assert response.answer_type == "maintenance_guide"
    assert response.structured_answer is None
