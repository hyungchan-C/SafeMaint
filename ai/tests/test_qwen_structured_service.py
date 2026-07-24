import asyncio
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


def test_qwen_empty_component_sections_are_not_backfilled(monkeypatch) -> None:
    engine = QwenEngine(Settings())
    payload = {
        "answer": "라이트 커튼은 안전 장치입니다.",
        "answer_type": "component_info",
        "structured_answer": {
            "answer_type": "component_info",
            "one_line_description": "PC 설정 툴을 통해 설정 및 변경이 가능한 안전 장치입니다.",
            "main_roles": [],
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
            question="라이트 커튼이 뭐야?",
            answer_type="component_info",
            sources=[
                ChatSource(
                    document_id="doc-1",
                    chunk_id="chunk-1",
                    title="라이트 커튼 사용자 매뉴얼",
                    source_type="equipment_manual",
                    excerpt=(
                        "라이트 커튼은 위험구역 접근과 광축 차단을 검출한다. "
                        "PC 설정 툴로 기능을 변경한 후 의도한 대로 동작하는지 확인한다."
                    ),
                )
            ],
        )
    )

    assert response.structured_answer is not None
    assert response.structured_answer.one_line_description == (
        "PC 설정 툴을 통해 설정 및 변경이 가능한 안전 장치입니다."
    )
    assert response.structured_answer.main_roles == []
    assert response.structured_answer.usage_locations == []
    assert response.structured_answer.precautions == []


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


def test_qwen_nested_answer_json_is_unwrapped_and_source_numbers_are_normalized(
    monkeypatch,
) -> None:
    engine = QwenEngine(Settings())
    nested_payload = {
        "answer": "라이트커튼은 광축 차단을 감지하는 안전장치입니다.",
        "answer_type": "component_info",
        "structured_answer": {
            "answer_type": "component_info",
            "one_line_description": "광축 차단을 감지하는 안전장치",
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
    }
    monkeypatch.setattr(
        engine,
        "_generate",
        lambda *args, **kwargs: json.dumps(
            {"answer": json.dumps(nested_payload, ensure_ascii=False)},
            ensure_ascii=False,
        ),
    )

    response = engine._answer_sync(
        AnswerRequest(
            question="라이트커튼이 무슨 장비야?",
            answer_type="component_info",
            sources=[
                ChatSource(
                    document_id="doc-1",
                    chunk_id="demo-chunk-1",
                    title="라이트커튼 제품 설명서",
                    source_type="component_manual",
                    excerpt="라이트커튼은 투광기와 수광기 사이의 광축 차단을 감지하는 안전장치이다.",
                )
            ],
        )
    )

    assert response.answer == "라이트커튼은 광축 차단을 감지하는 안전장치입니다."
    assert response.structured_answer is not None
    assert response.structured_answer.evidence_chunk_ids == ["demo-chunk-1"]
    assert response.used_source_ids == ["demo-chunk-1"]


def test_qwen_missing_required_fields_are_normalized_without_repair(monkeypatch) -> None:
    engine = QwenEngine(Settings())
    invalid_payload = {
        "answer": "설치 전 확인이 필요합니다.",
        "answer_type": "maintenance_guide",
        "structured_answer": {
            "answer_type": "maintenance_guide",
            "summary": {
                "status": "안전관리자 확인 필요",
                "risk_level": "판단 불가",
                "risk_basis": [],
            },
            "hazards": [
                {
                    "content": "광축 정렬 불량",
                    "evidence_chunk_ids": [1],
                    "sequence": 1,
                    "is_required": True,
                }
            ],
            "manual_steps": [],
            "pre_checks": [],
            "stop_conditions": [],
            "related_regulations_and_incidents": [],
            "evidence_chunk_ids": [1],
            "conflicts": [],
            "additional_information_needed": [],
        },
        "checklist_items": [],
        "used_source_ids": [1],
    }
    repaired_payload = {
        "answer": "설치 전 제조사 기준을 확인하세요.",
        "answer_type": "maintenance_guide",
        "structured_answer": {
            "answer_type": "maintenance_guide",
            "summary": {
                "status": "안전관리자 확인 필요",
                "risk_level": "판단 불가",
                "risk_basis": [],
                "core_warning": "설치 기준 확인 전에는 작업을 시작하지 마세요.",
            },
            "pre_checks": [
                {"content": "설치 거리 기준을 확인합니다.", "evidence_chunk_ids": [1]}
            ],
            "hazards": [
                {
                    "name": "광축 정렬 불량",
                    "content": "감지 성능이 저하될 수 있습니다.",
                    "evidence_chunk_ids": [1],
                }
            ],
            "manual_steps": [
                {"content": "광축 정렬 상태를 확인합니다.", "evidence_chunk_ids": [1]}
            ],
            "stop_conditions": [],
            "related_regulations_and_incidents": [],
            "evidence_chunk_ids": [1],
            "conflicts": [],
            "additional_information_needed": [],
        },
        "checklist_items": [
            {"content": "설치 거리 기준 확인", "sequence": 99, "evidence_chunk_ids": [1]}
        ],
        "used_source_ids": [1],
    }
    outputs = iter(
        [
            json.dumps(invalid_payload, ensure_ascii=False),
            json.dumps(repaired_payload, ensure_ascii=False),
        ]
    )
    monkeypatch.setattr(engine, "_generate", lambda *args, **kwargs: next(outputs))

    response = engine._answer_sync(
        AnswerRequest(
            question="라이트커튼 설치 시 주의사항",
            answer_type="maintenance_guide",
            sources=[
                ChatSource(
                    document_id="doc-1",
                    chunk_id="manual-chunk-1",
                    title="라이트커튼 설치 매뉴얼",
                    source_type="component_manual",
                    excerpt="설치 전 모델별 안전거리와 광축 정렬 기준을 확인한다.",
                )
            ],
        )
    )

    assert response.answer == "설치 전 확인이 필요합니다."
    assert response.structured_answer is not None
    assert response.structured_answer.summary.core_warning
    assert response.structured_answer.hazards[0].name == "정렬 불량"
    assert response.structured_answer.hazards[0].evidence_chunk_ids == ["manual-chunk-1"]
    assert response.checklist_items == []


def test_qwen_maintenance_payload_is_normalized_without_repair(monkeypatch) -> None:
    engine = QwenEngine(Settings())
    payload = {
        "answer": "설치 전 안전거리와 광축 정렬을 확인하세요.",
        "answer_type": "maintenance_guide",
        "structured_answer": {
            "answer_type": "maintenance_guide",
            "summary": {
                "status": "안전관리자 확인 필요",
                "risk_level": "보통",
                "risk_basis": [
                    {
                        "content": "광축 정렬 기준을 확인해야 함",
                        "evidence_chunk_ids": [1],
                    }
                ],
            },
            "pre_checks": [
                {
                    "id": None,
                    "content": "설치 전 모델별 안전거리 기준을 확인합니다.",
                    "sequence": 7,
                    "is_required": True,
                    "evidence_chunk_ids": [1],
                }
            ],
            "hazards": [
                {
                    "id": None,
                    "content": "광축 정렬 불량 시 감지 실패가 발생할 수 있습니다.",
                    "sequence": 1,
                    "is_required": True,
                    "is_completed": False,
                    "evidence_chunk_ids": [1],
                }
            ],
            "manual_steps": [
                {
                    "content": "설치 전 광축 정렬 상태를 확인합니다.",
                    "evidence_chunk_ids": [1],
                }
            ],
            "stop_conditions": [],
            "related_regulations_and_incidents": [],
            "evidence_chunk_ids": [1],
            "conflicts": [],
            "additional_information_needed": [
                {"content": "정확한 모델명"},
                "현장 작업표준",
            ],
        },
        "checklist_items": [
            {"content": "Qwen이 만든 체크리스트", "sequence": 99, "evidence_chunk_ids": [1]}
        ],
        "used_source_ids": [1],
    }
    calls: list[str] = []

    def fake_generate(*args, **kwargs):
        calls.append("generate")
        return json.dumps(payload, ensure_ascii=False)

    monkeypatch.setattr(engine, "_generate", fake_generate)

    response = engine._answer_sync(
        AnswerRequest(
            question="라이트커튼 설치 시 주의사항",
            answer_type="maintenance_guide",
            sources=[
                ChatSource(
                    document_id="doc-1",
                    chunk_id="manual-chunk-1",
                    title="라이트커튼 설치 매뉴얼",
                    source_type="component_manual",
                    excerpt="설치 전 모델별 안전거리와 광축 정렬 기준을 확인한다.",
                )
            ],
        )
    )

    assert calls == ["generate"]
    assert response.answer == "설치 전 안전거리와 광축 정렬을 확인하세요."
    assert response.structured_answer is not None
    assert response.structured_answer.summary.core_warning
    assert response.structured_answer.summary.risk_level == "보통"
    assert response.structured_answer.pre_checks[0].evidence_chunk_ids == [
        "manual-chunk-1"
    ]
    assert response.structured_answer.hazards[0].name == "정렬 불량"
    assert response.structured_answer.additional_information_needed == [
        "정확한 모델명",
        "현장 작업표준",
    ]
    assert len(response.checklist_items) == 1
    assert response.checklist_items[0].content == "Qwen이 만든 체크리스트"
    assert response.checklist_items[0].evidence_chunk_ids == ["manual-chunk-1"]


def test_qwen_repeated_invalid_json_returns_safe_structured_fallback(monkeypatch) -> None:
    engine = QwenEngine(Settings())
    monkeypatch.setattr(
        engine,
        "_generate",
        lambda *args, **kwargs: '{"answer": {"still": "invalid"}}',
    )

    response = engine._answer_sync(
        AnswerRequest(
            question="라이트커튼 설치 시 주의사항",
            answer_type="maintenance_guide",
            sources=[
                ChatSource(
                    document_id="doc-1",
                    chunk_id="manual-chunk-1",
                    title="라이트커튼 설치 매뉴얼",
                    source_type="component_manual",
                    excerpt="설치 전 모델별 안전거리와 광축 정렬 기준을 확인한다.",
                )
            ],
        )
    )

    assert response.structured_answer is not None
    assert response.structured_answer.answer_type == "maintenance_guide"
    assert response.structured_answer.manual_steps == []
    assert response.checklist_items == []
    assert response.used_source_ids == []


def test_qwen_fallback_does_not_generate_checklist_from_incident_keywords(
    monkeypatch,
) -> None:
    engine = QwenEngine(Settings())
    monkeypatch.setattr(
        engine,
        "_generate",
        lambda *args, **kwargs: '{"answer": {"still": "invalid"}}',
    )

    response = engine._answer_sync(
        AnswerRequest(
            question="설비 내부 이물질을 제거하려고 해.",
            answer_type="maintenance_guide",
            sources=[
                ChatSource(
                    document_id="doc-1",
                    chunk_id="incident-1",
                    title="이물질 제거 중 협착 사고",
                    source_type="public_incident",
                    excerpt=(
                        "운전중인 설비 내부 이물질을 제거하다가 협착되었다. "
                        "기계의 운전을 정지하고 비상정지장치를 확인하여야 한다."
                    ),
                )
            ],
        )
    )

    assert response.checklist_items == []


def test_qwen_incident_only_checklist_is_rejected(monkeypatch) -> None:
    engine = QwenEngine(Settings())
    payload = {
        "answer": "사고사례 원문을 확인하세요.",
        "answer_type": "maintenance_guide",
        "structured_answer": {
            "answer_type": "maintenance_guide",
            "summary": {
                "status": "근거 부족",
                "risk_level": "판단 불가",
                "risk_basis": [],
                "core_warning": "매뉴얼 작업 절차는 확인되지 않았습니다.",
            },
            "pre_checks": [],
            "hazards": [],
            "manual_steps": [],
            "stop_conditions": [],
            "related_regulations_and_incidents": [
                {
                    "content": "사고사례 원문",
                    "evidence_chunk_ids": ["incident-1"],
                }
            ],
            "evidence_chunk_ids": ["incident-1"],
            "conflicts": [],
            "additional_information_needed": [],
        },
        "checklist_items": [
            {
                "content": "사고사례를 작업 절차로 변환한 항목",
                "evidence_chunk_ids": ["incident-1"],
            }
        ],
        "used_source_ids": ["incident-1"],
    }
    monkeypatch.setattr(
        engine,
        "_generate",
        lambda *args, **kwargs: json.dumps(payload, ensure_ascii=False),
    )

    response = engine._answer_sync(
        AnswerRequest(
            question="설비 내부 이물질을 제거하려고 해.",
            answer_type="maintenance_guide",
            sources=[
                ChatSource(
                    document_id="doc-1",
                    chunk_id="incident-1",
                    title="이물질 제거 중 협착 사고",
                    source_type="public_incident",
                    excerpt="운전 중 설비 내부 이물질을 제거하다가 협착되었다.",
                )
            ],
        )
    )

    assert response.checklist_items == []


def test_qwen_answer_route_returns_safe_fallback_on_unhandled_exception(
    monkeypatch,
) -> None:
    from qwen_service import main as qwen_main

    async def broken_answer(request: AnswerRequest):
        raise RuntimeError("GPU generation failed")

    monkeypatch.setattr(qwen_main.engine, "answer", broken_answer)

    response = asyncio.run(
        qwen_main.answer(
            AnswerRequest(
                question="라이트커튼 설치 시 주의사항",
                answer_type="maintenance_guide",
                sources=[
                    ChatSource(
                        document_id="doc-1",
                        chunk_id="manual-chunk-1",
                        title="라이트커튼 설치 매뉴얼",
                        source_type="component_manual",
                        excerpt="설치 전 모델별 안전거리와 광축 정렬 기준을 확인한다.",
                    )
                ],
            )
        )
    )

    assert response.answer_type == "maintenance_guide"
    assert response.structured_answer is not None
    assert response.structured_answer.answer_type == "maintenance_guide"
    assert response.checklist_items == []
    assert response.used_source_ids == []
