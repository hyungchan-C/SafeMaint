from __future__ import annotations

from dataclasses import dataclass
import re

from app.schemas.chat import ChatRequest, QuestionIntent


DEFAULT_CLARIFICATION_QUESTION = (
    "부품의 일반적인 정보가 필요한가요, 아니면 설치·점검·교체 방법이 필요한가요?"
)

_DOCUMENT_REFERENCE = re.compile(
    r"(?:이\s*)?(?:pdf|문서|파일|매뉴얼|설명서|도면|카탈로그)", re.IGNORECASE
)
_DOCUMENT_PURPOSE = re.compile(
    r"(?:요약|개요|무슨\s*파일|문서\s*종류|작성일|버전|목차|내용|"
    r"나와|있어|있는지|포함|언급|찾아|몇\s*페이지|어느\s*페이지)",
    re.IGNORECASE,
)
_MAINTENANCE_ACTION = re.compile(
    r"(?:설치|교체|점검|청소|정비|수리|조정|분해|조립|탈거|시운전|"
    r"작업|보수|윤활|체결|배선|설정|install|replace|replacement|inspect|"
    r"clean|maintenance|repair)",
    re.IGNORECASE,
)
_MAINTENANCE_PURPOSE = re.compile(
    r"(?:방법|절차|순서|주의|확인|위험|안전|작업\s*전|작업\s*중|"
    r"해야|하려|할\s*때|어떻게|체크리스트|tbm|how|procedure|safety|risk)",
    re.IGNORECASE,
)
_COMPONENT_PURPOSE = re.compile(
    r"(?:무슨\s*(?:장비|부품|장치)|뭐야|무엇이야|정의|역할|기능|용도|"
    r"어디에\s*(?:사용|쓰)|어떤\s*(?:장비|부품|장치)|왜\s*(?:사용|쓰)|"
    r"what\s+is|what\s+does|where\s+is|purpose|function)",
    re.IGNORECASE,
)
_GENERIC_REQUEST = re.compile(
    r"(?:관련|대해|설명|알려\s*줘|알려\s*주세요|궁금)", re.IGNORECASE
)


@dataclass(frozen=True, slots=True)
class IntentDecision:
    intent: QuestionIntent
    confidence: float
    clarification_question: str | None = None


def classify_question_intent(request: ChatRequest) -> IntentDecision:
    """Classify the user's purpose without treating a component noun as intent.

    The rules look for what the user asks to do with the subject. A selected
    document is context, not enough by itself to turn every question into
    document QA.
    """

    question = " ".join(request.question.casefold().split())
    has_document_reference = bool(_DOCUMENT_REFERENCE.search(question))
    has_document_purpose = bool(_DOCUMENT_PURPOSE.search(question))
    has_maintenance_action = bool(_MAINTENANCE_ACTION.search(question))
    has_maintenance_purpose = bool(_MAINTENANCE_PURPOSE.search(question))
    has_component_purpose = bool(_COMPONENT_PURPOSE.search(question))

    if has_document_reference and has_document_purpose:
        return IntentDecision("document_qa", 0.96)

    if has_document_reference and request.context.selected_document_ids:
        if re.search(r"(?:요약|내용|찾아|있어|나와|페이지|문서)", question):
            return IntentDecision("document_qa", 0.94)

    if has_component_purpose and not has_maintenance_purpose:
        return IntentDecision("component_info", 0.94)

    if has_maintenance_action and (
        has_maintenance_purpose
        or not has_document_reference
        or re.search(r"(?:작업|교체|설치|청소|점검|정비)", question)
    ):
        return IntentDecision("maintenance_guide", 0.92)

    if has_component_purpose:
        return IntentDecision("component_info", 0.86)

    if has_document_reference:
        return IntentDecision("document_qa", 0.72)

    if _GENERIC_REQUEST.search(question) or len(question.split()) <= 4:
        return IntentDecision(
            "clarification_required",
            0.62,
            DEFAULT_CLARIFICATION_QUESTION,
        )

    return IntentDecision(
        "clarification_required",
        0.5,
        DEFAULT_CLARIFICATION_QUESTION,
    )
