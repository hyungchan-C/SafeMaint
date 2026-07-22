from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.schemas.chat import (
    AnswerType,
    ChatChecklistItem,
    ChatSource,
    ClarificationDetails,
    ComponentAnswerDetails,
    DocumentAnswerDetails,
    DocumentOverview,
    EvidenceBackedItem,
    MaintenanceAnswerDetails,
    MaintenanceSummary,
    NoEvidenceDetails,
    StructuredAnswer,
)
from app.services.question_intent import DEFAULT_CLARIFICATION_QUESTION


MANUAL_SOURCE_TYPES = frozenset(
    {"manual", "equipment_manual", "component_manual", "work_standard"}
)


def is_manual_source(source: ChatSource) -> bool:
    return source.source_type.casefold() in MANUAL_SOURCE_TYPES


def clarification_details(question: str | None = None) -> ClarificationDetails:
    return ClarificationDetails(
        question=question or DEFAULT_CLARIFICATION_QUESTION,
        options=["부품의 일반 정보", "설치·점검·교체 방법", "선택한 문서의 내용"],
    )


def no_evidence_details(*, work_related: bool) -> NoEvidenceDetails:
    return NoEvidenceDetails(
        message="질문과 일치하는 검증 가능한 문서 근거를 찾지 못했습니다.",
        required_information=["설비명 또는 부품명", "제조사", "정확한 모델·부품번호"],
        required_documents=["승인된 제조사 매뉴얼 또는 사업장 작업표준"],
        work_safety_notice=(
            "근거를 확인하기 전에는 작업을 진행하지 말고 안전관리자의 확인을 받으세요."
            if work_related
            else None
        ),
    )


def clarification_answer(details: ClarificationDetails) -> str:
    return details.question


def no_evidence_answer(*, work_related: bool) -> str:
    answer = (
        "질문과 일치하는 검증 가능한 문서 근거를 찾지 못했습니다.\n\n"
        "설비명·부품명·제조사·모델번호와 승인된 매뉴얼을 확인한 뒤 다시 질문해 주세요."
    )
    if work_related:
        answer += (
            "\n\n근거를 확인하기 전에는 구체적인 작업 절차를 적용하거나 작업을 진행하지 말고 "
            "안전관리자의 확인을 받으세요."
        )
    return answer


def source_based_fallback(
    answer_type: AnswerType,
    sources: list[ChatSource],
) -> StructuredAnswer | None:
    """Build a conservative structure from retrieved rows, never prose parsing."""

    if not sources:
        return None
    chunk_ids = [source.chunk_id for source in sources]
    first = sources[0]
    source_items = [
        EvidenceBackedItem(
            content=source.excerpt,
            evidence_chunk_ids=[source.chunk_id],
        )
        for source in sources[:5]
    ]

    if answer_type == "document_qa":
        return DocumentAnswerDetails(
            overview=DocumentOverview(
                filename=first.original_filename or first.title,
                document_type=first.source_type,
                version=(
                    str(first.document_version)
                    if first.document_version is not None
                    else None
                ),
            ),
            main_contents=source_items,
            evidence_chunk_ids=chunk_ids,
            unverified_information=[
                "검색된 부분에 없는 문서 메타데이터와 세부 내용은 확인하지 못했습니다."
            ],
        )

    if answer_type == "component_info":
        return ComponentAnswerDetails(
            one_line_description=first.excerpt,
            evidence_chunk_ids=chunk_ids,
            additional_information_needed=["정확한 제조사와 모델명을 확인해 주세요."],
        )

    if answer_type == "maintenance_guide":
        has_manual = any(is_manual_source(source) for source in sources)
        return MaintenanceAnswerDetails(
            summary=MaintenanceSummary(
                status="안전관리자 확인 필요" if has_manual else "근거 부족",
                risk_level="판단 불가",
                risk_basis=[],
                core_warning=(
                    "승인된 매뉴얼 원문과 현장 상태를 대조한 뒤 작업해야 합니다."
                    if has_manual
                    else "승인된 매뉴얼 근거가 없어 구체적인 작업 절차를 제공할 수 없습니다."
                ),
            ),
            evidence_chunk_ids=chunk_ids,
            additional_information_needed=[
                "제조사 매뉴얼의 해당 모델 작업 절차와 현장 작업표준을 확인해 주세요."
            ],
        )
    return None


def validated_structured_answer(
    value: StructuredAnswer | None,
    *,
    expected_type: AnswerType,
    sources: list[ChatSource],
) -> StructuredAnswer | None:
    if value is None or value.answer_type != expected_type:
        return None
    allowed_ids = {source.chunk_id for source in sources}
    referenced_ids = _collect_evidence_ids(value.model_dump(mode="python"))
    if not referenced_ids.issubset(allowed_ids):
        return None

    if isinstance(value, MaintenanceAnswerDetails):
        source_by_id = {source.chunk_id: source for source in sources}
        verified_risk_basis = [
            item
            for item in value.summary.risk_basis
            if item.evidence_chunk_ids
            and set(item.evidence_chunk_ids).issubset(allowed_ids)
        ]
        risk_level = value.summary.risk_level
        if risk_level != "판단 불가" and not verified_risk_basis:
            risk_level = "판단 불가"
        verified_manual_steps = [
            step
            for step in value.manual_steps
            if step.evidence_chunk_ids
            and all(
                chunk_id in source_by_id
                and is_manual_source(source_by_id[chunk_id])
                for chunk_id in step.evidence_chunk_ids
            )
        ]
        verified_public_references = [
            item
            for item in value.related_regulations_and_incidents
            if item.evidence_chunk_ids
            and all(
                source_by_id[chunk_id].source_type.casefold()
                in {"public_guide", "public_incident", "regulation", "incident"}
                for chunk_id in item.evidence_chunk_ids
            )
        ]
        value = value.model_copy(
            update={
                "summary": value.summary.model_copy(
                    update={
                        "risk_level": risk_level,
                        "risk_basis": verified_risk_basis,
                    }
                ),
                "manual_steps": verified_manual_steps,
                "related_regulations_and_incidents": verified_public_references,
            }
        )
    elif isinstance(value, DocumentAnswerDetails):
        first = sources[0]
        value = value.model_copy(
            update={
                "overview": DocumentOverview(
                    filename=first.original_filename or first.title,
                    document_type=first.source_type,
                    version=(
                        str(first.document_version)
                        if first.document_version is not None
                        else None
                    ),
                )
            }
        )
    return value


def validated_checklist_items(
    items: Iterable[ChatChecklistItem],
    *,
    sources: list[ChatSource],
) -> list[ChatChecklistItem]:
    allowed_ids = {source.chunk_id for source in sources}
    validated: list[ChatChecklistItem] = []
    for item in items:
        if not item.evidence_chunk_ids:
            continue
        if not set(item.evidence_chunk_ids).issubset(allowed_ids):
            continue
        validated.append(
            item.model_copy(
                update={
                    "id": None,
                    "sequence": len(validated) + 1,
                    "is_completed": False,
                    "completed_by_user_id": None,
                    "completed_at": None,
                }
            )
        )
    return validated


def _collect_evidence_ids(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            if key == "evidence_chunk_ids" and isinstance(nested, list):
                found.update(str(item) for item in nested if item)
            else:
                found.update(_collect_evidence_ids(nested))
    elif isinstance(value, list):
        for nested in value:
            found.update(_collect_evidence_ids(nested))
    return found
