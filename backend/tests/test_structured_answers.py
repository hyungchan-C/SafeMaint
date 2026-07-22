import pytest
from pydantic import ValidationError

from app.schemas.chat import (
    ChatChecklistItem,
    ChatSource,
    EvidenceBackedItem,
    EvidenceConflict,
    MaintenanceAnswerDetails,
    MaintenanceSummary,
)
from app.services.structured_answers import (
    source_based_fallback,
    validated_checklist_items,
    validated_structured_answer,
)


def _source(chunk_id: str, source_type: str) -> ChatSource:
    return ChatSource(
        document_id=f"doc-{chunk_id}",
        chunk_id=chunk_id,
        title="verified source",
        source_type=source_type,
        excerpt="verified excerpt",
        similarity=0.8,
    )


def test_document_fallback_never_contains_tbm_fields() -> None:
    details = source_based_fallback("document_qa", [_source("manual-1", "component_manual")])

    assert details is not None
    assert details.answer_type == "document_qa"
    assert "checklist" not in details.model_dump()
    assert "hazards" not in details.model_dump()


def test_component_fallback_never_contains_maintenance_steps() -> None:
    details = source_based_fallback("component_info", [_source("manual-1", "component_manual")])

    assert details is not None
    assert details.answer_type == "component_info"
    assert "manual_steps" not in details.model_dump()


def test_unretrieved_source_reference_rejects_structured_answer() -> None:
    details = MaintenanceAnswerDetails(
        summary=MaintenanceSummary(
            status="안전관리자 확인 필요",
            risk_level="판단 불가",
            risk_basis=[],
            core_warning="작업 전 확인 필요",
        ),
        pre_checks=[
            EvidenceBackedItem(
                content="확인 항목",
                evidence_chunk_ids=["invented-chunk"],
            )
        ],
    )

    assert validated_structured_answer(
        details,
        expected_type="maintenance_guide",
        sources=[_source("manual-1", "component_manual")],
    ) is None


def test_manual_steps_require_manual_source() -> None:
    details = MaintenanceAnswerDetails(
        summary=MaintenanceSummary(
            status="안전관리자 확인 필요",
            risk_level="보통",
            risk_basis=[
                EvidenceBackedItem(
                    content="공용 안전자료",
                    evidence_chunk_ids=["guide-1"],
                )
            ],
            core_warning="작업 전 확인 필요",
        ),
        manual_steps=[
            EvidenceBackedItem(
                content="임의 절차",
                evidence_chunk_ids=["guide-1"],
            )
        ],
    )

    validated = validated_structured_answer(
        details,
        expected_type="maintenance_guide",
        sources=[_source("guide-1", "public_guide")],
    )

    assert validated is not None
    assert validated.manual_steps == []


def test_risk_level_falls_back_when_risk_basis_has_no_verified_source() -> None:
    details = MaintenanceAnswerDetails(
        summary=MaintenanceSummary(
            status="안전관리자 확인 필요",
            risk_level="높음",
            risk_basis=[],
            core_warning="작업 전 확인 필요",
        ),
    )

    validated = validated_structured_answer(
        details,
        expected_type="maintenance_guide",
        sources=[_source("manual-1", "component_manual")],
    )

    assert validated is not None
    assert validated.summary.risk_level == "판단 불가"
    assert validated.summary.risk_basis == []


def test_checklist_items_require_retrieved_source() -> None:
    items = [
        ChatChecklistItem(
            content="검증된 항목",
            sequence=9,
            evidence_chunk_ids=["manual-1"],
        ),
        ChatChecklistItem(
            content="근거 없는 항목",
            sequence=10,
            evidence_chunk_ids=[],
        ),
    ]

    validated = validated_checklist_items(
        items,
        sources=[_source("manual-1", "component_manual")],
    )

    assert [item.content for item in validated] == ["검증된 항목"]
    assert validated[0].sequence == 1


def test_conflict_requires_two_distinct_evidence_chunks() -> None:
    with pytest.raises(ValidationError):
        EvidenceConflict(
            content="근거 내용이 서로 다릅니다.",
            evidence_chunk_ids=["manual-1", "manual-1"],
        )
