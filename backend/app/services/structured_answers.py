from __future__ import annotations

from collections.abc import Iterable
import re
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
    MaintenanceHazard,
    MaintenanceSummary,
    NoEvidenceDetails,
    StructuredAnswer,
)
from app.services.question_intent import DEFAULT_CLARIFICATION_QUESTION


MANUAL_SOURCE_TYPES = frozenset(
    {"manual", "equipment_manual", "component_manual", "work_standard"}
)
PUBLIC_REFERENCE_SOURCE_TYPES = frozenset(
    {"public_guide", "public_incident", "regulation", "incident"}
)
MAINTENANCE_SOURCE_TYPES = MANUAL_SOURCE_TYPES | PUBLIC_REFERENCE_SOURCE_TYPES
MAX_STRUCTURED_ITEM_CHARS = 220
MAX_SOURCE_ITEM_CHARS = 180

MANUAL_STEP_ACTION_PATTERN = re.compile(
    r"(?:확인|차단|잠금|표시|점검|검사|설치|분리|연결|정렬|고정|측정|"
    r"청소|교체|조정|기록|중지|준수|적용|verify|check|inspect|install|"
    r"remove|replace|lock|isolate|align|clean)",
    re.IGNORECASE,
)
DOCUMENT_LABEL_NOISE_PATTERN = re.compile(
    r"(?:사용자\s*)?(?:매뉴얼|설명서|문서|기술지침|안전작업\s*지침|"
    r"guide|manual|document|pdf)",
    re.IGNORECASE,
)
EQUIPMENT_ENTITY_PATTERN = re.compile(
    r"[A-Za-z0-9가-힣][A-Za-z0-9가-힣\s/_-]{0,28}"
    r"(?:장치|설비|기계|센서|컨트롤러|시스템|유닛|커튼)",
    re.IGNORECASE,
)
COMPONENT_ENTITY_PATTERN = re.compile(
    r"[A-Za-z0-9가-힣][A-Za-z0-9가-힣\s/_-]{0,24}"
    r"(?:컴포넌트|컨트롤러|스위치|센서|모듈|유닛|수광기|투광기|송신부|수신부|"
    r"공구|장비|볼트|클램프|로프|모터|밸브|펌프|툴|축)",
    re.IGNORECASE,
)
ACTION_KEYWORDS = (
    "설치",
    "점검",
    "검사",
    "배선",
    "청소",
    "세척",
    "정렬",
    "설정",
    "교체",
    "조정",
    "보수",
    "정비",
    "운반",
    "해체",
    "분리",
    "연결",
    "잠금",
    "차단",
    "install",
    "inspect",
    "check",
    "clean",
    "replace",
    "adjust",
    "isolate",
    "lock",
)
SAFETY_KEYWORDS = (
    "위험",
    "안전",
    "주의",
    "경고",
    "금지",
    "차단",
    "격리",
    "재가동",
    "방호",
    "보호",
    "인터락",
    "정지",
    "승인",
    "확인",
    "hazard",
    "warning",
    "caution",
    "lockout",
    "tagout",
    "interlock",
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
    *,
    question: str = "",
) -> StructuredAnswer | None:
    """Build a conservative structure from retrieved rows, never prose parsing."""

    if not sources:
        return None
    chunk_ids = [source.chunk_id for source in sources]
    first = sources[0]
    source_items = [_source_item(source) for source in sources[:5]]

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
            related_equipment=_document_related_equipment(sources),
            related_components=_document_related_components(sources),
            supported_tasks=_document_supported_tasks(sources),
            evidence_chunk_ids=chunk_ids,
            unverified_information=[
                "검색된 부분에 없는 문서 메타데이터와 세부 내용은 확인하지 못했습니다."
            ],
        )

    if answer_type == "component_info":
        return ComponentAnswerDetails(
            one_line_description=_component_description(sources),
            main_roles=_component_roles(sources) or source_items[:3],
            usage_locations=_component_usage_locations(sources),
            precautions=_component_precautions(sources)
            or _source_items_by_keywords(
                sources,
                ("주의", "위험", "안전", "금지", "확인", "보호", "차단"),
            )[:3],
            evidence_chunk_ids=chunk_ids,
            additional_information_needed=["정확한 제조사와 모델명을 확인해 주세요."],
        )

    if answer_type == "maintenance_guide":
        manual_sources = [source for source in sources if is_manual_source(source)]
        public_sources = [
            source
            for source in sources
            if source.source_type.casefold() in PUBLIC_REFERENCE_SOURCE_TYPES
        ]
        has_manual = bool(manual_sources)
        pre_checks = _maintenance_pre_checks(sources)
        hazards = _maintenance_hazards(sources)
        stop_conditions = _maintenance_stop_conditions(sources)
        public_references = [
            _public_reference_item(source) for source in public_sources[:5]
        ]
        manual_steps = _maintenance_manual_steps(
            manual_sources,
            question=question,
        ) or [
            EvidenceBackedItem(
                content=(
                    f"{source.section or source.title} 항목의 제조사 매뉴얼 원문을 확인합니다."
                ),
                evidence_chunk_ids=[source.chunk_id],
            )
            for source in manual_sources[:3]
            if _source_relevant_to_question(source, question)
        ]
        risk_basis = [
            item
            for item in [*hazards[:2], *public_references[:2], *pre_checks[:1]]
            if item.evidence_chunk_ids
        ][:3]
        has_work_references = bool(pre_checks or hazards or public_references or manual_steps)
        return MaintenanceAnswerDetails(
            summary=MaintenanceSummary(
                status=(
                    "안전관리자 확인 필요"
                    if has_manual or has_work_references
                    else "근거 부족"
                ),
                risk_level="판단 불가" if not risk_basis else "보통",
                risk_basis=risk_basis,
                core_warning=(
                    "승인된 매뉴얼 원문과 현장 상태를 대조한 뒤 작업해야 합니다."
                    if has_manual
                    else (
                        "제조사 매뉴얼 기반 세부 절차는 확인되지 않았습니다. "
                        "다만 검색된 공용 안전자료 기준으로 작업 전 확인사항과 위험요인을 먼저 확인하세요."
                    )
                ),
            ),
            pre_checks=pre_checks,
            hazards=hazards,
            manual_steps=manual_steps,
            stop_conditions=stop_conditions,
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
    if answer_type != "maintenance_guide" or not sources:
        return []
    candidates: list[EvidenceBackedItem] = []
    candidates.extend(_maintenance_pre_checks(sources))
    candidates.extend(_maintenance_stop_conditions(sources))
    items: list[ChatChecklistItem] = []
    seen: set[str] = set()
    for candidate in candidates:
        content = candidate.content
        if content in seen or not candidate.evidence_chunk_ids:
            continue
        seen.add(content)
        items.append(
            ChatChecklistItem(
                content=content,
                sequence=len(items) + 1,
                evidence_chunk_ids=candidate.evidence_chunk_ids,
            )
        )
        if len(items) >= 5:
            break
    return items


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
    referenced_ids = _collect_evidence_ids(value.model_dump(mode="python"))
    if not referenced_ids.issubset(allowed_ids):
        return None

    if isinstance(value, MaintenanceAnswerDetails):
        source_by_id = {source.chunk_id: source for source in sources}
        verified_risk_basis = _verified_items(value.summary.risk_basis, allowed_ids)
        risk_level = value.summary.risk_level
        if risk_level != "판단 불가" and not verified_risk_basis:
            risk_level = "판단 불가"
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
                    source_by_id[chunk_id].source_type.casefold()
                    in PUBLIC_REFERENCE_SOURCE_TYPES
                    for chunk_id in normalized_item.evidence_chunk_ids
                )
            ):
                verified_public_references.append(normalized_item)
        value = value.model_copy(
            update={
                "summary": value.summary.model_copy(
                    update={
                        "risk_level": risk_level,
                        "risk_basis": verified_risk_basis,
                    }
                ),
                "pre_checks": verified_pre_checks,
                "hazards": verified_hazards,
                "manual_steps": verified_manual_steps,
                "stop_conditions": verified_stop_conditions,
                "related_regulations_and_incidents": verified_public_references,
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
            }
        )
    elif isinstance(value, ComponentAnswerDetails):
        value = value.model_copy(
            update={
                "main_roles": _verified_items(value.main_roles, allowed_ids),
                "usage_locations": _verified_items(
                    value.usage_locations,
                    allowed_ids,
                ),
                "precautions": _verified_items(value.precautions, allowed_ids),
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
    if fallback is None:
        return value

    if isinstance(value, DocumentAnswerDetails) and isinstance(
        fallback,
        DocumentAnswerDetails,
    ):
        updates: dict[str, Any] = {}
        for field in (
            "main_contents",
            "related_equipment",
            "related_components",
            "supported_tasks",
            "evidence_chunk_ids",
            "unverified_information",
        ):
            if not getattr(value, field) and getattr(fallback, field):
                updates[field] = getattr(fallback, field)
        if not value.overview.filename and fallback.overview.filename:
            updates["overview"] = fallback.overview
        return value.model_copy(update=updates) if updates else value

    if isinstance(value, ComponentAnswerDetails) and isinstance(
        fallback,
        ComponentAnswerDetails,
    ):
        updates = {}
        empty_detail_sections = not any(
            (value.main_roles, value.usage_locations, value.precautions)
        )
        if empty_detail_sections and fallback.one_line_description:
            updates["one_line_description"] = fallback.one_line_description
        for field in (
            "main_roles",
            "usage_locations",
            "precautions",
            "evidence_chunk_ids",
            "additional_information_needed",
        ):
            if not getattr(value, field) and getattr(fallback, field):
                updates[field] = getattr(fallback, field)
        return value.model_copy(update=updates) if updates else value

    if isinstance(value, MaintenanceAnswerDetails) and isinstance(
        fallback,
        MaintenanceAnswerDetails,
    ):
        summary_updates: dict[str, Any] = {}
        if value.summary.status == "근거 부족" and fallback.summary.status != "근거 부족":
            summary_updates["status"] = fallback.summary.status
        if (
            value.summary.risk_level == "판단 불가"
            and fallback.summary.risk_level != "판단 불가"
            and fallback.summary.risk_basis
        ):
            summary_updates["risk_level"] = fallback.summary.risk_level
        if not value.summary.risk_basis and fallback.summary.risk_basis:
            summary_updates["risk_basis"] = fallback.summary.risk_basis
        if not value.summary.core_warning.strip() and fallback.summary.core_warning:
            summary_updates["core_warning"] = fallback.summary.core_warning

        updates = {
            "summary": value.summary.model_copy(update=summary_updates)
            if summary_updates
            else value.summary
        }
        for field in (
            "pre_checks",
            "hazards",
            "manual_steps",
            "stop_conditions",
            "related_regulations_and_incidents",
            "evidence_chunk_ids",
            "additional_information_needed",
        ):
            if not getattr(value, field) and getattr(fallback, field):
                updates[field] = getattr(fallback, field)
        return value.model_copy(update=updates)

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


def _source_item(source: ChatSource) -> EvidenceBackedItem:
    return EvidenceBackedItem(
        content=_concise_source_content(source),
        evidence_chunk_ids=[source.chunk_id],
    )


def _public_reference_item(source: ChatSource) -> EvidenceBackedItem:
    content = (
        _summarize_evidence_item(source.excerpt)
        or _best_sentence_summary(_source_display_text(source))
        or _source_reference_label(source)
    )
    return EvidenceBackedItem(
        content=content,
        evidence_chunk_ids=[source.chunk_id],
    )


def _source_items_by_keywords(
    sources: list[ChatSource],
    keywords: tuple[str, ...],
) -> list[EvidenceBackedItem]:
    items: list[EvidenceBackedItem] = []
    for source in sources:
        text = _source_text(source)
        if any(keyword.casefold() in text for keyword in keywords):
            items.append(_source_item(source))
    return items


def _document_related_equipment(sources: list[ChatSource]) -> list[str]:
    labels: list[str] = []
    for source in sources:
        _extend_labels(labels, _source_title_labels(source))
        _extend_labels(
            labels,
            _extract_entity_labels(_source_display_text(source), EQUIPMENT_ENTITY_PATTERN),
        )
    return labels[:10]


def _document_related_components(sources: list[ChatSource]) -> list[str]:
    labels: list[str] = []
    for source in sources:
        _extend_labels(
            labels,
            _extract_entity_labels(_source_display_text(source), COMPONENT_ENTITY_PATTERN),
        )
    return labels[:10]


def _document_supported_tasks(sources: list[ChatSource]) -> list[str]:
    labels: list[str] = []
    for source in sources:
        text = _source_text(source)
        for keywords, label in (
            (("설치", "install"), "설치"),
            (("배선", "wiring"), "배선 확인"),
            (("점검", "검사", "inspect", "check"), "점검"),
            (("청소", "세척", "clean"), "청소"),
            (("정렬", "광축", "align"), "광축 정렬 확인"),
            (("설정", "pc 설정", "configuration"), "기능 설정 확인"),
            (("모델 구성", "모델명", "model"), "모델 구성 확인"),
            (("교체", "replace"), "교체"),
        ):
            _add_label_if_present(labels, text, keywords, label)
    return labels[:10]


def _component_description(sources: list[ChatSource]) -> str:
    combined = " ".join(_source_text(source) for source in sources)
    subject = _best_subject_label(sources)
    if _contains_any(combined, ("검출", "감지", "detect", "monitor")):
        if _contains_any(combined, ("위험", "안전", "보호", "방호", "interlock")):
            return f"{subject}은 검색 근거에서 검출·감지 기능과 안전 보호 기능이 확인되는 장치입니다."
        return f"{subject}은 검색 근거에서 검출·감지 기능이 확인되는 장치입니다."
    if _contains_any(combined, ("센서", "sensor", "검출", "감지")):
        return f"{subject}은 검색 근거에서 확인되는 검출·감지 기능을 수행하는 부품입니다."
    return _concise_source_content(sources[0])


def _component_roles(sources: list[ChatSource]) -> list[EvidenceBackedItem]:
    items: list[EvidenceBackedItem] = []
    for source in sources:
        text = _source_text(source)
        if _contains_any(text, ("광축", "검출", "감지", "detect")):
            items.append(
                EvidenceBackedItem(
                    content="광축 차단이나 작업자 접근 여부를 검출합니다.",
                    evidence_chunk_ids=[source.chunk_id],
                )
            )
        if _contains_any(text, ("인터락", "재기동", "기계", "정지")):
            items.append(
                EvidenceBackedItem(
                    content="인터락 상태와 기계 재기동 조건을 관리하는 안전 기능과 연동됩니다.",
                    evidence_chunk_ids=[source.chunk_id],
                )
            )
        if _contains_any(text, ("pc 설정", "설정", "변경", "configuration")):
            items.append(
                EvidenceBackedItem(
                    content="설정 변경 후 장치가 의도한 대로 동작하는지 확인해야 하는 안전 기능을 제공합니다.",
                    evidence_chunk_ids=[source.chunk_id],
                )
            )
        if _contains_any(text, ("안전", "보호", "방호", "인사사고")):
            items.append(
                EvidenceBackedItem(
                    content="위험구역 접근으로 인한 인사사고를 예방하는 보호 기능을 담당합니다.",
                    evidence_chunk_ids=[source.chunk_id],
                )
            )
    return _dedupe_evidence_items(items)[:5]


def _component_usage_locations(sources: list[ChatSource]) -> list[EvidenceBackedItem]:
    items: list[EvidenceBackedItem] = []
    for source in sources:
        text = _source_text(source)
        if _contains_any(text, ("위험구역", "위험 구역", "기계", "machine")):
            items.append(
                EvidenceBackedItem(
                    content="기계 위험구역 또는 위험원 주변에 적용됩니다.",
                    evidence_chunk_ids=[source.chunk_id],
                )
            )
        if _contains_any(text, ("자동화", "운전", "작동", "machine", "equipment")):
            items.append(
                EvidenceBackedItem(
                    content="자동 운전 또는 기계 작동이 있는 작업 구역에 적용될 수 있습니다.",
                    evidence_chunk_ids=[source.chunk_id],
                )
            )
    return _dedupe_evidence_items(items)[:5]


def _component_precautions(sources: list[ChatSource]) -> list[EvidenceBackedItem]:
    items: list[EvidenceBackedItem] = []
    for source in sources:
        text = _source_text(source)
        if _contains_any(text, ("의도한 대로", "동작하는지", "동작 확인")):
            items.append(
                EvidenceBackedItem(
                    content="기능 설정 또는 변경 후 의도한 대로 동작하는지 확인합니다.",
                    evidence_chunk_ids=[source.chunk_id],
                )
            )
        if _contains_any(text, ("인사사고", "위험", "안전")):
            items.append(
                EvidenceBackedItem(
                    content="설정 오류나 임의 변경은 인사사고 위험으로 이어질 수 있으므로 검증 후 사용합니다.",
                    evidence_chunk_ids=[source.chunk_id],
                )
            )
        if _contains_any(text, ("위험 구역", "위험구역", "재기동", "인터락")):
            items.append(
                EvidenceBackedItem(
                    content="인터락 해제나 재기동 전 위험구역 내 작업자 유무를 확인합니다.",
                    evidence_chunk_ids=[source.chunk_id],
                )
            )
        if _contains_any(text, ("규격", "규제", "법률", "법령")):
            items.append(
                EvidenceBackedItem(
                    content="해당 국가·지역의 규격, 규제, 법률을 확인합니다.",
                    evidence_chunk_ids=[source.chunk_id],
                )
            )
    return _dedupe_evidence_items(items)[:5]


def _maintenance_pre_checks(sources: list[ChatSource]) -> list[EvidenceBackedItem]:
    candidates: list[EvidenceBackedItem] = []
    for source in sources:
        text = _source_text(source)
        if _contains_any(
            text,
            ("운전정지", "운전 정지", "정지 미실시", "운전중", "운전 중"),
        ) or _contains_keyword_groups(text, (("운전",), ("정지",))):
            candidates.append(
                EvidenceBackedItem(
                    content="작업 전 설비 운전정지 및 불시 기동 방지 상태를 확인합니다.",
                    evidence_chunk_ids=[source.chunk_id],
                )
            )
        if _contains_any(text, ("lockout", "tagout", "loto", "격리", "전원", "차단", "재가동")):
            candidates.append(
                EvidenceBackedItem(
                    content="작업 전 전원 차단·격리 및 재가동 방지 조치를 확인합니다.",
                    evidence_chunk_ids=[source.chunk_id],
                )
            )
        if _contains_any(text, ("키를 제거", "키 제거", "시건", "잠금", "표지판", "경고 라벨")):
            candidates.append(
                EvidenceBackedItem(
                    content="키 제거, 잠금·시건, 표지판 부착 등 재가동 방지 조치를 확인합니다.",
                    evidence_chunk_ids=[source.chunk_id],
                )
            )
        if _contains_any(text, ("비상정지", "비상 정지")):
            candidates.append(
                EvidenceBackedItem(
                    content="비상정지장치의 위치와 작동 상태를 확인합니다.",
                    evidence_chunk_ids=[source.chunk_id],
                )
            )
        if _contains_any(text, ("방호울", "방호", "가드", "덮개", "안전문", "안전장치")):
            candidates.append(
                EvidenceBackedItem(
                    content="방호장치·안전문·가드 등 접근 통제 장치의 상태를 확인합니다.",
                    evidence_chunk_ids=[source.chunk_id],
                )
            )
        if _contains_any(text, ("잔류", "회전", "정지시간", "압력", "퍼지", "가압")):
            candidates.append(
                EvidenceBackedItem(
                    content="잔류 에너지, 회전부 정지, 압력 해소 상태를 확인합니다.",
                    evidence_chunk_ids=[source.chunk_id],
                )
            )
        if _contains_any(text, ("위험 지역", "위험구역", "접근", "작동 지역")):
            candidates.append(
                EvidenceBackedItem(
                    content="작업 중 위험구역 접근 통제와 설비 정지 상태를 확인합니다.",
                    evidence_chunk_ids=[source.chunk_id],
                )
            )
        if _contains_any(text, ("pc 설정", "설정 툴", "의도한 대로", "동작하는지")):
            candidates.append(
                EvidenceBackedItem(
                    content="기능 설정 또는 변경 후 장치가 의도한 대로 동작하는지 확인합니다.",
                    evidence_chunk_ids=[source.chunk_id],
                )
            )
        if _contains_any(text, ("구성", "광축 수", "직렬 확장", "수광기", "재 전송", "재전송")):
            candidates.append(
                EvidenceBackedItem(
                    content="구성 변경이나 부품 교체가 있었다면 설정 정보 재전송·재설정 필요 여부를 확인합니다.",
                    evidence_chunk_ids=[source.chunk_id],
                )
            )
        if _contains_any(text, ("미끄럼", "청소", "세척", "작업대", "통로")):
            candidates.append(
                EvidenceBackedItem(
                    content="작업 구역 주변 정리·정돈, 미끄럼 방지, 작업대·통로 상태를 확인합니다.",
                    evidence_chunk_ids=[source.chunk_id],
                )
            )
        if _contains_any(text, ("공구", "장비", "볼트", "클램프", "금형", "다이")):
            candidates.append(
                EvidenceBackedItem(
                    content="설비 내부와 주변에 남은 공구·장비가 없는지 확인합니다.",
                    evidence_chunk_ids=[source.chunk_id],
                )
            )
    if not candidates:
        candidates = [
            EvidenceBackedItem(
                content="검색된 안전자료의 해당 항목을 원문으로 확인합니다.",
                evidence_chunk_ids=[source.chunk_id],
            )
            for source in sources[:2]
            if source.source_type.casefold() in MAINTENANCE_SOURCE_TYPES
        ]
    return _dedupe_evidence_items(candidates)[:5]


def _maintenance_hazards(sources: list[ChatSource]) -> list[MaintenanceHazard]:
    candidates: list[MaintenanceHazard] = []
    for source in sources:
        text = _source_text(source)
        if _contains_any(text, ("끼임", "협착", "말려", "말림", "압착")):
            candidates.append(
                MaintenanceHazard(
                    name="끼임·협착",
                    content="작업자가 설비 작동부나 움직이는 부품 사이에 끼이거나 협착될 수 있습니다.",
                    evidence_chunk_ids=[source.chunk_id],
                )
            )
        if _contains_any(
            text,
            ("불시", "기동", "재가동", "운전중", "운전 중", "정지 미실시"),
        ) or _contains_keyword_groups(text, (("운전",), ("정지",))):
            candidates.append(
                MaintenanceHazard(
                    name="불시 기동",
                    content="운전정지나 재가동 방지 조치가 불완전하면 설비가 예기치 않게 움직일 수 있습니다.",
                    evidence_chunk_ids=[source.chunk_id],
                )
            )
        if _contains_any(text, ("감전", "전기", "전원", "전압")):
            candidates.append(
                MaintenanceHazard(
                    name="감전",
                    content="전원 차단·격리 상태가 불완전하면 감전 위험이 발생할 수 있습니다.",
                    evidence_chunk_ids=[source.chunk_id],
                )
            )
        if _contains_any(text, ("회전", "롤", "벨트", "체인", "축", "풀리")):
            candidates.append(
                MaintenanceHazard(
                    name="회전체 접촉",
                    content="회전부나 이송부가 완전히 정지하지 않으면 접촉·말림 위험이 있습니다.",
                    evidence_chunk_ids=[source.chunk_id],
                )
            )
        if _contains_any(text, ("압력", "가압", "퍼지", "질소", "공기")):
            candidates.append(
                MaintenanceHazard(
                    name="잔류 압력",
                    content="배관·공압·유압 계통의 잔류 압력이나 저장 에너지가 남아 있을 수 있습니다.",
                    evidence_chunk_ids=[source.chunk_id],
                )
            )
        if _contains_any(text, ("위험 지역", "위험구역", "작동 지역", "프레스", "금형", "다이")):
            candidates.append(
                MaintenanceHazard(
                    name="위험구역 노출",
                    content="작업자가 설비 작동부나 위험구역에 노출될 수 있습니다.",
                    evidence_chunk_ids=[source.chunk_id],
                )
            )
        if _contains_any(text, ("의도한 대로", "설정되지", "설정 오류", "인사사고")):
            candidates.append(
                MaintenanceHazard(
                    name="설정 오류",
                    content="기능 설정이 의도한 대로 적용되지 않으면 보호 기능 저하와 인사사고 위험이 있습니다.",
                    evidence_chunk_ids=[source.chunk_id],
                )
            )
        if _contains_any(text, ("미끄럼", "통로", "작업대")):
            candidates.append(
                MaintenanceHazard(
                    name="미끄럼·추락",
                    content="작업 구역 주변 바닥, 통로, 작업대 상태가 불량하면 미끄럼이나 추락 위험이 커질 수 있습니다.",
                    evidence_chunk_ids=[source.chunk_id],
                )
            )
        if _contains_any(text, ("고압", "샤워", "분사")):
            candidates.append(
                MaintenanceHazard(
                    name="고압 세척 접근",
                    content="고압 세척 또는 분사 장치 접근 시 작업자가 위험원에 노출될 수 있습니다.",
                    evidence_chunk_ids=[source.chunk_id],
                )
            )
        if _contains_any(text, ("소음", "dB", "db(a)")):
            candidates.append(
                MaintenanceHazard(
                    name="소음 노출",
                    content="설비 주변 작업 중 소음 노출 위험이 확인되므로 노출 구역과 시간을 관리해야 합니다.",
                    evidence_chunk_ids=[source.chunk_id],
                )
            )
    if not candidates:
        candidates = [
            MaintenanceHazard(
                name="작업 위험요인 확인 필요",
                content="검색된 안전자료와 현장 상태를 대조해 해당 작업의 위험요인을 확인해야 합니다.",
                evidence_chunk_ids=[source.chunk_id],
            )
            for source in sources[:1]
            if source.source_type.casefold() in MAINTENANCE_SOURCE_TYPES
        ]
    return _dedupe_hazards(candidates)[:3]


def _maintenance_stop_conditions(sources: list[ChatSource]) -> list[EvidenceBackedItem]:
    candidates: list[EvidenceBackedItem] = []
    for source in sources:
        text = _source_text(source)
        if _contains_any(
            text,
            ("운전정지", "운전 정지", "정지 미실시", "운전중", "운전 중"),
        ) or _contains_keyword_groups(text, (("운전",), ("정지",))):
            candidates.append(
                EvidenceBackedItem(
                    content="설비 운전정지 상태를 확인할 수 없으면 작업을 중지합니다.",
                    evidence_chunk_ids=[source.chunk_id],
                )
            )
        if _contains_any(text, ("전원", "차단", "lockout", "tagout", "loto", "재가동")):
            candidates.append(
                EvidenceBackedItem(
                    content="전원 차단·격리 또는 재가동 방지 상태를 확인할 수 없으면 작업을 중지합니다.",
                    evidence_chunk_ids=[source.chunk_id],
                )
            )
        if _contains_any(text, ("키를 제거", "키 제거", "시건", "잠금", "표지판", "경고 라벨")):
            candidates.append(
                EvidenceBackedItem(
                    content="키 제거, 잠금·시건, 표지판 부착 상태를 확인할 수 없으면 작업을 중지합니다.",
                    evidence_chunk_ids=[source.chunk_id],
                )
            )
        if _contains_any(text, ("비상정지", "비상 정지")):
            candidates.append(
                EvidenceBackedItem(
                    content="비상정지장치 위치 또는 작동 상태를 확인할 수 없으면 작업을 중지합니다.",
                    evidence_chunk_ids=[source.chunk_id],
                )
            )
        if _contains_any(text, ("방호울", "방호", "가드", "덮개", "안전문", "안전장치")):
            candidates.append(
                EvidenceBackedItem(
                    content="방호장치·안전문·가드 등 접근 통제 상태를 확인할 수 없으면 작업을 중지합니다.",
                    evidence_chunk_ids=[source.chunk_id],
                )
            )
        if _contains_any(text, ("잔류", "회전", "정지시간", "압력", "퍼지", "가압")):
            candidates.append(
                EvidenceBackedItem(
                    content="잔류 에너지, 회전부 정지, 압력 해소 상태를 확인할 수 없으면 작업을 중지합니다.",
                    evidence_chunk_ids=[source.chunk_id],
                )
            )
        if _contains_any(text, ("의도한 대로", "동작하는지", "설정되지")):
            candidates.append(
                EvidenceBackedItem(
                    content="기능 설정 후 정상 동작 여부를 확인할 수 없으면 작업을 중지합니다.",
                    evidence_chunk_ids=[source.chunk_id],
                )
            )
        if _contains_any(text, ("위험 지역", "위험구역", "작동 지역")):
            candidates.append(
                EvidenceBackedItem(
                    content="작업자가 위험구역에 들어가지 못하도록 통제할 수 없으면 작업을 중지합니다.",
                    evidence_chunk_ids=[source.chunk_id],
                )
            )
        if _contains_any(text, ("고압", "샤워", "분사")):
            candidates.append(
                EvidenceBackedItem(
                    content="고압 세척·분사 장치 접근 통제가 불가능하면 작업을 중지합니다.",
                    evidence_chunk_ids=[source.chunk_id],
                )
            )
    return _dedupe_evidence_items(candidates)[:5]


def _maintenance_manual_steps(
    sources: list[ChatSource],
    *,
    question: str = "",
) -> list[EvidenceBackedItem]:
    candidates: list[EvidenceBackedItem] = []
    for source in sources:
        if not _source_relevant_to_question(source, question):
            continue
        text = _source_text(source)
        if _contains_any(text, ("pc 설정", "설정 툴", "기능을 설정", "기능을 다시 설정")):
            candidates.append(
                EvidenceBackedItem(
                    content="PC 설정 툴로 기능 설정 또는 변경 후 정상 동작 여부를 확인합니다.",
                    evidence_chunk_ids=[source.chunk_id],
                )
            )
        if _contains_any(text, ("구성", "광축 수", "직렬 확장", "교체")):
            candidates.append(
                EvidenceBackedItem(
                    content="구성 변경 또는 부품 교체가 있었다면 제조사 기준에 따라 기능을 다시 설정합니다.",
                    evidence_chunk_ids=[source.chunk_id],
                )
            )
        if _contains_any(text, ("수광기", "재 전송", "재전송")):
            candidates.append(
                EvidenceBackedItem(
                    content="수광기 등 관련 부품 교체 후 필요한 설정 정보를 교체 부품에 재전송합니다.",
                    evidence_chunk_ids=[source.chunk_id],
                )
            )
        if _contains_any(text, ("인터락", "재기동", "위험 구역", "위험구역")):
            candidates.append(
                EvidenceBackedItem(
                    content="인터락 해제 또는 재기동 전 위험구역 내 작업자 유무를 확인합니다.",
                    evidence_chunk_ids=[source.chunk_id],
                )
            )
    return _dedupe_evidence_items(candidates)[:5]


def _best_subject_label(sources: list[ChatSource]) -> str:
    labels = _document_related_equipment(sources) or _document_related_components(sources)
    return labels[0] if labels else "해당 장치"


def _source_display_text(source: ChatSource) -> str:
    return " ".join(
        str(value)
        for value in (
            source.title,
            source.source_type,
            source.section,
            source.original_filename,
            source.excerpt,
        )
        if value
    )


def _source_text(source: ChatSource) -> str:
    return _source_display_text(source).casefold()


def _source_relevant_to_question(source: ChatSource, question: str) -> bool:
    if not question.strip():
        return True
    return _item_relevant_to_question(_source_display_text(source), question)


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


def _contains_keyword_groups(text: str, groups: tuple[tuple[str, ...], ...]) -> bool:
    return all(_contains_any(text, group) for group in groups)


def _add_label_if_present(
    labels: list[str],
    text: str,
    keywords: tuple[str, ...],
    label: str,
) -> None:
    if label not in labels and _contains_any(text, keywords):
        labels.append(label)


def _extend_labels(labels: list[str], candidates: Iterable[str]) -> None:
    for candidate in candidates:
        label = _clean_entity_label(candidate)
        if label and label.casefold() not in {item.casefold() for item in labels}:
            labels.append(label)


def _source_title_labels(source: ChatSource) -> list[str]:
    candidates: list[str] = []
    for value in (source.title, source.original_filename):
        if not value:
            continue
        text = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", str(value))
        text = re.sub(r"[_-]+", " ", text).strip()
        label = _clean_entity_label(DOCUMENT_LABEL_NOISE_PATTERN.sub("", text))
        if label and not _looks_like_model_code(label):
            candidates.append(label)
    return candidates


def _extract_entity_labels(text: str, pattern: re.Pattern[str]) -> list[str]:
    candidates: list[str] = []
    for match in pattern.finditer(text):
        label = _clean_entity_label(match.group(0))
        if label and not _looks_like_model_code(label):
            candidates.append(label)
    return candidates


def _clean_entity_label(value: str) -> str | None:
    text = " ".join(value.replace("_", " ").split())
    text = re.sub(r"\([^)]*\)", "", text)
    text = re.split(r"(?:이며|이고|이고\s|,|;)", text)[0]
    text = re.sub(r"^.*(?:은|는|이|가|및)\s+", "", text)
    text = DOCUMENT_LABEL_NOISE_PATTERN.sub("", text)
    text = re.sub(r"^[\s\-·:()\[\]0-9.]+|[\s\-·:()\[\]0-9.]+$", "", text)
    text = re.sub(r"^(?:및|또는|이|그|해당|검색된)\s+", "", text)
    text = text.strip(" -·:/()[]")
    if not (2 <= len(text) <= 36):
        return None
    if text.casefold() in {"verified source", "equipment_manual", "component_manual"}:
        return None
    if not re.search(r"[A-Za-z가-힣]", text):
        return None
    return text


def _looks_like_model_code(value: str) -> bool:
    compact = re.sub(r"\s+", "", value)
    if len(compact) < 8:
        return False
    alpha_num = sum(char.isascii() and char.isalnum() for char in compact)
    separators = sum(char in "-_." for char in compact)
    return alpha_num >= max(6, len(compact) // 2) and separators >= 1


def _source_reference_label(source: ChatSource) -> str:
    label = _clean_entity_label(source.section or "") or _clean_entity_label(
        source.title or ""
    )
    if label:
        return label
    return "검색된 공용 안전자료"


def _clean_source_excerpt(text: str) -> str:
    text = " ".join(text.split())
    text = re.sub(r"\[자료유형\].*?\[내용\]\s*", "", text)
    text = re.sub(r"\[제목\]\s*", "", text)
    text = re.sub(r"\s*\|\s*", " ", text)
    text = re.sub(r"\bNo\.\s*점검\s*항목\s*확인\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\b점검\s*항목\s*확인\b", "", text)
    text = text.replace("○", " ")
    text = re.sub(r"^\(?[가-힣A-Za-z0-9]+\)?\s*[.)]\s*", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _source_sentences(text: str) -> list[str]:
    cleaned = _clean_source_excerpt(text)
    pieces = re.split(r"(?<=[.!?。])\s+|(?<=다\.)\s+", cleaned)
    return [
        piece.strip()
        for piece in pieces
        if _looks_like_meaningful_item(piece.strip())
    ]


def _dedupe_evidence_items(
    items: Iterable[EvidenceBackedItem],
) -> list[EvidenceBackedItem]:
    deduped: list[EvidenceBackedItem] = []
    seen: set[str] = set()
    for item in items:
        key = item.content
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _dedupe_hazards(
    items: Iterable[MaintenanceHazard],
) -> list[MaintenanceHazard]:
    deduped: list[MaintenanceHazard] = []
    seen: set[str] = set()
    for item in items:
        key = item.name
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _concise_source_content(source: ChatSource) -> str:
    text = _clean_source_excerpt(source.excerpt)
    sentences = _source_sentences(text)
    content = sentences[0] if sentences else text or source.title
    if _looks_like_raw_or_meta_item(content):
        content = _summarize_evidence_item(text) or content
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
    if _looks_like_raw_or_meta_item(cleaned):
        summarized = _summarize_evidence_item(cleaned)
        if summarized and _looks_like_meaningful_item(summarized):
            return summarized
        return None
    if _looks_like_meaningful_item(cleaned):
        return cleaned
    summarized = _summarize_evidence_item(cleaned)
    if summarized and _looks_like_meaningful_item(summarized):
        return summarized
    return None


def _looks_like_raw_or_meta_item(content: str) -> bool:
    text = " ".join(content.split())
    lowered = text.casefold()
    if _looks_like_meta_item(text):
        return True
    if any(marker in text for marker in ("【", "】", "|")):
        return True
    if re.search(r"(?:\([0-9가-힣]\)|[0-9]+\s+[가-힣A-Za-z])", text) and len(text) > 90:
        return True
    if re.match(r"^(?:및|또는|이|그|해당|검색된|있어|되어|하여야|시)\s+", text):
        return True
    if "점검 항목" in text or "자료유형" in text:
        return True
    if lowered.endswith("근거를 원문에서 확인했다"):
        return True
    return False


def _looks_like_meta_item(content: str) -> bool:
    lowered = " ".join(content.split()).casefold()
    return any(pattern in lowered for pattern in META_ITEM_PATTERNS)


def _summarize_evidence_item(content: str) -> str | None:
    text = _clean_source_excerpt(content)
    lowered = text.casefold()
    rule_summaries: list[str] = []
    summary_rules: tuple[tuple[bool, str], ...] = (
        (
            _contains_any(
                lowered,
                ("운전정지", "운전 정지", "정지 미실시", "운전중", "운전 중"),
            )
            or _contains_keyword_groups(lowered, (("운전",), ("정지",))),
            "작업 전 설비 운전정지 및 불시 기동 방지 상태를 확인합니다.",
        ),
        (
            _contains_any(lowered, ("lockout", "tagout", "loto"))
            or _contains_keyword_groups(
                lowered,
                (("전원", "동력", "에너지"), ("차단", "격리", "잠금", "재가동")),
            ),
            "작업 전 전원 차단·격리 및 재가동 방지 상태를 확인합니다.",
        ),
        (
            _contains_any(
                lowered,
                ("인터락", "재기동", "위험구역", "위험 구역", "작동 지역"),
            ),
            "인터락 해제 또는 재기동 전 위험구역 내 작업자 유무를 확인합니다.",
        ),
        (
            _contains_any(lowered, ("비상정지", "비상 정지")),
            "비상정지장치의 위치와 작동 상태를 확인합니다.",
        ),
        (
            _contains_any(lowered, ("방호울", "방호", "가드", "덮개", "안전문", "안전장치")),
            "방호장치·안전문·가드 등 접근 통제 장치의 상태를 확인합니다.",
        ),
        (
            _contains_any(lowered, ("잔류", "회전", "정지시간", "압력", "퍼지", "가압")),
            "잔류 에너지, 회전부 정지, 압력 해소 상태를 확인합니다.",
        ),
        (
            _contains_any(lowered, ("pc 설정", "설정 툴", "재설정", "의도한 대로"))
            or _contains_keyword_groups(
                lowered,
                (("설정",), ("변경", "동작", "확인", "전송", "재전송")),
            ),
            "기능 설정 또는 변경 후 정상 동작 여부를 확인합니다.",
        ),
        (
            _contains_any(lowered, ("재 전송", "재전송"))
            or _contains_keyword_groups(
                lowered,
                (("구성", "광축 수", "직렬 확장", "수광기", "투광기"), ("변경", "교체", "설정")),
            ),
            "구성 변경이나 부품 교체 후 필요한 설정·재전송 상태를 확인합니다.",
        ),
        (
            _contains_any(lowered, ("규격", "규제", "법률", "법령", "kosha", "산업안전")),
            "해당 작업에 적용되는 규격·규제·법률 기준을 확인합니다.",
        ),
        (
            _contains_any(lowered, ("미끄럼", "통로", "작업대"))
            or _contains_keyword_groups(lowered, (("청소", "세척", "정리"), ("상태", "확인", "조치"))),
            "작업 구역 정리·정돈, 미끄럼 방지, 작업대·통로 상태를 확인합니다.",
        ),
        (
            _contains_any(lowered, ("공구", "장비", "볼트", "클램프")),
            "설비와 작업 구역에 남은 공구·장비가 없는지 확인합니다.",
        ),
        (
            _contains_any(lowered, ("고압", "샤워", "분사")),
            "고압 세척·분사 장치 접근 통제 상태를 확인합니다.",
        ),
        (
            _contains_any(lowered, ("소음", "db", "db(a)")),
            "소음 노출 구역과 작업 시간을 확인합니다.",
        ),
    )
    for matched, summary in summary_rules:
        if matched and summary not in rule_summaries:
            rule_summaries.append(summary)
        if len(rule_summaries) >= 2:
            return " ".join(rule_summaries)

    if rule_summaries:
        return rule_summaries[0]

    action_summary = _action_condition_summary(lowered)
    if action_summary:
        return action_summary

    return _best_sentence_summary(text)


def _action_condition_summary(text: str) -> str | None:
    action_labels: list[str] = []
    for keyword in ACTION_KEYWORDS:
        if keyword.casefold() in text and keyword not in action_labels:
            action_labels.append(keyword)
        if len(action_labels) >= 3:
            break
    if not action_labels:
        return None
    if _contains_any(text, ("조건", "상태", "기준", "확인", "고려", "준수")):
        return f"작업 전 {'·'.join(action_labels)} 관련 조건과 현장 상태를 확인합니다."
    return f"{'·'.join(action_labels)} 작업 관련 제조사 기준을 확인합니다."


def _best_sentence_summary(text: str) -> str | None:
    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?。])\s+|(?<=다\.)\s+", text)
        if _has_meaningful_text(sentence.strip())
    ]
    if not sentences:
        return None

    def score(sentence: str) -> tuple[float, int]:
        lowered = sentence.casefold()
        signal_hits = sum(1 for term in SAFETY_KEYWORDS if term.casefold() in lowered)
        action_hits = sum(1 for term in ACTION_KEYWORDS if term.casefold() in lowered)
        length_penalty = 1 if len(sentence) > MAX_STRUCTURED_ITEM_CHARS else 0
        return (signal_hits * 2 + action_hits - length_penalty, -len(sentence))

    selected = max(sentences, key=score)
    if len(selected) > MAX_STRUCTURED_ITEM_CHARS:
        selected = selected[: MAX_STRUCTURED_ITEM_CHARS - 3].rstrip() + "..."
    return selected if _has_meaningful_text(selected) else None


def _looks_like_meaningful_item(content: str) -> bool:
    text = " ".join(content.split())
    if not _has_meaningful_text(text):
        return False
    if len(text) > MAX_STRUCTURED_ITEM_CHARS:
        return False
    if text.count(".") + text.count("。") + text.count("?") + text.count("!") > 2:
        return False
    return True


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
    if text.count(".") + text.count("。") + text.count("?") + text.count("!") > 2:
        return False
    return bool(MANUAL_STEP_ACTION_PATTERN.search(text))
