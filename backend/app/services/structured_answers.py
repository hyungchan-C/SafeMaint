from __future__ import annotations

from collections.abc import Iterable
import re

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
from app.services.document_types import (
    CHECKLIST_DOCUMENT_TYPES,
    MANUAL_DOCUMENT_TYPES,
    PUBLIC_REFERENCE_DOCUMENT_TYPES,
    canonical_document_type,
    is_manual_document_type,
)


MANUAL_SOURCE_TYPES = MANUAL_DOCUMENT_TYPES
PUBLIC_REFERENCE_SOURCE_TYPES = PUBLIC_REFERENCE_DOCUMENT_TYPES
MAINTENANCE_REFERENCE_SOURCE_TYPES = (
    PUBLIC_REFERENCE_SOURCE_TYPES | {"company_policy"}
)
MAX_SOURCE_ITEM_CHARS = 180

MANUAL_STEP_ACTION_PATTERN = re.compile(
    r"(?:확인|차단|잠금|표시|점검|검사|설치|분리|연결|정렬|고정|측정|"
    r"청소|교체|조정|기록|중지|준수|적용|verify|check|inspect|install|"
    r"remove|replace|lock|isolate|align|clean)",
    re.IGNORECASE,
)
RELEVANCE_STOPWORDS = frozenset(
    {
        "그거",
        "관련",
        "방법",
        "내용",
        "알려줘",
        "해야",
        "하려고",
        "예정",
        "어떻게",
        "무슨",
        "작업",
        "설비",
        "기계",
        "the",
        "and",
        "for",
        "with",
    }
)
CRITICAL_MAINTENANCE_TERMS = (
    "정지",
    "차단",
    "격리",
    "잠금",
    "재가동",
    "운전",
    "위험구역",
    "위험 지역",
    "방호",
    "비상정지",
    "인터락",
    "끼임",
    "협착",
    "감전",
    "추락",
    "낙하",
    "회전",
    "잔류",
    "압력",
    "lockout",
    "tagout",
    "interlock",
)
META_ITEM_PATTERNS = (
    "근거를 원문에서 확인",
    "문서 id",
    "문서 버전",
    ".pdf",
)


def is_manual_source(source: ChatSource) -> bool:
    return is_manual_document_type(source.source_type)


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
    *,
    question: str = "",
) -> StructuredAnswer | None:
    """Build a conservative structure from retrieved rows, never prose parsing."""

    if not sources:
        return None
    chunk_ids = [source.chunk_id for source in sources]
    first = sources[0]
    source_items = [_source_item(source) for source in sources[:8]]

    if answer_type == "document_qa":
        return DocumentAnswerDetails(
            overview=DocumentOverview(
                filename=first.original_filename or first.title,
                document_type=canonical_document_type(first.source_type),
                version=(
                    str(first.document_version)
                    if first.document_version is not None
                    else None
                ),
            ),
            main_contents=source_items,
            related_equipment=[],
            related_components=[],
            supported_tasks=[],
            evidence_chunk_ids=chunk_ids,
            unverified_information=[
                "검색된 부분에 없는 문서 메타데이터와 세부 내용은 확인하지 못했습니다."
            ],
        )

    if answer_type == "component_info":
        return ComponentAnswerDetails(
            one_line_description=(
                "검색된 원문 근거를 아래에서 확인할 수 있습니다. "
                "AI 설명을 생성하지 못해 원문을 그대로 제공합니다."
            ),
            main_roles=source_items[:5],
            usage_locations=[],
            precautions=[],
            evidence_chunk_ids=chunk_ids,
            additional_information_needed=["정확한 제조사와 모델명을 확인해 주세요."],
        )

    if answer_type == "maintenance_guide":
        public_sources = [
            source
            for source in sources
            if canonical_document_type(source.source_type)
            in MAINTENANCE_REFERENCE_SOURCE_TYPES
        ]
        public_references = [
            _public_reference_item(source) for source in public_sources[:5]
        ]
        return MaintenanceAnswerDetails(
            summary=MaintenanceSummary(
                status="근거 부족",
                risk_level="판단 불가",
                risk_basis=[],
                core_warning=(
                    "AI 작업 절차를 생성하지 못했습니다. 아래 검색 원문을 확인하고 "
                    "별도의 위험성평가와 안전관리자 검토를 진행해 주세요."
                ),
            ),
            pre_checks=[],
            hazards=[],
            manual_steps=[],
            stop_conditions=[],
            related_regulations_and_incidents=public_references,
            evidence_chunk_ids=chunk_ids,
            additional_information_needed=[
                "제조사 매뉴얼의 해당 모델 작업 절차와 현장 작업표준을 확인해 주세요."
            ],
        )
    return None


def source_based_checklist_items(
    answer_type: AnswerType,
    sources: list[ChatSource],
    *,
    question: str = "",
) -> list[ChatChecklistItem]:
    # A retrieval hit alone does not prove that a sentence is a valid TBM item.
    # Only a validated model response may create checklist content.
    return []


def validated_structured_answer(
    value: StructuredAnswer | None,
    *,
    expected_type: AnswerType,
    sources: list[ChatSource],
    question: str = "",
) -> StructuredAnswer | None:
    if value is None or value.answer_type != expected_type:
        return None
    allowed_ids = {source.chunk_id for source in sources}

    if isinstance(value, MaintenanceAnswerDetails):
        source_by_id = {source.chunk_id: source for source in sources}
        verified_risk_basis = _verified_items(value.summary.risk_basis, allowed_ids)
        verified_pre_checks = _verified_items(value.pre_checks, allowed_ids)
        verified_hazards = _verified_items(value.hazards, allowed_ids)
        verified_manual_steps: list[EvidenceBackedItem] = []
        for step in value.manual_steps:
            normalized_step = _verified_item(step, allowed_ids)
            if (
                normalized_step is not None
                and _looks_like_actionable_manual_step(normalized_step.content)
                and _item_relevant_to_question(normalized_step.content, question)
                and all(
                    chunk_id in source_by_id
                    and is_manual_source(source_by_id[chunk_id])
                    for chunk_id in normalized_step.evidence_chunk_ids
                )
            ):
                verified_manual_steps.append(normalized_step)
        verified_stop_conditions = _verified_items(value.stop_conditions, allowed_ids)
        verified_public_references: list[EvidenceBackedItem] = []
        for item in value.related_regulations_and_incidents:
            normalized_item = _verified_item(item, allowed_ids)
            if (
                normalized_item is not None
                and all(
                    canonical_document_type(source_by_id[chunk_id].source_type)
                    in MAINTENANCE_REFERENCE_SOURCE_TYPES
                    for chunk_id in normalized_item.evidence_chunk_ids
                )
            ):
                verified_public_references.append(normalized_item)
        value = value.model_copy(
            update={
                "summary": value.summary.model_copy(
                    update={
                        "risk_level": "판단 불가",
                        "risk_basis": verified_risk_basis,
                    }
                ),
                "pre_checks": verified_pre_checks,
                "hazards": verified_hazards,
                "manual_steps": verified_manual_steps,
                "stop_conditions": verified_stop_conditions,
                "related_regulations_and_incidents": verified_public_references,
                "evidence_chunk_ids": [
                    chunk_id
                    for chunk_id in value.evidence_chunk_ids
                    if chunk_id in allowed_ids
                ],
                "additional_information_needed": _verified_additional_information(
                    value.additional_information_needed,
                    question,
                ),
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
                ),
                "main_contents": _verified_items(value.main_contents, allowed_ids),
                "evidence_chunk_ids": [
                    chunk_id
                    for chunk_id in value.evidence_chunk_ids
                    if chunk_id in allowed_ids
                ],
            }
        )
    elif isinstance(value, ComponentAnswerDetails):
        component_allowed_ids = {
            source.chunk_id
            for source in sources
            if canonical_document_type(source.source_type)
            in {"component_manual", "equipment_manual", "public_guide"}
        }
        value = value.model_copy(
            update={
                "main_roles": _verified_items(
                    value.main_roles, component_allowed_ids
                ),
                "usage_locations": _verified_items(
                    value.usage_locations,
                    component_allowed_ids,
                ),
                "precautions": _verified_items(
                    value.precautions, component_allowed_ids
                ),
                "evidence_chunk_ids": [
                    chunk_id
                    for chunk_id in value.evidence_chunk_ids
                    if chunk_id in component_allowed_ids
                ],
            }
        )
    return value


def enriched_structured_answer(
    value: StructuredAnswer | None,
    fallback: StructuredAnswer | None,
    *,
    expected_type: AnswerType,
) -> StructuredAnswer | None:
    if fallback is not None and fallback.answer_type != expected_type:
        fallback = None
    if value is None or value.answer_type != expected_type:
        return fallback
    # Empty Qwen sections are meaningful. Do not repopulate them with rules.
    return value


def validated_checklist_items(
    items: Iterable[ChatChecklistItem],
    *,
    sources: list[ChatSource],
) -> list[ChatChecklistItem]:
    allowed_ids = {source.chunk_id for source in sources}
    checklist_ids = {
        source.chunk_id
        for source in sources
        if canonical_document_type(source.source_type)
        in CHECKLIST_DOCUMENT_TYPES
    }
    validated: list[ChatChecklistItem] = []
    for item in items:
        if not item.evidence_chunk_ids:
            continue
        if not set(item.evidence_chunk_ids).issubset(
            allowed_ids & checklist_ids
        ):
            continue
        content = _verified_content(item.content)
        if content is None:
            continue
        if _looks_like_meta_item(content):
            continue
        validated.append(
            item.model_copy(
                update={
                    "content": content,
                    "id": None,
                    "sequence": len(validated) + 1,
                    "is_completed": False,
                    "completed_by_user_id": None,
                    "completed_at": None,
                }
            )
        )
    return validated


def _verified_additional_information(
    items: Iterable[str],
    question: str,
) -> list[str]:
    normalized: list[str] = []
    question_text = " ".join(question.split()).casefold()
    for item in items:
        text = " ".join(str(item).split())
        if not text:
            continue
        if text.casefold() == question_text:
            continue
        if _looks_like_meta_item(text):
            continue
        if len(text) > 120:
            continue
        if text not in normalized:
            normalized.append(text)
    return normalized


def _source_item(source: ChatSource) -> EvidenceBackedItem:
    return EvidenceBackedItem(
        content=_concise_source_content(source),
        evidence_chunk_ids=[source.chunk_id],
    )


def _public_reference_item(source: ChatSource) -> EvidenceBackedItem:
    return EvidenceBackedItem(
        content=_concise_source_content(source),
        evidence_chunk_ids=[source.chunk_id],
    )


def _item_relevant_to_question(content: str, question: str) -> bool:
    if not question.strip():
        return True
    content_text = content.casefold()
    question_terms = _relevance_terms(question)
    content_terms = _relevance_terms(content)
    if question_terms and question_terms & content_terms:
        return True
    return _contains_any(content_text, CRITICAL_MAINTENANCE_TERMS)


def _relevance_terms(text: str) -> set[str]:
    terms: set[str] = set()
    for token in re.findall(r"[0-9A-Za-z가-힣_-]+", text.casefold()):
        token = token.strip("_-")
        if len(token) < 2 or token in RELEVANCE_STOPWORDS:
            continue
        terms.add(token)
    return terms


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword.casefold() in text for keyword in keywords)


def _clean_source_excerpt(text: str) -> str:
    text = " ".join(text.split())
    text = re.sub(r"【[^】]{1,30}】\s*", "", text)
    text = re.sub(r"\[자료유형\].*?\[내용\]\s*", "", text)
    text = re.sub(r"\[제목\]\s*", "", text)
    text = re.sub(r"\s*\|\s*", " ", text)
    text = re.sub(r"\bNo\.\s*점검\s*항목\s*확인\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\b점검\s*항목\s*확인\b", "", text)
    text = text.replace("○", " ")
    text = re.sub(r"^\(?[가-힣A-Za-z0-9]+\)?\s*[.)]\s*", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _concise_source_content(source: ChatSource) -> str:
    text = _clean_source_excerpt(source.excerpt)
    content = text or source.title
    if len(content) > MAX_SOURCE_ITEM_CHARS:
        content = content[: MAX_SOURCE_ITEM_CHARS - 3].rstrip() + "..."
    return content


def _verified_items(
    items: Iterable[EvidenceBackedItem],
    allowed_ids: set[str],
) -> list:
    verified: list[EvidenceBackedItem] = []
    for item in items:
        normalized_item = _verified_item(item, allowed_ids)
        if normalized_item is not None:
            verified.append(normalized_item)
    return verified


def _verified_item(
    item: EvidenceBackedItem,
    allowed_ids: set[str],
) -> EvidenceBackedItem | None:
    if not item.evidence_chunk_ids:
        return None
    if not set(item.evidence_chunk_ids).issubset(allowed_ids):
        return None
    content = _verified_content(item.content)
    if content is None:
        return None
    return item.model_copy(update={"content": content})


def _verified_content(content: str) -> str | None:
    cleaned = _clean_source_excerpt(content)
    if not _has_meaningful_text(cleaned):
        return None
    if _looks_like_meta_item(cleaned):
        return None
    return cleaned


def _looks_like_meta_item(content: str) -> bool:
    lowered = " ".join(content.split()).casefold()
    return any(pattern in lowered for pattern in META_ITEM_PATTERNS)


def _has_meaningful_text(content: str) -> bool:
    text = " ".join(content.split())
    if len(text) < 6:
        return False
    if re.fullmatch(r"[\d\s.\-()]+", text):
        return False
    if not re.search(r"[A-Za-z가-힣]", text):
        return False
    return True


def _looks_like_actionable_manual_step(content: str) -> bool:
    text = _verified_content(content) or ""
    if not text:
        return False
    return bool(MANUAL_STEP_ACTION_PATTERN.search(text))
