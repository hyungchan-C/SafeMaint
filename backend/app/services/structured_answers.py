from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
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
    MaintenanceHazard,
    MaintenanceSummary,
    NoEvidenceDetails,
    QueryAnalysis,
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
# 위험요인/중지조건/위험판단근거: PDF + 사고사례 + 공용안전 (회사기준 제외)
HAZARD_SOURCE_TYPES = MANUAL_SOURCE_TYPES | PUBLIC_REFERENCE_SOURCE_TYPES
# 작업 전 확인사항/작업 시 주의사항: PDF 중심 + 공용안전(사고사례 제외) + 회사기준
PRECAUTION_SOURCE_TYPES = (
    MANUAL_SOURCE_TYPES
    | (PUBLIC_REFERENCE_SOURCE_TYPES - {"public_incident"})
    | {"company_policy"}
)
MAX_SOURCE_ITEM_CHARS = 180
MAX_CARD_ITEM_CHARS = 72
MAX_FINAL_CARD_CHARS = 34
TBM_CHECKLIST_LIMIT = 5

PDF_SOURCE_GROUP = "pdf"
PUBLIC_SAFETY_SOURCE_GROUP = "public_safety"
INCIDENT_SOURCE_GROUP = "incident"
COMPANY_SOURCE_GROUP = "company"

PRECHECK_TERMS = (
    "광축",
    "투광부",
    "수광부",
    "투광기",
    "수광기",
    "반응",
    "안전거리",
    "방호구역",
    "방호높이",
    "검출성능",
    "검출거리",
    "검출 거리",
    "설정거리",
    "설정 거리",
    "검출체",
    "검출면",
    "주위금속",
    "주위 금속",
    "대향",
    "병렬",
    "주파수 간섭",
    "정격",
    "고정 브라켓",
    "스패터",
    "급정지기구",
    "OSSD",
    "확인",
    "점검",
    "검사",
    "준비",
    "설정",
    "정상",
    "동작",
    "상태",
    "교육",
    "자격",
    "안전거리",
    "작업 전",
    "before",
    "check",
    "inspect",
    "verify",
)
ACTION_TERMS = (
    "광축",
    "투광부",
    "수광부",
    "반응",
    "안전거리",
    "방호구역",
    "OSSD",
    "설치",
    "교체",
    "점검",
    "검사",
    "청소",
    "세척",
    "조정",
    "설정",
    "정렬",
    "분리",
    "연결",
    "체결",
    "고정",
    "브라켓",
    "배선",
    "제거",
    "보수",
    "정비",
    "install",
    "replace",
    "inspect",
    "check",
    "clean",
    "adjust",
    "set",
    "connect",
    "remove",
)
HAZARD_TERMS = (
    "위험",
    "주의",
    "경고",
    "사고",
    "끼임",
    "협착",
    "감전",
    "화재",
    "폭발",
    "추락",
    "낙하",
    "오동작",
    "복귀불량",
    "간섭",
    "노이즈",
    "스패터",
    "무효",
    "손상",
    "부상",
    "위험구역",
    "hazard",
    "danger",
    "warning",
    "caution",
    "accident",
)
STOP_TERMS = (
    "중지",
    "정지",
    "비상정지",
    "즉시",
    "금지",
    "보고",
    "이상",
    "불가",
    "고장",
    "복귀불량",
    "오동작",
    "간섭",
    "해제",
    "위험구역",
    "stop",
    "emergency",
    "fault",
    "abnormal",
)
REFERENCE_TERMS = (
    "법",
    "기준",
    "규정",
    "지침",
    "대책",
    "사례",
    "사고",
    "예방",
    "보호",
    "조치",
    "standard",
    "guide",
    "incident",
)
ROLE_TERMS = (
    "역할",
    "기능",
    "용도",
    "감지",
    "검출",
    "차단",
    "보호",
    "제어",
    "방호",
    "component",
    "function",
)
LOCATION_TERMS = (
    "위치",
    "구역",
    "장소",
    "설비",
    "장비",
    "라인",
    "기계",
    "작업대",
    "통로",
    "위험구역",
)
EQUIPMENT_TERMS = ("설비", "장비", "기계", "라인", "작업대")
COMPONENT_TERMS = (
    "부품",
    "장치",
    "센서",
    "스위치",
    "모듈",
    "컨트롤러",
    "케이블",
    "커버",
    "브라켓",
    "검출체",
    "검출면",
    "컴포넌트",
)
DOCUMENT_TYPE_LABELS = {
    "equipment_manual": "장비 매뉴얼",
    "component_manual": "부품 매뉴얼",
    "maintenance_manual": "정비 매뉴얼",
    "company_policy": "회사 기준",
    "public_law": "관련 법령",
    "public_guide": "안전 가이드",
    "public_media": "안전자료",
    "public_incident": "사고사례",
}
DOCUMENT_EQUIPMENT_TERMS = (
    "컨베이어",
    "벨트 컨베이어",
    "프레스",
    "사출기",
    "크레인",
    "리프트",
    "산업용 로봇",
    "지게차",
    "로봇",
    "공작기계",
    "절단기",
    "프레스기",
    "라인",
    "설비",
    "장비",
    "기계",
    "작업대",
)
DOCUMENT_COMPONENT_TERMS = (
    "세이프티 컴포넌트",
    "안전 센서",
    "센서",
    "스위치",
    "비상정지장치",
    "비상정지 스위치",
    "급정지기구",
    "인터락",
    "인터록",
    "방호장치",
    "방호덮개",
    "접근방지울",
    "투광부",
    "수광부",
    "투광기",
    "수광기",
    "광축",
    "OSSD",
    "PC 설정 툴",
    "검출체",
    "검출면",
    "보호커버",
    "커버",
    "브라켓",
    "컨트롤러",
    "모듈",
    "케이블",
)
DOCUMENT_ENTITY_REJECT_TERMS = (
    "확인",
    "점검",
    "검사",
    "시험",
    "설치",
    "교체",
    "청소",
    "작업",
    "절차",
    "방법",
    "조건",
    "기준",
    "상태",
    "항목",
    "주의사항",
    "요약",
    "문서",
    "매뉴얼",
)
DOCUMENT_ENTITY_FRAGMENT_REJECT_TERMS = (
    "위험부",
    "도달",
    "영역을",
    "넘어",
    "제품과",
    "전에",
    "이전에",
    "공급자가",
    "규정하는",
    "방식",
)
COMPONENT_INFO_TERMS = (
    "방호장치",
    "투광부",
    "수광부",
    "투광기",
    "수광기",
    "광축",
    "검출",
    "감지",
    "차단",
    "정지",
    "보호",
    "위험구역",
    "방호구역",
    "전면",
    "프레스",
    "로봇",
    "컨베이어",
    "센서",
    "스위치",
    "인터락",
    "인터록",
    "비상정지",
    "검출거리",
    "설정거리",
    "검출체",
    "검출면",
    "주위금속",
    "주위 금속",
    "대향",
    "병렬",
    "주파수 간섭",
    "브라켓",
    "고정",
    "스패터",
)
SECTION_STYLE_ENDINGS = (
    "하기",
    "않기",
    "말기",
    "금지",
    "준수",
    "유지",
)
STOP_CONDITION_MARKERS = (
    "않으면",
    "없으면",
    "못하면",
    "불가",
    "확인할 수 없",
    "유지할 수 없",
    "멈추지",
    "정지하지",
    "작동하지",
    "동작하지",
    "이상",
    "고장",
    "오검출",
    "불량",
    "상태면",
    "발생하면",
    "발생 시",
)
STOP_ACTION_MARKERS = ("중지", "정지", "보류", "금지")

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
        "대해",
        "알려줘",
        "알려줄래",
        "알려주세요",
        "해야",
        "하려고",
        "예정",
        "어떻게",
        "무슨",
        "뭐야",
        "무엇",
        "정의",
        "설명",
        "알려",
        "사용",
        "용도",
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
    "검출거리",
    "설정거리",
    "주위금속",
    "주위 금속",
    "대향",
    "병렬",
    "주파수 간섭",
    "오동작",
    "복귀불량",
    "스패터",
    "브라켓",
    "lockout",
    "tagout",
    "interlock",
)
META_ITEM_PATTERNS = (
    "근거를 원문에서 확인",
    "문서 id",
    "문서 버전",
    "사용자 매뉴얼",
    ".pdf",
)
BAD_CARD_PREFIXES = (
    "은 ",
    "는 ",
    "이 ",
    "가 ",
    "을 ",
    "를 ",
    "에 ",
    "에서 ",
    "후에",
    "특히 ",
)
BAD_CARD_SUFFIXES = (
    "...",
    "예.",
    "후에",
    "직",
    "및",
    "또는",
)
GENERIC_QWEN_FALLBACK_ANSWERS = (
        "검색된 근거를 기준으로 작업 전 확인할 핵심 사항을 요약했습니다.",
        "검색된 문서 근거를 기준으로 유지보수 시 확인할 사항을 정리했습니다.",
        "검색된 문서 근거를 기준으로 확인 가능한 내용을 요약했습니다.",
        "검색된 문서 근거를 기준으로 질문과 관련된 내용을 요약했습니다.",
        "검색된 문서 근거에서 확인되는 부품 정보를 정리했습니다.",
        "검색된 문서 근거를 기준으로 부품 정보를 요약했습니다.",
    )
CONCISE_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("주위금속",), "주위금속 영향 거리 확인"),
    (("주위 금속",), "주위 금속 영향 거리 확인"),
    (("대향", "병렬"), "대향·병렬 설치 간섭거리 확인"),
    (("주파수", "간섭"), "주파수 간섭 방지 거리 확인"),
    (("고정", "브라켓"), "고정 브라켓 적용 확인"),
    (("검출 거리",), "검출거리 기준 확인"),
    (("검출거리",), "검출거리 기준 확인"),
    (("설정 거리",), "설정거리 기준 확인"),
    (("설정거리",), "설정거리 기준 확인"),
    (("스패터", "보호"), "스패터 보호커버 적용 확인"),
    (("설치 후", "반응"), "설치 후 반응 확인"),
    (("올바르게", "반응"), "설치 후 반응 확인"),
    (("투광부", "수광부"), "투광부/수광부 광축 확인"),
    (("광축", "차단"), "광축 차단 시험 확인"),
    (("빔", "차단"), "빔 차단 시 정지 확인"),
    (("위험한 동작", "멈추"), "위험 동작 정지 확인"),
    (("방호높이",), "방호높이 확인"),
    (("검출성능",), "검출성능 확인"),
    (("급정지기구",), "급정지기구 연동 확인"),
    (("방호구역", "견고"), "방호구역 고정 확인"),
    (("안전인증기준",), "안전인증기준 적합 확인"),
    (("운전을 정지", "작동하지 않도록"), "운전 정지/재가동 방지"),
    (("전원차단", "잠금"), "전원 차단/잠금 확인"),
    (("전원 차단", "잠금"), "전원 차단/잠금 확인"),
    (("기동장치", "잠금"), "기동장치 잠금 확인"),
    (("잠금장치",), "잠금장치 설치 확인"),
    (("비상정지", "작업현장"), "비상정지스위치 접근 점검"),
    (("비상정지 스위치",), "비상정지스위치 위치 점검"),
    (("비상 정지",), "비상정지장치 위치 점검"),
    (("정기적으로 정비",), "정기 정비 상태 점검"),
    (("작업표준", "취급요령"), "작업표준 교육 확인"),
    (("방호덮개", "개방하지"), "방호덮개 개방 금지"),
    (("접근방지울",), "접근방지울 설치 확인"),
    (("협착 위험부", "안전덮개"), "협착 위험부 덮개 확인"),
    (("끼임", "예방", "포스터"), "끼임 예방 포스터"),
    (("끼임", "예방", "지침"), "끼임 예방 지침"),
    (("끼임", "절단재해", "예방"), "끼임·절단재해 예방 지침"),
    (("협착사고",), "협착사고 사례"),
    (("협착", "사고"), "협착사고 사례"),
    (("청소작업", "협착"), "청소작업 협착사고 사례"),
    (("전원", "차단"), "전원 차단 확인"),
    (("전원차단",), "전원 차단 확인"),
    (("정지", "확인"), "기계 정지 확인"),
    (("기동장치", "잠금"), "기동장치 잠금 확인"),
    (("잠금",), "잠금 조치 확인"),
    (("격리",), "에너지 격리 확인"),
    (("안전 거리",), "안전거리 확보"),
    (("안전거리",), "안전거리 확보"),
    (("방호덮개", "설치"), "방호덮개 설치 확인"),
    (("방호", "덮개"), "방호덮개 설치 확인"),
    (("비상정지",), "비상정지 위치 확인"),
    (("위험 구역", "작업자"), "위험구역 작업자 없음"),
    (("위험구역", "작업자"), "위험구역 작업자 없음"),
    (("위험 구역", "위치"), "위험구역 위치 확인"),
    (("위험구역", "위치"), "위험구역 위치 확인"),
    (("인터락", "해제"), "인터락 해제 위치 확인"),
    (("인터락", "재기동"), "재기동 전 인터락 확인"),
    (("재기동", "작업자"), "재기동 전 작업자 확인"),
    (("인터록", "변경"), "인터록 변경관리 지침"),
    (("인터록", "설정값"), "인터록 설정값 변경관리"),
    (("인터록", "작동시험"), "인터록 작동시험 확인"),
    (("설정", "동작"), "설정·동작 확인"),
    (("기능", "설정"), "기능 설정 확인"),
    (("설치", "구성"), "설치 구성 확인"),
    (("설치",), "설치 조건 확인"),
    (("교체",), "교체 후 상태 확인"),
    (("점검",), "점검 상태 확인"),
    (("검사",), "검사 상태 확인"),
    (("청소",), "청소 전 정지 확인"),
    (("정렬",), "정렬 상태 확인"),
    (("조정",), "조정값 확인"),
    (("배선",), "배선 상태 확인"),
    (("보호",), "보호장치 확인"),
    (("방호",), "방호장치 확인"),
    (("무효",), "안전기능 무효화 주의"),
    (("오동작",), "오동작 위험 확인"),
    (("인사사고",), "인사사고 위험 확인"),
    (("위험",), "위험요인 확인"),
    (("사고",), "사고사례 확인"),
)


@dataclass(frozen=True)
class SentenceCandidate:
    content: str
    evidence_chunk_id: str
    source_type: str
    source_group: str
    score: float


ENTITY_SUFFIXES = (
    "센서",
    "스위치",
    "장치",
    "모듈",
    "컨트롤러",
    "케이블",
    "커튼",
    "베어링",
    "모터",
    "펌프",
    "밸브",
    "실린더",
    "로봇",
    "컨베이어",
    "프레스",
    "브라켓",
    "커버",
    "기구",
    "부품",
    "컴포넌트",
    "릴레이",
    "차단기",
    "인버터",
    "드라이버",
    "설비",
    "장비",
    "기계",
)
COMPONENT_ENTITY_SUFFIXES = (
    "센서",
    "스위치",
    "장치",
    "모듈",
    "컨트롤러",
    "케이블",
    "커튼",
    "베어링",
    "모터",
    "펌프",
    "밸브",
    "실린더",
    "브라켓",
    "커버",
    "기구",
    "부품",
    "컴포넌트",
    "릴레이",
    "차단기",
    "인버터",
    "드라이버",
)
EQUIPMENT_ENTITY_SUFFIXES = (
    "컨베이어",
    "프레스",
    "사출기",
    "크레인",
    "리프트",
    "로봇",
    "설비",
    "장비",
    "기계",
    "라인",
    "작업대",
)
GENERIC_ENTITY_VALUES = {
    "부품",
    "장치",
    "설비",
    "장비",
    "기계",
    "라인",
    "항목",
    "제품",
    "제품과 기계",
    "기계장치",
    "문서",
    "매뉴얼",
}
QUESTION_ACTION_WORDS = (
    "설치",
    "교체",
    "사용",
    "운전",
    "작동",
    "재기동",
    "리셋",
    "해제",
    "변경",
    "확인",
    "점검",
    "청소",
    "정비",
    "보수",
    "수리",
    "조정",
    "작업",
    "방법",
    "절차",
    "요약",
    "설명",
)
TECHNICAL_PRECHECK_TERMS = (
    "렌즈",
    "초점",
    "시야",
    "조명",
    "트리거",
    "카메라",
    "센서",
    "베어링",
    "하우징",
    "윤활",
    "축",
    "커플링",
    "이상음",
    "진동",
    "과열",
    "벨트",
    "장력",
    "풀리",
    "체인",
    "기어",
    "체결 토크",
    "볼트",
    "너트",
    "와셔",
    "압력",
    "유량",
    "누설",
    "필터",
    "접지",
    "절연",
    "잔류전압",
    "브레이크",
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
    """Build short card candidates from retrieved rows for Qwen and fallback UI."""

    if not sources:
        return None
    chunk_ids = [source.chunk_id for source in sources]
    first = sources[0]
    candidates = _sentence_candidates(sources, question)

    if answer_type == "document_qa":
        return finalize_document_answer(
            DocumentAnswerDetails(
                overview=DocumentOverview(
                    filename=first.original_filename or first.title,
                    document_type=canonical_document_type(first.source_type),
                    version=(
                        str(first.document_version)
                        if first.document_version is not None
                        else None
                    ),
                ),
                evidence_chunk_ids=chunk_ids,
                unverified_information=[
                    "검색된 부분 밖의 문서 전문은 확인하지 못했습니다."
                ],
            ),
            sources=sources,
            question=question,
        )

    if answer_type == "component_info":
        component_sources = [
            source
            for source in sources
            if canonical_document_type(source.source_type)
            in {"component_manual", "equipment_manual", "public_guide", "public_law"}
        ] or sources
        component_candidates = _sentence_candidates(component_sources, question)
        main_roles = _component_role_items(
            component_candidates,
            question=question,
            limit=5,
        )
        usage_locations = _component_usage_items(
            component_candidates,
            question=question,
            limit=5,
        )
        precautions = _component_precaution_items(
            component_candidates,
            question=question,
            limit=5,
        )
        fallback_main_roles = _items_for_card(
            candidates,
            groups={PDF_SOURCE_GROUP, PUBLIC_SAFETY_SOURCE_GROUP},
            terms=ROLE_TERMS,
            limit=5,
            allow_fallback=True,
            exclude_risk_only=True,
        )
        return finalize_component_answer(
            ComponentAnswerDetails(
                one_line_description=_component_definition_sentence(
                    component_sources,
                    main_roles or fallback_main_roles,
                    question=question,
                ),
                main_roles=main_roles or _component_verified_role_items(
                    fallback_main_roles,
                    limit=5,
                ),
                usage_locations=usage_locations,
                precautions=precautions,
                evidence_chunk_ids=chunk_ids,
                additional_information_needed=[],
            ),
            sources=sources,
            question=question,
        )

    if answer_type == "maintenance_guide":
        pre_checks = _maintenance_pre_check_items(
            candidates,
            question=question,
            limit=3,
        )
        pre_check_keys = _maintenance_item_keys(pre_checks)
        hazards = _maintenance_hazards_for_card(
            candidates,
            question=question,
            limit=3,
        )
        manual_steps = _maintenance_manual_step_items(
            candidates,
            question=question,
            limit=6,
        )
        precautions = _maintenance_precaution_items(
            candidates,
            question=question,
            excluded_keys=pre_check_keys,
            limit=4,
        )
        used_section_keys = pre_check_keys | _maintenance_item_keys(precautions)
        stop_conditions = _maintenance_stop_condition_items(
            candidates,
            question=question,
            excluded_keys=used_section_keys,
            fallback_items=pre_checks + precautions,
            limit=5,
        )
        public_references = _maintenance_reference_items_from_sources(
            sources,
            question=question,
            limit=8,
        )
        return MaintenanceAnswerDetails(
            summary=MaintenanceSummary(
                status="안전관리자 확인 필요",
                risk_level="판단 불가",
                risk_basis=[],
                core_warning=_core_warning_from_labels(
                    _risk_labels_for_summary(None, hazards, sources=sources)
                ),
            ),
            pre_checks=pre_checks,
            hazards=hazards,
            manual_steps=manual_steps,
            precautions=precautions,
            stop_conditions=stop_conditions,
            related_regulations_and_incidents=public_references,
            evidence_chunk_ids=chunk_ids,
            additional_information_needed=_additional_needed_items(
                pre_checks=pre_checks,
                manual_steps=manual_steps,
                references=public_references,
            ),
        )
    return None


def source_based_checklist_items(
    answer_type: AnswerType,
    sources: list[ChatSource],
    *,
    question: str = "",
) -> list[ChatChecklistItem]:
    if answer_type != "maintenance_guide":
        return []
    fallback = source_based_fallback(answer_type, sources, question=question)
    if not isinstance(fallback, MaintenanceAnswerDetails):
        return []
    return checklist_items_from_pre_checks(fallback)


def checklist_items_from_pre_checks(
    value: StructuredAnswer | None,
) -> list[ChatChecklistItem]:
    if not isinstance(value, MaintenanceAnswerDetails):
        return []
    items: list[ChatChecklistItem] = []
    seen_phrases: set[str] = set()
    seen_keys: set[str] = set()
    pre_check_keys = _maintenance_item_keys(value.pre_checks)
    items.extend(
        _chat_checklist_items_from_evidence(
            value.manual_steps,
            excluded_keys=set(),
            sequence_start=1,
            seen_phrases=seen_phrases,
            seen_keys=seen_keys,
            allow_excluded=True,
            limit=TBM_CHECKLIST_LIMIT,
        )
    )
    supplemental_sources = [
        *value.precautions,
        *value.stop_conditions,
    ]
    if len(items) < TBM_CHECKLIST_LIMIT:
        items.extend(
            _chat_checklist_items_from_evidence(
                supplemental_sources,
                excluded_keys=pre_check_keys,
                sequence_start=len(items) + 1,
                seen_phrases=seen_phrases,
                seen_keys=seen_keys,
                allow_excluded=False,
                limit=TBM_CHECKLIST_LIMIT,
            )
        )
    if len(items) < TBM_CHECKLIST_LIMIT:
        items.extend(
            _chat_checklist_items_from_evidence(
                value.pre_checks,
                excluded_keys=set(),
                sequence_start=len(items) + 1,
                seen_phrases=seen_phrases,
                seen_keys=seen_keys,
                allow_excluded=True,
                limit=TBM_CHECKLIST_LIMIT,
            )
        )
    if len(items) < TBM_CHECKLIST_LIMIT:
        items.extend(
            _chat_checklist_items_from_evidence(
                supplemental_sources,
                excluded_keys=pre_check_keys,
                sequence_start=len(items) + 1,
                seen_phrases=seen_phrases,
                seen_keys=seen_keys,
                allow_excluded=True,
                limit=TBM_CHECKLIST_LIMIT,
            )
        )
    return items[:TBM_CHECKLIST_LIMIT]


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
        verified_risk_basis: list[EvidenceBackedItem] = []
        verified_pre_checks = _verified_items_by_type(
            value.pre_checks, allowed_ids, source_by_id, PRECAUTION_SOURCE_TYPES
        )
        verified_pre_checks = _dedupe_maintenance_items(
            verified_pre_checks,
            validator=_is_maintenance_pre_check_phrase,
            limit=20,
        )
        verified_pre_check_keys = _maintenance_item_keys(verified_pre_checks)
        verified_hazards = _verified_items_by_type(
            value.hazards, allowed_ids, source_by_id, HAZARD_SOURCE_TYPES
        )
        verified_hazards = _normalize_maintenance_hazards(
            verified_hazards,
            limit=3,
        )
        verified_precautions = _verified_items_by_type(
            value.precautions, allowed_ids, source_by_id, PRECAUTION_SOURCE_TYPES
        )
        verified_precautions = _normalize_maintenance_items(
            verified_precautions,
            formatter=_precaution_phrase,
            excluded_keys=verified_pre_check_keys,
            fallback_to_excluded=True,
            min_items=2,
            limit=20,
        )
        verified_manual_steps: list[EvidenceBackedItem] = []
        for step in value.manual_steps:
            raw_step = _verified_item(step, allowed_ids)
            normalized_step = _verified_item(
                step,
                allowed_ids,
                compact_for_card=True,
            )
            if (
                raw_step is not None
                and normalized_step is not None
                and _looks_like_actionable_manual_step(raw_step.content)
                and _item_relevant_to_question(raw_step.content, question)
                and all(
                    chunk_id in source_by_id
                    and is_manual_source(source_by_id[chunk_id])
                    for chunk_id in normalized_step.evidence_chunk_ids
                )
            ):
                verified_manual_steps.append(normalized_step)
        verified_stop_conditions = _verified_items_by_type(
            value.stop_conditions, allowed_ids, source_by_id, HAZARD_SOURCE_TYPES
        )
        verified_stop_conditions = _normalize_maintenance_items(
            verified_stop_conditions,
            formatter=_stop_condition_phrase,
            excluded_keys=verified_pre_check_keys
            | _maintenance_item_keys(verified_precautions),
            fallback_to_excluded=True,
            min_items=3,
            limit=20,
        )
        if not verified_stop_conditions:
            verified_stop_conditions = _normalize_maintenance_items(
                [*verified_pre_checks, *verified_precautions],
                formatter=_stop_condition_phrase,
                limit=20,
            )
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
                and all(
                    _reference_source_relevant_to_question(
                        source_by_id[chunk_id],
                        question,
                    )
                    for chunk_id in normalized_item.evidence_chunk_ids
                )
            ):
                source = source_by_id[normalized_item.evidence_chunk_ids[0]]
                reference_content = _maintenance_reference_content(
                    source,
                    question=question,
                )
                if reference_content:
                    verified_public_references.append(
                        normalized_item.model_copy(
                            update={"content": reference_content}
                        )
                    )
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
                "precautions": verified_precautions,
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
        value = finalize_document_answer(
            value,
            sources=sources,
            question=question,
        )
    elif isinstance(value, ComponentAnswerDetails):
        component_allowed_ids = {
            source.chunk_id
            for source in sources
            if canonical_document_type(source.source_type)
            in {"component_manual", "equipment_manual", "public_guide", "public_law"}
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
        value = finalize_component_answer(
            value,
            sources=sources,
            question=question,
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
    if isinstance(value, MaintenanceAnswerDetails) and isinstance(
        fallback,
        MaintenanceAnswerDetails,
    ):
        return value.model_copy(
            update={
                "summary": value.summary.model_copy(
                    update={
                        "risk_basis": value.summary.risk_basis
                        or fallback.summary.risk_basis,
                        "core_warning": value.summary.core_warning
                        or fallback.summary.core_warning,
                    }
                ),
                # Pre-check cards must come only from verified manual evidence.
                # Do not let a model fill missing manufacturer requirements.
                "pre_checks": fallback.pre_checks,
                "hazards": value.hazards or fallback.hazards,
                "manual_steps": value.manual_steps or fallback.manual_steps,
                "precautions": _dedupe_maintenance_items(
                    value.precautions or fallback.precautions,
                    limit=8,
                ),
                "stop_conditions": _dedupe_maintenance_items(
                    value.stop_conditions or fallback.stop_conditions,
                    limit=8,
                ),
                "related_regulations_and_incidents": (
                    value.related_regulations_and_incidents
                    or fallback.related_regulations_and_incidents
                ),
                "evidence_chunk_ids": value.evidence_chunk_ids
                or fallback.evidence_chunk_ids,
                "additional_information_needed": (
                    value.additional_information_needed
                    or fallback.additional_information_needed
                ),
            }
        )
    if isinstance(value, DocumentAnswerDetails) and isinstance(
        fallback,
        DocumentAnswerDetails,
    ):
        return value.model_copy(
            update={
                "main_contents": value.main_contents or fallback.main_contents,
                "related_equipment": value.related_equipment
                or fallback.related_equipment,
                "related_components": value.related_components
                or fallback.related_components,
                "supported_tasks": value.supported_tasks or fallback.supported_tasks,
                "evidence_chunk_ids": value.evidence_chunk_ids
                or fallback.evidence_chunk_ids,
                "unverified_information": value.unverified_information
                or fallback.unverified_information,
            }
        )
    if isinstance(value, ComponentAnswerDetails) and isinstance(
        fallback,
        ComponentAnswerDetails,
    ):
        return value.model_copy(
            update={
                "main_roles": value.main_roles or fallback.main_roles,
                "usage_locations": value.usage_locations
                or fallback.usage_locations,
                "precautions": value.precautions or fallback.precautions,
                "evidence_chunk_ids": value.evidence_chunk_ids
                or fallback.evidence_chunk_ids,
                "additional_information_needed": (
                    value.additional_information_needed
                    or fallback.additional_information_needed
                ),
            }
        )
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


def _sentence_candidates(
    sources: list[ChatSource],
    question: str,
) -> list[SentenceCandidate]:
    question_terms = _relevance_terms(question)
    candidates: list[SentenceCandidate] = []
    for source in sources:
        source_type = canonical_document_type(source.source_type)
        source_group = _source_group(source)
        source_score = max(
            float(source.reranker_score or 0.0),
            float(source.retrieval_score or 0.0),
            float(source.similarity or 0.0),
        )
        if source_group == PDF_SOURCE_GROUP:
            profile = getattr(source, "document_profile", None)
            if isinstance(profile, dict):
                profile_values: list[str] = []
                for field in ("supported_tasks", "safety_topics", "summary_points"):
                    profile_values.extend(_profile_string_values(profile, field))
                for value in profile_values:
                    if not _has_meaningful_text(value):
                        continue
                    candidates.append(
                        SentenceCandidate(
                            content=value,
                            evidence_chunk_id=source.chunk_id,
                            source_type=source_type,
                            source_group=source_group,
                            score=source_score + 0.22,
                        )
                    )
        if source_group in {
            PUBLIC_SAFETY_SOURCE_GROUP,
            INCIDENT_SOURCE_GROUP,
            COMPANY_SOURCE_GROUP,
        }:
            title = _clean_source_excerpt(source.title)
            if _has_meaningful_text(title) and not _looks_like_meta_item(title):
                candidates.append(
                    SentenceCandidate(
                        content=title,
                        evidence_chunk_id=source.chunk_id,
                        source_type=source_type,
                        source_group=source_group,
                        score=source_score + 0.18,
                    )
                )
        for index, sentence in enumerate(_split_candidate_sentences(source.excerpt)):
            content = _clean_source_excerpt(sentence)
            if not _has_meaningful_text(content) or _looks_like_meta_item(content):
                continue
            score = _candidate_score(
                content,
                question_terms,
                source_score=source_score,
                sentence_index=index,
            )
            candidates.append(
                SentenceCandidate(
                    content=content,
                    evidence_chunk_id=source.chunk_id,
                    source_type=source_type,
                    source_group=source_group,
                    score=score,
                )
            )
    return sorted(candidates, key=lambda candidate: candidate.score, reverse=True)


def _source_group(source: ChatSource) -> str:
    source_type = canonical_document_type(source.source_type)
    if source_type in MANUAL_SOURCE_TYPES:
        return PDF_SOURCE_GROUP
    if source_type == "public_incident":
        return INCIDENT_SOURCE_GROUP
    if source_type in {"public_law", "public_guide", "public_media"}:
        return PUBLIC_SAFETY_SOURCE_GROUP
    if source_type == "company_policy":
        return COMPANY_SOURCE_GROUP
    return "other"


def _split_candidate_sentences(excerpt: str) -> list[str]:
    cleaned = _clean_source_excerpt(excerpt)
    if not cleaned:
        return []
    cleaned = re.sub(r"(?<=[다요함음됨임])\.\s+", ".\n", cleaned)
    cleaned = re.sub(r"(?<=[.!?。])\s+", "\n", cleaned)
    cleaned = re.sub(r"\s*[•·\-]\s+", "\n", cleaned)
    pieces = re.split(r"\n+|(?<=다\.)\s+|(?<=요\.)\s+|(?<=함\.)\s+", cleaned)
    return [piece.strip() for piece in pieces if piece.strip()]


def _candidate_score(
    content: str,
    question_terms: set[str],
    *,
    source_score: float,
    sentence_index: int,
) -> float:
    text = content.casefold()
    content_terms = _relevance_terms(content)
    overlap = len(question_terms & content_terms) / max(len(question_terms), 1)
    signal_hits = sum(
        1
        for term in PRECHECK_TERMS
        + ACTION_TERMS
        + HAZARD_TERMS
        + STOP_TERMS
        + REFERENCE_TERMS
        if term.casefold() in text
    )
    early_bonus = max(0.0, 0.08 - sentence_index * 0.01)
    length_penalty = 0.0 if len(content) <= 90 else 0.08
    return source_score + overlap * 0.6 + min(signal_hits, 5) * 0.05 + early_bonus - length_penalty


def _items_for_card(
    candidates: list[SentenceCandidate],
    *,
    groups: set[str],
    terms: tuple[str, ...],
    limit: int,
    allow_fallback: bool = False,
    require_action: bool = False,
    exclude_risk_only: bool = False,
    exclude_reference_title: bool = False,
    question: str = "",
    dedupe_evidence: bool = True,
) -> list[EvidenceBackedItem]:
    selected = _candidate_subset(
        candidates,
        groups=groups,
        terms=terms,
        limit=max(limit, limit * 4),
        allow_fallback=allow_fallback,
        require_action=require_action,
        exclude_risk_only=exclude_risk_only,
        exclude_reference_title=exclude_reference_title,
    )
    items: list[EvidenceBackedItem] = []
    seen: set[str] = set()
    used_evidence: set[str] = set()
    for candidate in selected:
        if dedupe_evidence and candidate.evidence_chunk_id in used_evidence:
            continue
        if question and not _item_relevant_to_question(candidate.content, question):
            continue
        content = _final_card_phrase(candidate.content)
        if content == "위험요인 확인":
            continue
        if not content or content in seen:
            continue
        seen.add(content)
        used_evidence.add(candidate.evidence_chunk_id)
        items.append(
            EvidenceBackedItem(
                content=content,
                evidence_chunk_ids=[candidate.evidence_chunk_id],
            )
        )
        if len(items) >= limit:
            break
    return items


def finalize_maintenance_answer(
    value: StructuredAnswer | None,
    *,
    sources: list[ChatSource],
    question: str,
    analysis: QueryAnalysis | None = None,
) -> StructuredAnswer | None:
    if not isinstance(value, MaintenanceAnswerDetails):
        return value
    hazards = _normalize_maintenance_hazards(value.hazards, limit=3)
    related = value.related_regulations_and_incidents
    if not related:
        related = _maintenance_reference_items_from_sources(
            sources,
            question=question,
            limit=8,
        )
    labels = _risk_labels_for_summary(analysis, hazards, sources=sources)
    return value.model_copy(
        update={
            "summary": value.summary.model_copy(
                update={
                    "risk_level": "판단 불가",
                    "risk_basis": [],
                    "core_warning": _core_warning_from_labels(labels),
                }
            ),
            "pre_checks": _dedupe_maintenance_items(value.pre_checks, limit=3),
            "hazards": hazards,
            "precautions": _dedupe_maintenance_items(value.precautions, limit=8),
            "stop_conditions": _dedupe_maintenance_items(
                value.stop_conditions,
                limit=8,
            ),
            "related_regulations_and_incidents": related,
        }
    )


def finalize_document_answer(
    value: StructuredAnswer | None,
    *,
    sources: list[ChatSource],
    question: str,
) -> StructuredAnswer | None:
    if not isinstance(value, DocumentAnswerDetails):
        return value
    if not sources:
        return value

    document_sources = [
        source for source in sources if _source_group(source) == PDF_SOURCE_GROUP
    ] or sources
    candidates = _sentence_candidates(document_sources, question)
    chunk_ids = [
        source.chunk_id
        for source in sources
        if source.chunk_id
    ]
    allowed_ids = set(chunk_ids)
    main_contents = _document_profile_summary_items(document_sources, limit=4)
    if not main_contents:
        main_contents = _document_summary_items(candidates, question=question)
    if not main_contents:
        main_contents = _verified_items(value.main_contents, allowed_ids)
    related_equipment = _document_profile_strings(
        document_sources,
        "equipment",
        limit=6,
    ) or _document_related_entities(
        document_sources,
        existing=value.related_equipment,
        terms=DOCUMENT_EQUIPMENT_TERMS,
        generic_kind="equipment",
        limit=6,
    )
    related_components = _document_profile_strings(
        document_sources,
        "components",
        "product_names",
        limit=8,
    ) or _document_related_entities(
        document_sources,
        existing=value.related_components,
        terms=DOCUMENT_COMPONENT_TERMS,
        generic_kind="component",
        limit=8,
    )
    supported_tasks = _document_profile_strings(
        document_sources,
        "supported_tasks",
        limit=8,
    ) or _document_supported_tasks(
        candidates,
        existing=value.supported_tasks,
        limit=8,
    )
    return value.model_copy(
        update={
            "overview": _document_overview(document_sources[0], value.overview),
            "main_contents": main_contents,
            "related_equipment": related_equipment,
            "related_components": related_components,
            "supported_tasks": supported_tasks,
            "evidence_chunk_ids": [
                chunk_id
                for chunk_id in value.evidence_chunk_ids
                if chunk_id in allowed_ids
            ]
            or chunk_ids,
            "unverified_information": _document_unverified_information(
                value.unverified_information,
                has_supported_tasks=bool(supported_tasks),
            ),
        }
    )


def finalize_component_answer(
    value: StructuredAnswer | None,
    *,
    sources: list[ChatSource],
    question: str,
) -> StructuredAnswer | None:
    if not isinstance(value, ComponentAnswerDetails):
        return value
    if not sources:
        return value

    component_sources = [
        source
        for source in sources
        if canonical_document_type(source.source_type)
        in {"component_manual", "equipment_manual", "public_guide", "public_law"}
    ] or sources
    candidates = _sentence_candidates(component_sources, question)
    allowed_ids = {source.chunk_id for source in component_sources}
    verified_roles = _component_verified_role_items(
        _verified_items(value.main_roles, allowed_ids),
        limit=5,
    )
    roles = _component_role_items(candidates, question=question, limit=5) or verified_roles
    verified_usage = _component_verified_usage_items(
        _verified_items(value.usage_locations, allowed_ids),
        limit=5,
    )
    usage_locations = (
        _component_usage_items(candidates, question=question, limit=5)
        or verified_usage
    )
    verified_precautions = _component_verified_precaution_items(
        _verified_items(value.precautions, allowed_ids),
        limit=5,
    )
    precautions = (
        _component_precaution_items(candidates, question=question, limit=5)
        or verified_precautions
    )
    return value.model_copy(
        update={
            "one_line_description": _component_definition_sentence(
                component_sources,
                roles,
                question=question,
            ),
            "main_roles": roles,
            "usage_locations": usage_locations,
            "precautions": precautions,
            "evidence_chunk_ids": [
                chunk_id
                for chunk_id in value.evidence_chunk_ids
                if chunk_id in allowed_ids
            ]
            or [source.chunk_id for source in component_sources if source.chunk_id],
            "additional_information_needed": _component_additional_needed(
                roles,
                usage_locations,
                precautions,
            ),
        }
    )


def _component_definition_sentence(
    sources: list[ChatSource],
    main_roles: list[EvidenceBackedItem],
    *,
    question: str = "",
) -> str:
    subject = (
        _question_subject_phrase(question)
        or _document_subject_from_sources(sources, kind="component")
        or "해당 부품"
    )
    subject = _trim_entity_phrase(subject, max_chars=42)
    kind = _entity_kind_label(subject)
    topic = _topic_phrase(subject)
    source_text = " ".join(_document_source_text(source) for source in sources).casefold()
    if any(term in source_text for term in ("투광부", "수광부", "투광기", "수광기", "광축")) and any(
        term in source_text for term in ("검출", "감지", "차단", "정지")
    ):
        return f"{topic} 검출 영역 차단을 감지해 위험 동작 정지 신호를 보내는 {kind}입니다."
    if any(term in source_text for term in ("검출", "감지", "검출체", "검출면", "탐지")):
        return f"{topic} 대상의 접근·위치·상태를 감지해 설비 제어에 쓰이는 {kind}입니다."
    if any(term in source_text for term in ("회전", "윤활", "마찰", "하중")):
        return f"{topic} 회전부를 지지하고 마찰과 하중을 관리하는 {kind}입니다."
    definition_body = _definition_body_from_sources(sources)
    if definition_body:
        return _polite_document_sentence(f"{topic} {definition_body}")
    role_labels = [
        item.content.rstrip(". ")
        for item in main_roles[:2]
        if item.content and not _looks_like_bad_card_text(item.content, enforce_length=False)
    ]
    if role_labels:
        return f"{topic} {', '.join(role_labels)} 역할을 하는 {kind}입니다."
    return f"{topic} 검색된 문서에서 확인되는 {kind}입니다."


def _question_subject_phrase(question: str) -> str:
    tokens: list[str] = []
    for raw_token in re.findall(r"[0-9A-Za-z가-힣□_-]+", question):
        token = _strip_subject_particle(raw_token)
        lowered = token.casefold()
        if len(token) < 2:
            continue
        if lowered in RELEVANCE_STOPWORDS:
            continue
        if lowered in {"거야", "할거야", "예정", "예정이야", "하려고", "할게"}:
            continue
        if _is_question_request_token(lowered):
            continue
        if any(action in token for action in QUESTION_ACTION_WORDS):
            continue
        tokens.append(token)
    return _trim_entity_phrase(" ".join(tokens[:6]), max_chars=42)


def _strip_subject_particle(value: str) -> str:
    text = value.strip()
    if len(text) >= 3 and re.search(r"[가-힣]", text[-1]) and text[-1] in "이가은는을를에":
        return text[:-1]
    return text


def _is_question_request_token(token: str) -> bool:
    return any(
        marker in token
        for marker in (
            "알려",
            "설명해",
            "말해",
            "정리해",
            "요약해",
            "주세요",
            "궁금",
        )
    )


def _topic_phrase(subject: str) -> str:
    if not subject:
        return "해당 부품은"
    last = subject[-1]
    if "가" <= last <= "힣":
        has_batchim = (ord(last) - ord("가")) % 28 != 0
        return f"{subject}{'은' if has_batchim else '는'}"
    return f"{subject}는"


def _document_subject_from_sources(sources: list[ChatSource], *, kind: str) -> str:
    for source in sources:
        text = _document_source_text(source)
        for candidate in _document_generic_entity_candidates(text, kind=kind):
            phrase = _document_entity_phrase(candidate)
            if phrase:
                return phrase
    for source in sources:
        for value in (source.title, source.original_filename, source.section):
            phrase = _document_entity_phrase(str(value or ""), known=True)
            if phrase:
                return phrase
    return ""


def _trim_entity_phrase(value: str, *, max_chars: int = 42) -> str:
    text = _clean_source_excerpt(value)
    text = re.sub(r"\.(?:pdf|PDF)$", "", text).strip(" .,:;·-/[]()")
    text = re.sub(r"^(?:관련|주요|문서 내|해당)\s+", "", text)
    if len(text) > max_chars:
        text = text[:max_chars].rstrip(" ,.;:·-/")
    return text


def _entity_kind_label(subject: str) -> str:
    lowered = subject.casefold()
    for suffix, label in (
        ("센서", "센서"),
        ("스위치", "스위치"),
        ("베어링", "기계 부품"),
        ("케이블", "연결 부품"),
        ("컨트롤러", "제어 부품"),
        ("모듈", "모듈"),
        ("커튼", "안전장치"),
        ("방호장치", "안전장치"),
        ("장치", "장치"),
        ("기구", "기구"),
    ):
        if suffix in lowered:
            return label
    return "부품"


def _definition_body_from_sources(sources: list[ChatSource]) -> str:
    for source in sources:
        for sentence in _split_candidate_sentences(source.excerpt):
            cleaned = _clean_source_excerpt(sentence).strip(" .")
            if not _has_meaningful_text(cleaned) or _looks_like_raw_document_fragment(cleaned):
                continue
            match = re.search(
                r"(?:은|는)\s+(.{8,120}?)(?:입니다|이다|합니다|한다|수행되는\s+장치|수행하는\s+장치)",
                cleaned,
            )
            if not match:
                continue
            body = _clean_source_excerpt(match.group(1)).strip(" .")
            if any(term in body.casefold() for term in ("의미", "숫자", "항목", "s-mark", "kcs", "mm")):
                continue
            if not any(term in body for term in ROLE_TERMS + COMPONENT_INFO_TERMS):
                continue
            body = re.sub(r"\s+", " ", body)
            if len(body) > 90:
                body = body[:90].rsplit(" ", 1)[0].rstrip(" ,.;:·-")
            return body
    return ""


def _component_role_items(
    candidates: list[SentenceCandidate],
    *,
    question: str,
    limit: int,
) -> list[EvidenceBackedItem]:
    selected = _candidate_subset(
        candidates,
        groups={candidate.source_group for candidate in candidates},
        terms=COMPONENT_INFO_TERMS + ROLE_TERMS,
        limit=max(limit * 5, 16),
        allow_fallback=False,
        exclude_risk_only=True,
        exclude_reference_title=True,
    )
    items: list[EvidenceBackedItem] = []
    seen: set[str] = set()
    for candidate in selected:
        if question and not _item_relevant_to_question(candidate.content, question):
            if len(items) >= 2:
                continue
        content = _component_role_phrase(candidate.content)
        if not content or content in seen:
            continue
        items.append(
            EvidenceBackedItem(
                content=content,
                evidence_chunk_ids=[candidate.evidence_chunk_id],
            )
        )
        seen.add(content)
        if len(items) >= limit:
            break
    return items


def _component_verified_role_items(
    items: Iterable[EvidenceBackedItem],
    *,
    limit: int,
) -> list[EvidenceBackedItem]:
    normalized: list[EvidenceBackedItem] = []
    seen: set[str] = set()
    for item in items:
        content = _component_role_phrase(item.content)
        if not content:
            continue
        if content in seen:
            continue
        normalized.append(item.model_copy(update={"content": content}))
        seen.add(content)
        if len(normalized) >= limit:
            break
    return normalized


def _component_role_phrase(text: str) -> str:
    cleaned = _clean_source_excerpt(text)
    if not _has_meaningful_text(cleaned):
        return ""
    lowered = cleaned.casefold()
    if any(
        term in lowered
        for term in (
            "보호 커버",
            "보호커버",
            "고정 브라켓",
            "고정 hole",
            "고정 홀",
            "주의하십시오",
            "사용하십시오",
            "설치하십시오",
            "사용하는 경우",
        )
    ):
        return ""
    if any(term in lowered for term in ("뮤팅", "블랭킹", "파라미터", "설정")) and any(
        term in lowered for term in ("기능", "모니터링", "상태", "제어")
    ):
        return "기능 설정 및 상태 모니터링 지원"
    if "모니터링" in lowered:
        return "작동 상태 모니터링"
    if any(term in lowered for term in ("광축", "빔", "beam")) and any(
        term in lowered for term in ("차단", "검출", "감지")
    ):
        return "검출 영역 차단 감지"
    if any(term in lowered for term in ("검출 거리", "검출거리", "설정 거리", "설정거리", "검출체", "검출면", "감지", "검출", "탐지")):
        return "대상 접근 또는 상태 검출"
    if any(term in lowered for term in ("위험한 동작", "위험 동작", "멈추", "정지", "출력신호", "신호")):
        return "위험 동작 정지 신호 출력"
    if any(term in lowered for term in ("투광부", "수광부", "투광기", "수광기")):
        return "투광부와 수광부를 통한 검출 영역 감시"
    if any(term in lowered for term in ("정격", "전원", "전압", "전류", "배선", "결선", "선식")):
        return "정격·배선 조건에 따른 신호 전달"
    if any(term in lowered for term in ("회전", "하중", "마찰", "윤활", "회전축")):
        return "회전부 지지 및 마찰 저감"
    if any(term in lowered for term in ("위험구역", "위험 구역", "방호구역")) and any(
        term in lowered for term in ("접근", "감지", "검출", "차단")
    ):
        return "위험구역 접근 감지"
    if ("보호" in lowered or "방호" in lowered) and any(
        term in lowered for term in ("작업자", "인체", "위험", "차단", "정지", "감지", "검출")
    ):
        return "작업자 보호"
    if "감지" in lowered or "검출" in lowered:
        return "접근 또는 차단 감지"
    content = _final_card_phrase(cleaned)
    if not content or any(
        term in content
        for term in (
            "확인",
            "점검",
            "검사",
            "시험",
            "경우",
            "주의",
            "설치",
            "사용",
        )
    ):
        return ""
    return content


def _component_usage_items(
    candidates: list[SentenceCandidate],
    *,
    question: str,
    limit: int,
) -> list[EvidenceBackedItem]:
    selected = _candidate_subset(
        candidates,
        groups={candidate.source_group for candidate in candidates},
        terms=LOCATION_TERMS + COMPONENT_INFO_TERMS,
        limit=max(limit * 5, 16),
        allow_fallback=False,
        exclude_reference_title=True,
    )
    items: list[EvidenceBackedItem] = []
    seen: set[str] = set()
    for candidate in selected:
        if question and not _item_relevant_to_question(candidate.content, question):
            if len(items) >= 2:
                continue
        content = _component_usage_phrase(candidate.content)
        if not content or content in seen:
            continue
        items.append(
            EvidenceBackedItem(
                content=content,
                evidence_chunk_ids=[candidate.evidence_chunk_id],
            )
        )
        seen.add(content)
        if len(items) >= limit:
            break
    return items


def _component_verified_usage_items(
    items: Iterable[EvidenceBackedItem],
    *,
    limit: int,
) -> list[EvidenceBackedItem]:
    normalized: list[EvidenceBackedItem] = []
    seen: set[str] = set()
    for item in items:
        content = _component_usage_phrase(item.content)
        if not content:
            continue
        if content in seen:
            continue
        normalized.append(item.model_copy(update={"content": content}))
        seen.add(content)
        if len(normalized) >= limit:
            break
    return normalized


def _component_usage_phrase(text: str) -> str:
    cleaned = _clean_source_excerpt(text)
    if not _has_meaningful_text(cleaned):
        return ""
    lowered = cleaned.casefold()
    if _is_prohibited_usage_context(lowered):
        return ""
    if "아크 용접" in lowered or "스패터" in lowered or "spatter" in lowered:
        return "이물 부착이나 스패터가 발생할 수 있는 설비 주변"
    if any(term in lowered for term in ("고정 hole", "고정 홀", "브라켓", "체결", "장착")):
        return "고정·체결이 필요한 장착 위치"
    if any(term in lowered for term in ("주위금속", "주위 금속", "간섭", "노이즈")):
        # Interference/noise statements describe an installation constraint or
        # avoidance condition, not a place where the component is used.
        return ""
    if "검출체" in lowered or "검출면" in lowered:
        return "대상물 유무·위치 검출 지점"
    if any(term in lowered for term in ("회전", "회전축", "베어링", "윤활")):
        return "회전축 또는 구동부 주변"
    if any(term in lowered for term in ("전원", "배선", "결선", "케이블")):
        return "전원·배선 연결부 주변"
    if "산업용 로봇" in lowered or "로봇" in lowered:
        return "로봇 작업 위험 영역"
    if "프레스" in lowered:
        return "프레스 전면 또는 방호구역"
    if "컨베이어" in lowered:
        return "컨베이어 접근 위험 구간"
    if "위험부" in lowered:
        return "기계 위험부 접근 지점"
    if "전면" in lowered:
        return "기계 전면"
    if "위험구역" in lowered or "위험 구역" in lowered or "방호구역" in lowered:
        return "위험구역 접근부"
    if "작업현장" in lowered and ("비상정지" in lowered or "스위치" in lowered):
        return "작업자가 즉시 접근할 수 있는 위치"
    return ""


def _is_prohibited_usage_context(lowered: str) -> bool:
    return any(
        marker in lowered
        for marker in (
            "사용하지",
            "설치하지",
            "장착하지",
            "피해야",
            "피하십시오",
            "금지",
            "불가",
            "해서는 안",
            "하면 안",
            "하지 마",
            "주의",
            "경고",
            "do not use",
            "do not install",
            "must not",
            "prohibited",
            "avoid",
        )
    )


def _component_precaution_items(
    candidates: list[SentenceCandidate],
    *,
    question: str,
    limit: int,
) -> list[EvidenceBackedItem]:
    selected = _candidate_subset(
        candidates,
        groups={candidate.source_group for candidate in candidates},
        terms=HAZARD_TERMS + PRECHECK_TERMS + ACTION_TERMS + COMPONENT_INFO_TERMS,
        limit=max(limit * 5, 18),
        allow_fallback=False,
        exclude_reference_title=True,
    )
    items: list[EvidenceBackedItem] = []
    seen: set[str] = set()
    for candidate in selected:
        if question and not _item_relevant_to_question(candidate.content, question):
            if len(items) >= 2:
                continue
        content = _component_precaution_phrase(candidate.content)
        if not content or content in seen:
            continue
        items.append(
            EvidenceBackedItem(
                content=content,
                evidence_chunk_ids=[candidate.evidence_chunk_id],
            )
        )
        seen.add(content)
        if len(items) >= limit:
            break
    return items


def _component_verified_precaution_items(
    items: Iterable[EvidenceBackedItem],
    *,
    limit: int,
) -> list[EvidenceBackedItem]:
    normalized: list[EvidenceBackedItem] = []
    seen: set[str] = set()
    for item in items:
        content = _component_precaution_phrase(item.content)
        if not content:
            continue
        if content in seen:
            continue
        normalized.append(item.model_copy(update={"content": content}))
        seen.add(content)
        if len(normalized) >= limit:
            break
    return normalized


def _component_precaution_phrase(text: str) -> str:
    cleaned = _clean_source_excerpt(text)
    if not _has_meaningful_text(cleaned):
        return ""
    lowered = cleaned.casefold()
    if (
        any(term in lowered for term in ("강한 자기력", "강한 자기장", "고주파 노이즈"))
        and _is_prohibited_usage_context(lowered)
    ):
        return "강한 자기장·고주파 노이즈 발생 기기 근처에서 사용하지 않기"
    key = _maintenance_semantic_key(cleaned)
    if key == "metal_clearance":
        return "주변 금속·장애물 이격거리를 확보하지 않은 상태로 설치하지 않기"
    if key == "interference":
        return "인접 장치 간 간섭 방지 거리를 줄이지 않기"
    if key == "detection_distance":
        return "검출거리와 설정거리 범위를 벗어나게 설치하지 않기"
    if key == "mounting":
        return "고정·체결 상태가 불안정한 상태로 사용하지 않기"
    if key == "contamination_damage":
        return "오염·손상 우려가 있으면 보호 조치 없이 사용하지 않기"
    if key == "wiring_power":
        return "정격·전원·배선 조건을 확인하지 않은 상태로 연결하지 않기"
    if any(term in lowered for term in ("무효", "우회", "해제")):
        return "안전기능을 임의로 우회하거나 무효화하지 않기"
    if any(term in lowered for term in ("광축", "투광부", "수광부", "정렬")):
        return "검출부 정렬을 임의로 변경하지 않기"
    if any(term in lowered for term in ("안전거리", "안전 거리")):
        return "안전거리 기준을 임의로 줄이지 않기"
    if any(term in lowered for term in ("설정", "파라미터", "뮤팅", "블랭킹")):
        return "승인 없이 안전 관련 설정값을 변경하지 않기"
    if any(term in lowered for term in ("동작", "반응", "작동", "시험", "확인")):
        return "정상 동작 시험 없이 사용하지 않기"
    if any(term in lowered for term in ("손상", "고장", "오동작", "불량")):
        return "손상이나 오동작이 있으면 사용하지 않기"
    return _precaution_phrase(cleaned)


PROFILE_STRING_FIELDS = {
    "product_names",
    "model_names",
    "aliases",
    "equipment",
    "components",
    "supported_tasks",
    "safety_topics",
    "summary_points",
    "document_keywords",
}


def _document_profiles(sources: list[ChatSource]) -> list[tuple[ChatSource, dict]]:
    profiles: list[tuple[ChatSource, dict]] = []
    seen: set[int] = set()
    for source in sources:
        profile = getattr(source, "document_profile", None)
        if not isinstance(profile, dict):
            continue
        profile_id = id(profile)
        if profile_id in seen:
            continue
        profiles.append((source, profile))
        seen.add(profile_id)
    return profiles


def _profile_string_values(profile: dict, field: str) -> list[str]:
    if field not in PROFILE_STRING_FIELDS:
        return []
    raw_values = profile.get(field)
    if isinstance(raw_values, str):
        raw_values = [raw_values]
    if not isinstance(raw_values, list):
        return []
    values: list[str] = []
    for raw_value in raw_values:
        text = _clean_source_excerpt(str(raw_value or ""))
        text = text.strip(" -•*[]()")
        if not text or len(text) > 120 or _looks_like_bad_card_text(text, enforce_length=False):
            continue
        if text not in values:
            values.append(text)
    return values


def _document_profile_strings(
    sources: list[ChatSource],
    *fields: str,
    limit: int,
) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for _, profile in _document_profiles(sources):
        for field in fields:
            for raw_value in _profile_string_values(profile, field):
                value = (
                    _document_entity_phrase(raw_value, known=True)
                    if field in {"equipment", "components", "product_names"}
                    else _clean_source_excerpt(raw_value)
                )
                if not value:
                    continue
                key = re.sub(r"\s+", "", value.casefold())
                if key in seen:
                    continue
                values.append(value)
                seen.add(key)
                if len(values) >= limit:
                    return values
    return values


def _document_profile_summary_items(
    sources: list[ChatSource],
    *,
    limit: int,
) -> list[EvidenceBackedItem]:
    items: list[EvidenceBackedItem] = []
    seen: set[str] = set()
    for source, profile in _document_profiles(sources):
        for value in _profile_string_values(profile, "summary_points"):
            content = _polite_document_sentence(value)
            key = _document_sentence_key(content)
            if key in seen:
                continue
            items.append(
                EvidenceBackedItem(
                    content=content,
                    evidence_chunk_ids=[source.chunk_id] if source.chunk_id else [],
                )
            )
            seen.add(key)
            if len(items) >= limit:
                return items
    return items


def _document_overview(
    source: ChatSource,
    current: DocumentOverview,
) -> DocumentOverview:
    profile = getattr(source, "document_profile", None)
    model_names = _profile_string_values(profile, "model_names") if isinstance(profile, dict) else []
    fallback_model_name = _document_model_name_from_source(source)
    return DocumentOverview(
        filename=source.original_filename or source.title or current.filename,
        document_type=_document_type_label(source),
        manufacturer=current.manufacturer,
        model_name=current.model_name
        or (model_names[0] if model_names else None)
        or fallback_model_name,
        version=(
            str(source.document_version)
            if source.document_version is not None
            else current.version
        ),
        authored_at=current.authored_at,
    )


def _document_type_label(source: ChatSource) -> str:
    source_type = canonical_document_type(source.source_type)
    label = DOCUMENT_TYPE_LABELS.get(source_type, source_type or source.source_type)
    filename = f"{source.original_filename or ''} {source.title or ''}".casefold()
    is_pdf = filename.endswith(".pdf") or source_type in (
        MANUAL_SOURCE_TYPES | PUBLIC_REFERENCE_SOURCE_TYPES | {"company_policy"}
    )
    if is_pdf and label:
        return f"PDF / {label}"
    return label or "문서"


def _document_model_name_from_source(source: ChatSource) -> str | None:
    text = " ".join(
        value
        for value in (source.title or "", source.original_filename or "")
        if value
    )
    text = _clean_source_excerpt(text)
    if not text:
        return None
    for pattern in (
        r"([A-Z0-9][A-Za-z0-9/_-]{0,30}\s+Series(?:\s*\([^)]+\))?)",
        r"([A-Z]{1,8}(?:/[A-Z]{1,8})+\s+Series(?:\s*\([^)]+\))?)",
    ):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip(" .,_-/")
    filename = source.original_filename or ""
    stem = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", filename).strip()
    if not stem:
        return None
    parts = [
        part
        for part in re.split(r"[_\s]+", stem)
        if part and part.casefold() not in {"ko", "kr", "manual", "user", "pdf"}
    ]
    for part in parts:
        if re.search(r"[A-Z]{2,}", part) and re.search(r"\d|[-/]", part):
            return part.strip(" .,_-/")
    return None


def _document_summary_items(
    candidates: list[SentenceCandidate],
    *,
    question: str,
    limit: int = 5,
) -> list[EvidenceBackedItem]:
    if not candidates:
        return []
    selected = _candidate_subset(
        candidates,
        groups={candidate.source_group for candidate in candidates},
        terms=(),
        limit=max(12, limit * 8),
        allow_fallback=True,
        exclude_reference_title=True,
    )
    items: list[EvidenceBackedItem] = []
    seen: set[str] = set()
    for candidate in selected:
        content = _document_summary_point(candidate.content)
        if not content:
            content = _document_summary_sentence(candidate.content)
        if not content:
            continue
        if question and not _item_relevant_to_question(candidate.content, question):
            if len(items) >= 2:
                continue
        key = _document_sentence_key(content)
        if key in seen:
            continue
        items.append(
            EvidenceBackedItem(
                content=content,
                evidence_chunk_ids=[candidate.evidence_chunk_id],
            )
        )
        seen.add(key)
        if len(items) >= limit:
            break
    return items


def _document_summary_point(text: str) -> str:
    cleaned = _clean_source_excerpt(text)
    if not _has_meaningful_text(cleaned):
        return ""
    lowered = cleaned.casefold()
    if "제품 매뉴얼 반드시" in lowered or "본 문서에 기재된 제품" in lowered:
        return ""
    if _looks_like_raw_document_fragment(cleaned):
        return ""
    if any(
        term in lowered
        for term in (
            "모델",
            "형식",
            "기종",
            "사양",
            "정격",
            "치수",
            "검출 성능",
            "검출성능",
            "거리",
            "전압",
            "전류",
            "하중",
            "토크",
        )
    ):
        return "문서는 제품의 모델 구성, 주요 사양, 정격·치수 기준을 정리합니다."
    if any(term in lowered for term in ("설치", "장착", "고정", "체결", "배선", "결선")):
        return "문서는 설치·장착·배선 시 확인해야 할 조건과 기준을 안내합니다."
    if any(term in lowered for term in ("주의", "경고", "금지", "오동작", "고장", "손상", "위험", "사고")):
        return "문서는 사용 중 발생할 수 있는 오동작·손상 요인과 안전 주의사항을 안내합니다."
    if any(term in lowered for term in ("기능", "역할", "용도", "설정", "파라미터", "모니터링", "동작")):
        return "문서는 제품의 기능, 설정 항목, 동작 확인 방법을 설명합니다."
    if any(term in lowered for term in ("구성", "부품", "컴포넌트", "센서", "스위치", "장치")):
        return "문서는 대상 장치의 구성 요소와 관련 부품 정보를 설명합니다."
    return ""


def _document_summary_sentence(text: str) -> str:
    cleaned = _clean_source_excerpt(text)
    cleaned = re.sub(r"^(?:주의|경고|참고)\s*[:：]\s*", "", cleaned)
    cleaned = cleaned.strip(" -•*[]()")
    if not _has_meaningful_text(cleaned) or _looks_like_meta_item(cleaned):
        return ""
    if re.fullmatch(r"[0-9A-Za-z가-힣□/()+_.\-\s]{2,50}\s*문서(?:입니다|임)?\.?", cleaned):
        return ""
    if _looks_like_raw_document_fragment(cleaned):
        return ""
    if len(cleaned) > 150:
        cleaned = cleaned[:150].rsplit(" ", 1)[0].rstrip(" ,.;:·-")
    return _polite_document_sentence(cleaned)


def _looks_like_raw_document_fragment(text: str) -> bool:
    normalized = _clean_source_excerpt(text).casefold()
    if len(normalized) > 180:
        return True
    return any(
        marker in normalized
        for marker in (
            "항목 항목",
            "①",
            "②",
            "③",
            "④",
            "⑤",
            "⑥",
            "electronic protective device",
            "검입니다",
            "제품 매뉴얼 반드시",
            "본 문서에 기재된 제품",
            "예고없이 변경",
            "일부 모델은 단종",
        )
    )


def _polite_document_sentence(text: str) -> str:
    content = _clean_source_excerpt(text).strip(" .")
    if not content:
        return ""
    replacements = (
        (r"하여야\s*한다$", "해야 합니다"),
        (r"해야\s*한다$", "해야 합니다"),
        (r"되어야\s*한다$", "되어야 합니다"),
        (r"할\s*수\s*있다$", "할 수 있습니다"),
        (r"할\s*수\s*없다$", "할 수 없습니다"),
        (r"해야\s*함$", "해야 합니다"),
        (r"하여야\s*함$", "해야 합니다"),
        (r"한다$", "합니다"),
        (r"같다$", "같습니다"),
        (r"따른다$", "따릅니다"),
        (r"갖춘다$", "갖춥니다"),
        (r"된다$", "됩니다"),
        (r"있다$", "있습니다"),
        (r"없다$", "없습니다"),
        (r"이다$", "입니다"),
        (r"임$", "입니다"),
        (r"함$", "합니다"),
    )
    if re.search(r"(니다|습니다|입니다|합니다|됩니다|주세요|십시오|하세요)$", content):
        return f"{content}."
    for pattern, replacement in replacements:
        updated = re.sub(pattern, replacement, content)
        if updated != content:
            return f"{updated}."
    if content.endswith(("포함", "설명", "제시", "제공")):
        return f"{content}합니다."
    return f"{content}입니다."


def _document_sentence_key(sentence: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", sentence.casefold())[:90]


def _document_related_entities(
    sources: list[ChatSource],
    *,
    existing: Iterable[str],
    terms: tuple[str, ...],
    generic_kind: str,
    limit: int,
) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()

    def add(raw: str, *, known: bool = False) -> None:
        if len(values) >= limit:
            return
        value = _document_entity_phrase(raw, known=known)
        if not value:
            return
        key = re.sub(r"\s+", "", value.casefold())
        if key in seen:
            return
        if value == "로봇" and "산업용로봇" in seen:
            return
        if value == "산업용 로봇" and "로봇" in seen:
            values[:] = [existing for existing in values if existing != "로봇"]
            seen.discard("로봇")
        if value == "방호장치" and any(
            existing.endswith("방호장치") and existing != "방호장치"
            for existing in values
        ):
            return
        if value.endswith("방호장치") and value != "방호장치" and "방호장치" in seen:
            values[:] = [existing for existing in values if existing != "방호장치"]
            seen.discard("방호장치")
        if not known and not _document_phrase_in_sources(value, sources):
            return
        values.append(value)
        seen.add(key)

    for source in sources:
        text = _document_source_text(source)
        lowered = text.casefold()
        for term in terms:
            if term.casefold() in lowered:
                add(term, known=True)
        for candidate in _document_generic_entity_candidates(
            text,
            kind=generic_kind,
        ):
            add(candidate)
    for item in existing:
        add(str(item))
    return values


def _document_source_text(source: ChatSource) -> str:
    profile = getattr(source, "document_profile", None)
    profile_text = ""
    if isinstance(profile, dict):
        profile_parts: list[str] = []
        for field in PROFILE_STRING_FIELDS:
            values = profile.get(field)
            if isinstance(values, list):
                profile_parts.extend(str(value) for value in values if value)
            elif isinstance(values, str):
                profile_parts.append(values)
        profile_text = " ".join(profile_parts)
    return _clean_source_excerpt(
        " ".join(
            part
            for part in (
                source.original_filename or "",
                source.title or "",
                source.section or "",
                profile_text,
                source.excerpt or "",
            )
            if part
        )
    )


def _document_generic_entity_candidates(text: str, *, kind: str) -> list[str]:
    candidates: list[str] = []
    suffixes = EQUIPMENT_ENTITY_SUFFIXES if kind == "equipment" else COMPONENT_ENTITY_SUFFIXES
    suffix_pattern = "|".join(re.escape(suffix) for suffix in suffixes)
    patterns = (
        rf"([0-9A-Za-z가-힣□·/()+_-]+(?:\s+[0-9A-Za-z가-힣□·/()+_-]+){{0,4}}\s*(?:{suffix_pattern}))",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            candidates.append(match.group(1))
    return candidates


def _document_entity_phrase(raw: str, *, known: bool = False) -> str:
    text = _clean_source_excerpt(raw)
    text = re.sub(r"\([^)]{1,40}\)", "", text)
    text = re.sub(r"\.(?:pdf|PDF)$", "", text)
    text = text.strip(" .,:;·-/[]()")
    text = re.sub(r"^(?:pdf|PDF)\s+", "", text)
    text = re.sub(r"\b(?:KCs|KC|S-mark|S\s*mark)\b\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^[A-Za-z0-9]{1,2}\s+(?=[가-힣])", "", text)
    text = re.sub(r"^(?:은|는|이|가|을|를|와|과)\s+", "", text)
    text = re.sub(
        r"^[0-9A-Za-z가-힣□·/()+_-]+(?:\s+[0-9A-Za-z가-힣□·/()+_-]+){0,2}"
        r"(?:은|는|이|가)\s+",
        "",
        text,
    )
    text = re.sub(r"^(?:관련|주요|문서 내)\s+", "", text)
    if len(text) > 32:
        nested = _document_generic_entity_candidates(text, kind="component")
        nested.extend(_document_generic_entity_candidates(text, kind="equipment"))
        nested = sorted(nested, key=lambda value: (len(value), value))
        for candidate in nested:
            normalized = _document_entity_phrase(candidate, known=known)
            if normalized:
                text = normalized
                break
    if not text or len(text) > 32:
        return ""
    parts = text.split()
    if len(parts) >= 2 and len(parts) % 2 == 0:
        half = len(parts) // 2
        if parts[:half] == parts[half:]:
            text = " ".join(parts[:half])
    parts = text.split()
    if len(parts) >= 2 and parts[-1] == parts[-2]:
        text = " ".join(parts[:-1])
    if not re.search(r"[A-Za-z가-힣]", text):
        return ""
    if any(term in text for term in DOCUMENT_ENTITY_FRAGMENT_REJECT_TERMS):
        return ""
    if not known and any(term in text for term in DOCUMENT_ENTITY_REJECT_TERMS):
        return ""
    if text in GENERIC_ENTITY_VALUES:
        return ""
    tokens = re.findall(r"[0-9A-Za-z가-힣]+", text)
    if not known and len(tokens) > 1 and tokens[-1] in {"기계", "장비", "제품", "설비", "라인"}:
        return ""
    return text


def _document_phrase_in_sources(phrase: str, sources: list[ChatSource]) -> bool:
    phrase_key = re.sub(r"\s+", "", phrase.casefold())
    if not phrase_key:
        return False
    return any(
        phrase_key in re.sub(r"\s+", "", _document_source_text(source).casefold())
        for source in sources
    )


def _document_supported_tasks(
    candidates: list[SentenceCandidate],
    *,
    existing: Iterable[str],
    limit: int,
) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        if len(values) >= limit:
            return
        value = _document_task_phrase(raw)
        if not value:
            return
        key = _maintenance_semantic_key(value)
        if key in seen:
            return
        values.append(value)
        seen.add(key)

    if candidates:
        selected = _candidate_subset(
            candidates,
            groups={candidate.source_group for candidate in candidates},
            terms=ACTION_TERMS + PRECHECK_TERMS,
            limit=max(limit * 4, 16),
            allow_fallback=False,
            require_action=True,
            exclude_reference_title=True,
        )
        for candidate in selected:
            add(candidate.content)
    for item in existing:
        add(str(item))
    return values


def _document_task_phrase(text: str) -> str:
    cleaned = _clean_source_excerpt(text)
    if not _has_meaningful_text(cleaned):
        return ""
    lowered = cleaned.casefold()
    key = _maintenance_semantic_key(cleaned)
    if key == "metal_clearance":
        return "주변 금속·장애물 이격거리 확인"
    if key == "interference":
        return "인접 장치 간 간섭거리 확인"
    if key == "detection_distance":
        return "검출거리와 설정거리 확인"
    if key == "mounting":
        return "장착 위치와 고정 상태 확인"
    if key == "contamination_damage":
        return "오염·손상 및 보호 조치 확인"
    if key == "wiring_power":
        return "정격·전원·배선 조건 확인"
    if key == "safety_distance":
        return "안전거리 기준 확인"
    if key == "alignment":
        return "검출부 정렬 상태 확인"
    if key == "detection_response" and any(
        term in lowered for term in ("차단", "반응", "정지", "동작", "검출", "멈추")
    ):
        return "검출·차단 시 설비 반응 시험"
    if key == "lockout":
        return "전원 차단 및 잠금 확인"
    if key == "emergency_stop":
        return "비상정지장치 접근·작동 확인"
    if key == "danger_zone":
        return "방호구역 내 작업자 유무 확인"
    if key == "guarding":
        return "방호장치 설치 조건 확인"
    if key in {"cleaning", "machine_stop"}:
        return "운전 정지 및 재가동 방지 확인"
    if key == "settings":
        return "설정값과 기능 적용 상태 확인"
    if key == "mechanical_rotation":
        return "회전부 고정·윤활 상태 확인"
    if "pc" in lowered and ("설정" in lowered or "툴" in lowered):
        return "PC 설정 툴 설정값 확인"
    if "설정" in lowered and "동작" in lowered:
        return "기능 설정 후 정상 동작 확인"
    if "설치" in lowered and "점검" in lowered:
        return "설치 및 점검 항목 확인"
    content = _final_card_phrase(cleaned)
    if not content or not any(term in cleaned for term in ACTION_TERMS + PRECHECK_TERMS):
        return ""
    content = content.rstrip(" .")
    if content.endswith(("확인", "점검", "검사", "시험", "측정", "정렬")):
        return content
    if content.endswith(("설치", "교체", "청소", "정비", "보수", "설정")):
        return f"{content} 확인"
    return ""


def _document_unverified_information(
    existing: Iterable[str],
    *,
    has_supported_tasks: bool,
) -> list[str]:
    values: list[str] = []
    for item in existing:
        text = _clean_source_excerpt(str(item))
        if text and text not in values:
            values.append(text)
    if "검색된 부분 밖의 문서 전문은 확인하지 못했습니다." not in values:
        values.append("검색된 부분 밖의 문서 전문은 확인하지 못했습니다.")
    if not has_supported_tasks:
        values.append("검색 근거에서 확인 가능한 작업 항목은 찾지 못했습니다.")
    return values


def _maintenance_pre_check_items(
    candidates: list[SentenceCandidate],
    *,
    question: str,
    limit: int,
) -> list[EvidenceBackedItem]:
    manual_selected = _candidate_subset(
        candidates,
        groups={PDF_SOURCE_GROUP},
        terms=PRECHECK_TERMS
        + ACTION_TERMS
        + STOP_TERMS
        + (
            "안전 거리",
            "안전거리",
            "검출 영역",
            "검출영역",
            "투광기",
            "수광기",
            "광축",
            "상호 간섭",
            "상호간섭",
            "차광판",
            "외란광",
            "반사광",
            "반사면",
            "제품 버전",
        )
        + TECHNICAL_PRECHECK_TERMS,
        limit=max(limit * 6, 24),
        allow_fallback=False,
        exclude_risk_only=True,
        exclude_reference_title=True,
    )
    manual_items = _formatted_maintenance_items(
        manual_selected,
        formatter=_pre_check_phrase,
        question=question,
        enforce_question_relevance=False,
        dedupe_semantic_keys=False,
        limit=limit * 2,
    )
    source_group_by_id = {
        candidate.evidence_chunk_id: candidate.source_group for candidate in candidates
    }
    manual_items.sort(
        key=lambda item: _pre_check_sort_key(
            item,
            question=question,
            source_group_by_id=source_group_by_id,
        )
    )
    return _dedupe_maintenance_items(
        manual_items,
        validator=_is_maintenance_pre_check_phrase,
        dedupe_semantic_keys=False,
        limit=limit,
    )


def _maintenance_manual_step_items(
    candidates: list[SentenceCandidate],
    *,
    question: str,
    limit: int,
) -> list[EvidenceBackedItem]:
    selected = _candidate_subset(
        candidates,
        groups={PDF_SOURCE_GROUP},
        terms=ACTION_TERMS
        + PRECHECK_TERMS
        + HAZARD_TERMS
        + (
            "안전 거리",
            "안전거리",
            "검출 영역",
            "검출영역",
            "투광기",
            "수광기",
            "광축",
            "상호 간섭",
            "상호간섭",
            "차광판",
            "외란광",
            "반사광",
            "반사면",
            "베어링",
            "하우징",
            "윤활",
            "축 정렬",
            "이상음",
            "과열",
            "장력",
            "체결 토크",
        ),
        limit=max(limit * 8, 36),
        allow_fallback=True,
        require_action=False,
        exclude_risk_only=True,
        exclude_reference_title=True,
    )
    return _formatted_maintenance_items(
        selected,
        formatter=_manual_step_phrase,
        question=question,
        enforce_question_relevance=False,
        dedupe_semantic_keys=False,
        limit=limit,
    )


def _manual_step_phrase(text: str) -> str:
    cleaned = _clean_source_excerpt(text)
    if not _has_meaningful_text(cleaned):
        return ""
    lowered = cleaned.casefold()

    if "베어링" in lowered and any(term in lowered for term in ("축", "하우징")) and any(
        term in lowered for term in ("손상", "치수")
    ):
        return "베어링 설치 전 축·하우징의 손상과 치수를 확인합니다."
    if "윤활" in lowered:
        return "지정된 윤활제의 종류와 주입량을 확인합니다."
    if ("베어링" in lowered or "축" in lowered) and "정렬" in lowered:
        return "축·베어링의 정렬 상태를 확인합니다."
    if "베어링" in lowered and any(term in lowered for term in ("이상음", "과열")):
        return "조립 후 시운전하여 베어링의 이상음·과열 여부를 확인합니다."
    if any(term in lowered for term in ("전원 차단", "전원을 차단", "잠금", "lockout")):
        return "작업 전 전원을 차단하고 잠금·표지를 적용합니다."
    if "안전 거리" in lowered or "안전거리" in lowered:
        return "기계 위험부와 라이트커튼 사이의 안전거리를 확보합니다."
    if (
        ("검출 영역" in lowered or "검출영역" in lowered)
        and "통과" in lowered
    ):
        return "위험부 접근 시 반드시 라이트커튼 검출영역을 통과하도록 설치합니다."
    if (
        ("검출 영역" in lowered or "검출영역" in lowered)
        and ("별도의 가드" in lowered or "가드" in lowered)
    ):
        return "검출영역을 우회할 수 있으면 별도 가드를 설치합니다."
    if (
        any(term in lowered for term in ("투광기", "수광기", "투/수광기"))
        and "광축 표시등" in lowered
    ):
        return "투광기·수광기의 상·하단 광축 표시등을 정확히 맞춥니다."
    if (
        any(term in lowered for term in ("투광기", "수광기", "투/수광기"))
        and any(term in lowered for term in ("벽면", "반사면"))
    ):
        return "투광기·수광기를 벽면·반사면의 영향을 받지 않는 위치에 설치합니다."
    if (
        any(term in lowered for term in ("여러 세트", "복수", "다수"))
        and ("상호 간섭" in lowered or "상호간섭" in lowered)
    ):
        return "여러 세트 설치 시 상호간섭을 방지하거나 차광판을 사용합니다."
    if (
        any(term in lowered for term in ("외란광", "직사광선", "스포트라이트", "반사광"))
        and ("수광기" in lowered or "설치" in lowered)
    ):
        return "외란광·반사광이 수광기에 직접 입사하지 않도록 설치합니다."
    if (
        any(term in lowered for term in ("반사형", "회귀 반사형", "회귀반사형"))
        and any(term in lowered for term in ("사용하지", "배치"))
    ):
        return "반사형 또는 회귀반사형 배치로 사용하지 않습니다."

    content = _compact_phrase(cleaned, max_chars=72)
    if not _looks_like_actionable_manual_step(content):
        return ""
    return content


def _pre_check_sort_key(
    item: EvidenceBackedItem,
    *,
    question: str,
    source_group_by_id: dict[str, str],
) -> tuple[int, int]:
    source_rank = 1
    if any(source_group_by_id.get(chunk_id) == PDF_SOURCE_GROUP for chunk_id in item.evidence_chunk_ids):
        source_rank = 0
    key = _maintenance_semantic_key(item.content)
    question_text = question.casefold()
    wiring_focused = any(
        term in question_text
        for term in ("dc", "ac", "전원", "전압", "전류", "배선", "결선", "선식", "케이블")
    )
    if wiring_focused:
        key_order = {
            "wiring_power": 0,
            "mounting": 1,
            "metal_clearance": 2,
            "detection_distance": 3,
            "safety_distance": 4,
        }
    else:
        key_order = {
            "safety_distance": 0,
            "danger_zone": 1,
            "guarding": 1,
            "detection_response": 2,
            "alignment": 3,
            "interference": 4,
            "mounting": 5,
            "contamination_damage": 6,
            "wiring_power": 7,
            "metal_clearance": 8,
        }
    return (source_rank, key_order.get(key, 9))


def _maintenance_precaution_items(
    candidates: list[SentenceCandidate],
    *,
    question: str,
    excluded_keys: set[str] | None = None,
    limit: int,
) -> list[EvidenceBackedItem]:
    selected = _candidate_subset(
        candidates,
        groups={PDF_SOURCE_GROUP, PUBLIC_SAFETY_SOURCE_GROUP, COMPANY_SOURCE_GROUP},
        terms=ACTION_TERMS + HAZARD_TERMS + PRECHECK_TERMS + STOP_TERMS,
        limit=max(limit * 6, 24),
        allow_fallback=True,
        exclude_reference_title=True,
    )
    return _formatted_maintenance_items(
        selected,
        formatter=_precaution_phrase,
        question=question,
        excluded_keys=excluded_keys,
        fallback_to_excluded=True,
        min_items=min(2, limit),
        limit=limit,
    )


def _maintenance_stop_condition_items(
    candidates: list[SentenceCandidate],
    *,
    question: str,
    excluded_keys: set[str] | None = None,
    fallback_items: Iterable[EvidenceBackedItem],
    limit: int,
) -> list[EvidenceBackedItem]:
    selected = _candidate_subset(
        candidates,
        groups={PDF_SOURCE_GROUP, PUBLIC_SAFETY_SOURCE_GROUP, COMPANY_SOURCE_GROUP},
        terms=STOP_TERMS + HAZARD_TERMS + PRECHECK_TERMS + ACTION_TERMS,
        limit=max(limit * 6, 24),
        allow_fallback=True,
        exclude_reference_title=True,
    )
    items = _formatted_maintenance_items(
        selected,
        formatter=_stop_condition_phrase,
        question=question,
        excluded_keys=excluded_keys,
        fallback_to_excluded=True,
        min_items=min(3, limit),
        limit=limit,
    )
    if len(items) >= min(limit, 3):
        return items
    existing_keys = {_maintenance_semantic_key(item.content) for item in items}
    for fallback_item in fallback_items:
        content = _stop_condition_phrase(fallback_item.content)
        if not content:
            continue
        key = _maintenance_semantic_key(f"{fallback_item.content} {content}")
        if key in existing_keys:
            continue
        items.append(
            EvidenceBackedItem(
                content=content,
                evidence_chunk_ids=fallback_item.evidence_chunk_ids,
            )
        )
        existing_keys.add(key)
        if len(items) >= limit:
            break
    return items


def _formatted_maintenance_items(
    candidates: Iterable[SentenceCandidate],
    *,
    formatter: Callable[[str], str],
    question: str,
    enforce_question_relevance: bool = True,
    dedupe_semantic_keys: bool = True,
    excluded_keys: set[str] | None = None,
    fallback_to_excluded: bool = False,
    min_items: int = 0,
    limit: int,
) -> list[EvidenceBackedItem]:
    items: list[EvidenceBackedItem] = []
    seen_phrases: set[str] = set()
    seen_keys: set[str] = set()
    excluded = excluded_keys or set()
    candidate_list = list(candidates)

    def collect(*, allow_excluded: bool) -> None:
        for candidate in candidate_list:
            if len(items) >= limit:
                return
            if (
                enforce_question_relevance
                and question
                and not _item_relevant_to_question(candidate.content, question)
            ):
                continue
            content = formatter(candidate.content)
            if not content or not _has_meaningful_text(content):
                continue
            key = _maintenance_semantic_key(f"{candidate.content} {content}")
            if key in excluded and not allow_excluded:
                continue
            if (dedupe_semantic_keys and key in seen_keys) or content in seen_phrases:
                continue
            items.append(
                EvidenceBackedItem(
                    content=content,
                    evidence_chunk_ids=[candidate.evidence_chunk_id],
                )
            )
            seen_phrases.add(content)
            if dedupe_semantic_keys:
                seen_keys.add(key)

    collect(allow_excluded=False)
    if fallback_to_excluded and len(items) < min_items:
        collect(allow_excluded=True)
    return items


def _maintenance_item_keys(items: Iterable[EvidenceBackedItem]) -> set[str]:
    return {_maintenance_semantic_key(item.content) for item in items if item.content}


def _chat_checklist_items_from_evidence(
    evidence_items: Iterable[EvidenceBackedItem],
    *,
    excluded_keys: set[str],
    sequence_start: int,
    seen_phrases: set[str],
    seen_keys: set[str],
    allow_excluded: bool,
    limit: int,
) -> list[ChatChecklistItem]:
    items: list[ChatChecklistItem] = []
    for evidence_item in evidence_items:
        if len(items) + sequence_start - 1 >= limit:
            break
        content = _checklist_content_from_pre_check(evidence_item.content)
        if not content:
            continue
        source_text = f"{evidence_item.content} {content}"
        key = _tbm_checklist_key(source_text)
        semantic_key = _maintenance_semantic_key(source_text)
        if (
            key in excluded_keys or semantic_key in excluded_keys
        ) and not allow_excluded:
            continue
        if key in seen_keys or content in seen_phrases:
            continue
        items.append(
            ChatChecklistItem(
                content=content,
                sequence=sequence_start + len(items),
                evidence_chunk_ids=evidence_item.evidence_chunk_ids,
            )
        )
        seen_phrases.add(content)
        seen_keys.add(key)
    return items


def _tbm_checklist_key(text: str) -> str:
    lowered = _clean_source_excerpt(text).casefold()
    if "베어링" in lowered and any(term in lowered for term in ("손상", "치수", "하우징")):
        return "tbm:bearing_fit"
    if "윤활" in lowered:
        return "tbm:lubrication"
    if ("베어링" in lowered or "축" in lowered) and "정렬" in lowered:
        return "tbm:bearing_alignment"
    if "베어링" in lowered and any(term in lowered for term in ("이상음", "과열")):
        return "tbm:bearing_condition"
    if any(term in lowered for term in ("전원 차단", "전원을 차단", "잠금", "lockout")):
        return "tbm:lockout"
    if "안전거리" in lowered or "안전 거리" in lowered:
        return "tbm:safety_distance"
    if "광축 표시등" in lowered or (
        "투광기" in lowered and "수광기" in lowered and "정렬" in lowered
    ):
        return "tbm:optical_alignment"
    if "상호간섭" in lowered or "상호 간섭" in lowered or "차광판" in lowered:
        return "tbm:mutual_interference"
    if any(term in lowered for term in ("외란광", "직사광선", "스포트라이트", "반사광")):
        return "tbm:external_light"
    if "반사면" in lowered or "회귀반사형" in lowered or "회귀 반사형" in lowered:
        return "tbm:reflection"
    if ("검출영역" in lowered or "검출 영역" in lowered) and any(
        term in lowered for term in ("우회", "통과", "가드")
    ):
        return "tbm:detection_access"
    return f"tbm:{_maintenance_semantic_key(text)}"


def _normalize_maintenance_items(
    items: Iterable[EvidenceBackedItem],
    *,
    formatter: Callable[[str], str],
    excluded_keys: set[str] | None = None,
    fallback_to_excluded: bool = False,
    min_items: int = 0,
    limit: int,
) -> list[EvidenceBackedItem]:
    normalized: list[EvidenceBackedItem] = []
    seen_phrases: set[str] = set()
    seen_keys: set[str] = set()
    excluded = excluded_keys or set()
    item_list = list(items)

    def collect(*, allow_excluded: bool) -> None:
        for item in item_list:
            if len(normalized) >= limit:
                return
            content = formatter(item.content)
            if not content or not _has_meaningful_text(content):
                continue
            key = _maintenance_semantic_key(f"{item.content} {content}")
            if key in excluded and not allow_excluded:
                continue
            if key in seen_keys or content in seen_phrases:
                continue
            normalized.append(item.model_copy(update={"content": content}))
            seen_phrases.add(content)
            seen_keys.add(key)

    collect(allow_excluded=False)
    if fallback_to_excluded and len(normalized) < min_items:
        collect(allow_excluded=True)
    return normalized


def _normalize_maintenance_hazards(
    items: Iterable[EvidenceBackedItem],
    *,
    excluded_keys: set[str] | None = None,
    fallback_to_excluded: bool = False,
    min_items: int = 0,
    limit: int,
) -> list[MaintenanceHazard]:
    hazards: list[MaintenanceHazard] = []
    seen_phrases: set[str] = set()
    seen_keys: set[str] = set()
    excluded = excluded_keys or set()
    item_list = list(items)

    def collect(*, allow_excluded: bool) -> None:
        for item in item_list:
            if len(hazards) >= limit:
                return
            content = _hazard_phrase(item.content)
            if not content or not _has_meaningful_text(content):
                continue
            key = _maintenance_semantic_key(f"{item.content} {content}")
            if key in excluded and not allow_excluded:
                continue
            if key in seen_keys or content in seen_phrases:
                continue
            hazards.append(
                MaintenanceHazard(
                    name=_maintenance_hazard_name(item, key, content),
                    content=content,
                    evidence_chunk_ids=item.evidence_chunk_ids,
                )
            )
            seen_phrases.add(content)
            seen_keys.add(key)

    collect(allow_excluded=False)
    if fallback_to_excluded and len(hazards) < min_items:
        collect(allow_excluded=True)
    return hazards


def _dedupe_maintenance_items(
    items: Iterable[EvidenceBackedItem],
    *,
    validator: Callable[[str], bool] | None = None,
    dedupe_semantic_keys: bool = True,
    limit: int,
) -> list[EvidenceBackedItem]:
    deduped: list[EvidenceBackedItem] = []
    seen_phrases: set[str] = set()
    seen_keys: set[str] = set()
    for item in items:
        content = " ".join(item.content.split())
        if not content:
            continue
        if validator is not None and not validator(content):
            continue
        key = _maintenance_semantic_key(content)
        if (dedupe_semantic_keys and key in seen_keys) or content in seen_phrases:
            continue
        deduped.append(item.model_copy(update={"content": content}))
        seen_phrases.add(content)
        if dedupe_semantic_keys:
            seen_keys.add(key)
        if len(deduped) >= limit:
            break
    return deduped


def _prioritize_maintenance_items(
    items: list[EvidenceBackedItem],
) -> list[EvidenceBackedItem]:
    return [
        item
        for _, item in sorted(
            enumerate(items),
            key=lambda pair: (_maintenance_item_priority(pair[1].content), pair[0]),
        )
    ]


def _maintenance_item_priority(content: str) -> int:
    text = content.casefold()
    if "광축" in text:
        return 2
    if "안전거리" in text:
        return 0
    if any(
        term in text
        for term in (
            "전원",
            "차단",
            "잠금",
            "재가동",
            "운전 정지",
            "기계 정지",
            "청소 전 정지",
        )
    ):
        return 0
    if any(term in text for term in ("반응", "광축", "비상정지", "비상 정지")):
        return 1
    if any(term in text for term in ("방호", "보호", "접근방지", "덮개")):
        return 2
    if any(term in text for term in ("정기", "일반", "상태 점검")):
        return 8
    return 4


def _is_maintenance_pre_check_phrase(content: str) -> bool:
    text = content.casefold()
    if _looks_like_reference_only_phrase(text):
        return False
    return any(
        marker.casefold() in text
        for marker in PRECHECK_TERMS
        + TECHNICAL_PRECHECK_TERMS
        + (
            "안전 거리",
            "검출 영역",
            "검출영역",
            "가드",
            "차광판",
            "외란광",
            "반사광",
            "제품 버전",
            "확인",
            "점검",
            "검사",
            "시험",
            "기준",
            "상태",
            "차단",
            "정지",
            "잠금",
            "격리",
            "방호",
            "보호",
            "안전거리",
            "광축",
            "비상정지",
            "작업자",
            "교육",
        )
    )


def _looks_like_reference_only_phrase(text: str) -> bool:
    return any(
        marker in text
        for marker in (
            "포스터",
            "사례",
            "지침",
            "저장조의",
            "공급 장치의 운전",
        )
    )


def _maintenance_semantic_key(content: str) -> str:
    text = _clean_source_excerpt(content).casefold()
    compact = re.sub(r"\s+", "", text)
    if any(term in text for term in ("비상정지", "비상 정지", "emergency stop")):
        return "emergency_stop"
    if any(term in text for term in ("안전거리", "안전 거리", "safety distance")):
        return "safety_distance"
    if (
        any(term in text for term in ("주위금속", "주위 금속", "주변 금속"))
        or ("금속" in text and any(term in text for term in ("이격", "거리", "간격")))
    ):
        return "metal_clearance"
    if any(term in text for term in ("대향", "병렬", "주파수 간섭", "간섭", "노이즈", "서지")):
        return "interference"
    if any(
        term in text
        for term in (
            "검출 거리",
            "검출거리",
            "설정 거리",
            "설정거리",
            "검출면",
            "검출체",
            "감지 거리",
            "감지거리",
            "동작 거리",
            "동작거리",
        )
    ):
        return "detection_distance"
    if any(term in text for term in ("고정 브라켓", "고정 hole", "고정 홀", "브라켓", "고정", "체결", "장착", "토크")):
        return "mounting"
    if any(term in text for term in ("스패터", "spatter", "오염", "이물", "먼지", "손상", "파손")):
        return "contamination_damage"
    if any(term in text for term in ("정격", "전원", "전압", "전류", "배선", "결선", "케이블", "전선", "접지")):
        return "wiring_power"
    if any(term in text for term in ("투광부", "수광부", "투광기", "수광기", "정렬")):
        return "alignment"
    if any(term in text for term in ("광축", "빔", "반응", "검출성능", "osdd", "ossd")):
        return "detection_response"
    if any(term in text for term in ("검출", "감지", "차단")) and any(
        term in text for term in ("반응", "동작", "정지", "시험", "확인")
    ):
        return "detection_response"
    if any(term in text for term in ("설정", "설정값", "파라미터", "모드", "뮤팅", "블랭킹")):
        return "settings"
    if any(term in text for term in ("회전", "회전축", "베어링", "윤활", "마찰", "하중")):
        return "mechanical_rotation"
    if any(term in text for term in ("청소", "세척")) and any(
        term in text for term in ("운전", "정지", "재가동")
    ):
        return "machine_stop"
    if any(
        term in text
        for term in ("운전 정지", "운전을 정지", "기계 정지", "정지 확인", "작동하지 않도록")
    ):
        return "machine_stop"
    if any(term in text for term in ("전원", "잠금", "격리", "lockout", "tagout")):
        return "lockout"
    if any(
        term in text
        for term in ("위험구역", "위험 구역", "방호구역", "방호 구역", "작업자", "재기동")
    ):
        return "danger_zone"
    if any(
        term in text
        for term in ("방호장치", "보호장치", "방호덮개", "접근방지", "안전덮개", "방호")
    ):
        return "guarding"
    if any(term in text for term in ("청소", "세척")):
        return "cleaning"
    if any(term in text for term in ("작업표준", "취급요령", "교육", "자격")):
        return "training"
    if any(term in text for term in ("정기", "정비", "보수")):
        return "maintenance_status"
    tokens = re.findall(r"[0-9A-Za-z가-힣]+", compact)
    useful = [token for token in tokens if token not in RELEVANCE_STOPWORDS]
    return " ".join(useful[:3]) or compact[:24]


def _pre_check_phrase(text: str) -> str:
    cleaned = _clean_source_excerpt(text)
    if not _has_meaningful_text(cleaned):
        return ""
    lowered = cleaned.casefold()
    if any(
        term in lowered
        for term in (
            "설치 후",
            "교체 후",
            "조립 후",
            "정비 후",
            "작업 완료 후",
            "시운전 후",
        )
    ):
        return ""
    if not any(
        term.casefold() in lowered
        for term in PRECHECK_TERMS
        + TECHNICAL_PRECHECK_TERMS
        + (
            "안전 거리",
            "검출 영역",
            "검출영역",
            "가드",
            "차광판",
            "외란광",
            "반사광",
            "제품 버전",
            "여부",
            "상태",
            "조건",
            "기준",
            "일치",
            "손상",
            "오염",
            "누설",
            "이상",
        )
    ):
        return ""
    if not any(
        term in lowered
        for term in ACTION_TERMS
        + (
            "확인",
            "점검",
            "검사",
            "시험",
            "측정",
            "정렬",
            "확보",
            "일치",
            "방지",
            "적용",
            "준수",
            "이어야",
            "해야",
            "하지 않",
            "않도록",
            "금지",
            "하십시오",
            "하세요",
        )
    ):
        return ""

    sentence = re.split(r"(?<=[.!?。])\s+", cleaned, maxsplit=1)[0]
    sentence = re.sub(
        r"(?:반드시\s*)?(?:확인|점검|검사|시험|측정)하"
        r"(?:십시오|세요|여야 합니다|도록 합니다|기 바랍니다|여야 한다)[.]?$",
        lambda match: {
            "확인": "확인",
            "점검": "점검",
            "검사": "검사",
            "시험": "시험",
            "측정": "측정",
        }.get(
            re.search(r"(확인|점검|검사|시험|측정)", match.group(0)).group(1),
            "확인",
        ),
        sentence,
    )
    sentence = sentence.rstrip(" .。")
    content = _compact_phrase(sentence, max_chars=72)
    if not content or _looks_like_bad_card_text(content, enforce_length=False):
        return ""
    return content


def _hazard_phrase(text: str) -> str:
    cleaned = _clean_source_excerpt(text)
    if not _has_meaningful_text(cleaned):
        return ""
    lowered = cleaned.casefold()
    key = _maintenance_semantic_key(cleaned)
    phrase = _hazard_phrase_from_key(key)
    if phrase:
        return phrase
    if any(term in lowered for term in ("정상 동작", "동작 여부")) and any(
        term in lowered for term in ("확인", "시험", "점검")
    ):
        return "정상 동작 미확인으로 인한 오동작 위험"
    if any(term in lowered for term in ("검출 성능", "감지 성능", "성능 저하")):
        return "검출 성능 저하로 인한 보호 실패 위험"
    if "협착" in lowered:
        return "협착 사고 위험"
    if "끼임" in lowered:
        return "끼임 사고 위험"
    if "감전" in lowered:
        return "감전 위험"
    if "화재" in lowered:
        return "화재 위험"
    if "폭발" in lowered:
        return "폭발 위험"
    if "추락" in lowered:
        return "추락 위험"
    if "낙하" in lowered:
        return "낙하물 충돌 위험"
    if "오동작" in lowered:
        return "오동작으로 인한 비정상 기동 위험"
    if "손상" in lowered:
        return "장치 손상으로 인한 안전기능 저하 위험"
    if "부상" in lowered or "인사사고" in lowered:
        return "작업자 부상 위험"
    if "무효" in lowered or "우회" in lowered or "해제" in lowered:
        return "안전기능 무효화로 인한 보호 실패 위험"
    if any(term in lowered for term in ("위험", "warning", "caution", "danger")):
        content = _final_card_phrase(cleaned)
        if not content or content == "위험요인 확인":
            return ""
        content = content.rstrip(" .")
        content = re.sub(r"(확인|점검|검사|시험)$", "미흡", content).strip()
        if content.endswith("위험"):
            return content
        return f"{content} 위험"
    return ""


def _hazard_phrase_from_key(key: str) -> str:
    if key == "metal_clearance":
        return "주변 금속·장애물 영향으로 인한 오동작 위험"
    if key == "interference":
        return "인접 장치 간 간섭으로 인한 오동작 위험"
    if key == "detection_distance":
        return "검출거리 설정 미흡으로 인한 미검출·오검출 위험"
    if key == "mounting":
        return "고정 불량으로 인한 위치 틀어짐 위험"
    if key == "contamination_damage":
        return "오염·손상으로 인한 오동작 또는 기능 저하 위험"
    if key == "wiring_power":
        return "정격·전원·배선 오류로 인한 오동작 또는 손상 위험"
    if key == "safety_distance":
        return "안전거리 미확보로 인한 위험구역 접근 위험"
    if key == "alignment":
        return "정렬 불량으로 인한 오검출 위험"
    if key == "detection_response":
        return "검출 실패 또는 위험 동작 정지 실패 위험"
    if key == "lockout":
        return "전원 차단/잠금 미흡으로 인한 불시기동 위험"
    if key == "emergency_stop":
        return "비상정지 미작동으로 인한 긴급 정지 실패 위험"
    if key == "danger_zone":
        return "방호구역 내 작업자 노출 위험"
    if key == "guarding":
        return "방호장치 우회로 인한 위험구역 접근 위험"
    if key in {"cleaning", "machine_stop"}:
        return "운전 정지 미확보로 인한 끼임 위험"
    if key == "settings":
        return "설정값 오류로 인한 안전기능 실패 위험"
    if key == "mechanical_rotation":
        return "회전부 고정·윤활 불량으로 인한 손상 위험"
    return ""


def _hazard_name_from_key(key: str) -> str:
    if key == "metal_clearance":
        return "주변 영향"
    if key == "interference":
        return "간섭"
    if key == "detection_distance":
        return "검출거리 설정 미흡"
    if key == "mounting":
        return "고정 불량"
    if key == "contamination_damage":
        return "오염·손상"
    if key == "wiring_power":
        return "정격·배선 오류"
    if key == "safety_distance":
        return "안전거리 미확보"
    if key == "alignment":
        return "정렬 불량"
    if key == "detection_response":
        return "검출/정지 실패"
    if key == "lockout":
        return "불시기동"
    if key == "emergency_stop":
        return "비상정지 실패"
    if key == "danger_zone":
        return "위험구역 노출"
    if key == "guarding":
        return "방호 우회"
    if key in {"cleaning", "machine_stop"}:
        return "운전 정지 미확보"
    if key == "settings":
        return "설정값 오류"
    if key == "mechanical_rotation":
        return "회전부 관리 미흡"
    return ""


def _maintenance_hazard_name(
    item: EvidenceBackedItem,
    key: str,
    content: str,
) -> str:
    source_name = getattr(item, "name", "")
    if (
        source_name
        and not _looks_like_meta_item(source_name)
        and not _looks_like_bad_card_text(source_name, enforce_length=False)
    ):
        return _compact_phrase(source_name, max_chars=24)
    return _hazard_name_from_key(key) or _hazard_name(content, item.content)


def _precaution_phrase(text: str) -> str:
    cleaned = _clean_source_excerpt(text)
    if not _has_meaningful_text(cleaned):
        return ""
    lowered = cleaned.casefold()
    if _is_precaution_style(cleaned):
        return cleaned.rstrip(". ")
    key = _maintenance_semantic_key(cleaned)
    if key == "metal_clearance":
        return "주변 금속·장애물 이격거리를 확보하지 않은 상태로 설치하지 않기"
    if key == "interference":
        return "인접 장치 간 간섭 방지 거리를 줄이지 않기"
    if key == "detection_distance":
        return "검출거리와 설정거리 범위를 벗어나게 설치하지 않기"
    if key == "mounting":
        return "고정·체결 상태가 불안정한 상태로 사용하지 않기"
    if key == "contamination_damage":
        return "오염·손상 우려가 있으면 보호 조치 없이 사용하지 않기"
    if key == "wiring_power":
        return "정격·전원·배선 조건을 확인하지 않은 상태로 연결하지 않기"
    if key == "safety_distance":
        return "안전거리 기준을 임의로 줄여 설치하지 않기"
    if key == "alignment":
        return "검출부 정렬을 임의로 바꾸지 않기"
    if key == "detection_response":
        if "설치 후" in lowered or "반응" in lowered:
            return "설치 후 정상 반응 시험 없이 사용하지 않기"
        return "검출·차단 반응 시험 없이 사용하지 않기"
    if key == "lockout":
        return "전원 차단/잠금 상태를 임의로 해제하지 않기"
    if key == "emergency_stop":
        return "비상정지장치 접근을 막지 않기"
    if key == "danger_zone":
        return "방호구역 내 작업자 확인 전 재기동하지 않기"
    if key == "guarding":
        return "방호장치를 임의로 제거하거나 우회하지 않기"
    if key in {"cleaning", "machine_stop"}:
        return "운전 정지 확인 전 청소하지 않기"
    if key == "training":
        return "작업표준과 취급요령 교육 없이 진행하지 않기"
    if key == "settings":
        return "승인 없이 설정값을 변경하지 않기"
    if key == "mechanical_rotation":
        return "회전부 고정·윤활 상태 확인 없이 운전하지 않기"
    if "무효" in lowered or "우회" in lowered or "해제" in lowered:
        return "안전기능을 임의로 무효화하거나 우회하지 않기"
    if "설치" in lowered:
        return "승인된 설치 기준 없이 임의 설치하지 않기"
    if any(term in lowered for term in ("위험", "사고", "오동작", "고장", "이상")):
        return "위험요인 확인 전 임의 작업하지 않기"
    return ""


def _stop_condition_phrase(text: str) -> str:
    cleaned = _clean_source_excerpt(text)
    if not _has_meaningful_text(cleaned):
        return ""
    if _is_stop_condition_phrase(cleaned):
        return _normalize_stop_condition_sentence(cleaned)
    key = _maintenance_semantic_key(cleaned)
    if key == "metal_clearance":
        return "주변 금속·장애물 이격거리를 확보할 수 없으면 즉시 작업 중지"
    if key == "interference":
        return "인접 장치 간 간섭 방지 거리를 확보할 수 없으면 즉시 작업 중지"
    if key == "detection_distance":
        return "검출거리나 설정거리 기준을 확인할 수 없으면 즉시 작업 중지"
    if key == "mounting":
        return "장치를 견고하게 고정할 수 없으면 즉시 작업 중지"
    if key == "contamination_damage":
        return "오염·손상으로 오동작 우려가 있으면 즉시 작업 중지"
    if key == "wiring_power":
        return "정격·전원·배선 조건이 맞지 않으면 즉시 작업 중지"
    if key == "safety_distance":
        return "안전거리 기준을 확인할 수 없으면 즉시 작업 중지"
    if key == "alignment":
        return "정렬 이상이나 오검출이 발생하면 즉시 작업 중지"
    if key == "detection_response":
        return "검출 또는 차단 시 위험 동작이 멈추지 않으면 즉시 작업 중지"
    if key == "lockout":
        return "전원 차단/잠금 상태를 유지할 수 없으면 즉시 작업 중지"
    if key == "emergency_stop":
        return "비상정지장치가 작동하지 않거나 접근할 수 없으면 즉시 작업 중지"
    if key == "danger_zone":
        return "방호구역 내 작업자가 있으면 즉시 작업 중지"
    if key == "guarding":
        return "방호장치를 우회하거나 무효화한 상태면 작업 중지"
    if key in {"cleaning", "machine_stop"}:
        return "운전 정지가 유지되지 않으면 즉시 작업 중지"
    if key == "settings":
        return "안전 관련 설정값을 확인할 수 없으면 즉시 작업 중지"
    if key == "mechanical_rotation":
        return "회전부 이상음·과열·손상이 있으면 즉시 작업 중지"
    lowered = cleaned.casefold()
    if any(term in lowered for term in ("고장", "이상", "오동작", "불량")):
        return "고장이나 이상 동작이 확인되면 즉시 작업 중지"
    return ""


def _is_precaution_style(text: str) -> bool:
    normalized = text.rstrip(". ")
    if normalized.endswith(("확인", "점검")):
        return False
    return normalized.endswith(SECTION_STYLE_ENDINGS)


def _is_stop_condition_phrase(text: str) -> bool:
    lowered = text.casefold()
    has_condition = any(marker in lowered for marker in STOP_CONDITION_MARKERS)
    has_stop_action = any(marker in lowered for marker in STOP_ACTION_MARKERS)
    return has_condition and has_stop_action


def _normalize_stop_condition_sentence(text: str) -> str:
    key = _maintenance_semantic_key(text)
    if key in {
        "metal_clearance",
        "interference",
        "detection_distance",
        "mounting",
        "contamination_damage",
        "wiring_power",
        "safety_distance",
        "alignment",
        "detection_response",
        "lockout",
        "emergency_stop",
        "danger_zone",
        "guarding",
        "cleaning",
        "machine_stop",
        "settings",
        "mechanical_rotation",
    }:
        return _stop_condition_phrase_from_key(key)
    cleaned = _compact_phrase(text, max_chars=72).rstrip(". ")
    cleaned = re.sub(r"(합니다|하세요|하여야 함|해야 함)\.?$", "", cleaned).strip()
    if "중지" not in cleaned:
        cleaned = f"{cleaned} 시 작업 중지"
    return cleaned


def _stop_condition_phrase_from_key(key: str) -> str:
    if key == "metal_clearance":
        return "주변 금속·장애물 이격거리를 확보할 수 없으면 즉시 작업 중지"
    if key == "interference":
        return "인접 장치 간 간섭 방지 거리를 확보할 수 없으면 즉시 작업 중지"
    if key == "detection_distance":
        return "검출거리나 설정거리 기준을 확인할 수 없으면 즉시 작업 중지"
    if key == "mounting":
        return "장치를 견고하게 고정할 수 없으면 즉시 작업 중지"
    if key == "contamination_damage":
        return "오염·손상으로 오동작 우려가 있으면 즉시 작업 중지"
    if key == "wiring_power":
        return "정격·전원·배선 조건이 맞지 않으면 즉시 작업 중지"
    if key == "safety_distance":
        return "안전거리 기준을 확인할 수 없으면 즉시 작업 중지"
    if key == "alignment":
        return "정렬 이상이나 오검출이 발생하면 즉시 작업 중지"
    if key == "detection_response":
        return "검출 또는 차단 시 위험 동작이 멈추지 않으면 즉시 작업 중지"
    if key == "lockout":
        return "전원 차단/잠금 상태를 유지할 수 없으면 즉시 작업 중지"
    if key == "emergency_stop":
        return "비상정지장치가 작동하지 않거나 접근할 수 없으면 즉시 작업 중지"
    if key == "danger_zone":
        return "방호구역 내 작업자가 있으면 즉시 작업 중지"
    if key == "guarding":
        return "방호장치를 우회하거나 무효화한 상태면 작업 중지"
    if key in {"cleaning", "machine_stop"}:
        return "운전 정지가 유지되지 않으면 즉시 작업 중지"
    if key == "settings":
        return "안전 관련 설정값을 확인할 수 없으면 즉시 작업 중지"
    if key == "mechanical_rotation":
        return "회전부 이상음·과열·손상이 있으면 즉시 작업 중지"
    return ""


def _maintenance_hazards_for_card(
    candidates: list[SentenceCandidate],
    *,
    question: str,
    limit: int,
) -> list[MaintenanceHazard]:
    selected = _candidate_subset(
        candidates,
        groups={PDF_SOURCE_GROUP, PUBLIC_SAFETY_SOURCE_GROUP, INCIDENT_SOURCE_GROUP},
        terms=HAZARD_TERMS + STOP_TERMS + PRECHECK_TERMS + ACTION_TERMS,
        limit=max(limit * 6, 18),
        allow_fallback=True,
        exclude_reference_title=True,
    )
    hazards: list[MaintenanceHazard] = []
    seen_phrases: set[str] = set()
    seen_keys: set[str] = set()
    for candidate in selected:
        if question and not _item_relevant_to_question(candidate.content, question):
            continue
        content = _hazard_phrase(candidate.content)
        if not content:
            continue
        key = _maintenance_semantic_key(f"{candidate.content} {content}")
        if key in seen_keys or content in seen_phrases:
            continue
        hazards.append(
            MaintenanceHazard(
                name=_hazard_name_from_key(key) or _hazard_name(content, candidate.content),
                content=content,
                evidence_chunk_ids=[candidate.evidence_chunk_id],
            )
        )
        seen_phrases.add(content)
        seen_keys.add(key)
        if len(hazards) >= limit:
            break
    return hazards


def _hazards_for_card(
    candidates: list[SentenceCandidate],
    *,
    groups: set[str],
    terms: tuple[str, ...],
    limit: int,
    question: str = "",
) -> list[MaintenanceHazard]:
    selected = _candidate_subset(
        candidates,
        groups=groups,
        terms=terms,
        limit=limit,
        allow_fallback=False,
    )
    hazards: list[MaintenanceHazard] = []
    seen: set[str] = set()
    used_evidence: set[str] = set()
    for candidate in selected:
        if candidate.evidence_chunk_id in used_evidence:
            continue
        if question and not _item_relevant_to_question(candidate.content, question):
            continue
        content = _final_card_phrase(candidate.content)
        if content == "위험요인 확인":
            continue
        if not content or content in seen:
            continue
        seen.add(content)
        used_evidence.add(candidate.evidence_chunk_id)
        hazards.append(
            MaintenanceHazard(
                name=_hazard_name(content, candidate.content),
                content=content,
                evidence_chunk_ids=[candidate.evidence_chunk_id],
            )
        )
        if len(hazards) >= limit:
            break
    return hazards


def _candidate_subset(
    candidates: list[SentenceCandidate],
    *,
    groups: set[str],
    terms: tuple[str, ...],
    limit: int,
    allow_fallback: bool,
    require_action: bool = False,
    exclude_risk_only: bool = False,
    exclude_reference_title: bool = False,
) -> list[SentenceCandidate]:
    matched: list[SentenceCandidate] = []
    fallback: list[SentenceCandidate] = []
    seen: set[str] = set()
    ordered_candidates = sorted(
        candidates,
        key=lambda candidate: _card_candidate_score(
            candidate,
            terms=terms,
            require_action=require_action,
        ),
        reverse=True,
    )
    for candidate in ordered_candidates:
        if candidate.source_group not in groups:
            continue
        if exclude_risk_only and _is_risk_only_candidate(candidate.content):
            continue
        if exclude_reference_title and _is_reference_title_candidate(candidate.content):
            continue
        content_key = candidate.content.casefold()
        if content_key in seen:
            continue
        fallback.append(candidate)
        if require_action and not _contains_any(content_key, ACTION_TERMS):
            continue
        if terms and not _contains_any(content_key, terms):
            continue
        matched.append(candidate)
        seen.add(content_key)
        if len(matched) >= limit:
            return matched
    if not allow_fallback:
        return matched
    for candidate in sorted(
        fallback,
        key=lambda candidate: _card_candidate_score(
            candidate,
            terms=terms,
            require_action=require_action,
        ),
        reverse=True,
    ):
        content_key = candidate.content.casefold()
        if content_key in seen:
            continue
        if exclude_risk_only and _is_risk_only_candidate(candidate.content):
            continue
        if exclude_reference_title and _is_reference_title_candidate(candidate.content):
            continue
        if require_action and not _contains_any(content_key, ACTION_TERMS):
            continue
        matched.append(candidate)
        seen.add(content_key)
        if len(matched) >= limit:
            break
    return matched


def _card_candidate_score(
    candidate: SentenceCandidate,
    *,
    terms: tuple[str, ...],
    require_action: bool,
) -> float:
    text = candidate.content.casefold()
    term_hits = sum(1 for term in terms if term.casefold() in text)
    action_hits = sum(1 for term in ACTION_TERMS if term.casefold() in text)
    source_bonus = 0.0
    if candidate.source_group == INCIDENT_SOURCE_GROUP:
        source_bonus = 0.08
    elif candidate.source_group == PUBLIC_SAFETY_SOURCE_GROUP:
        source_bonus = 0.04
    action_bonus = min(action_hits, 4) * (0.06 if require_action else 0.025)
    return candidate.score + min(term_hits, 6) * 0.12 + action_bonus + source_bonus


def _is_risk_only_candidate(text: str) -> bool:
    lowered = text.casefold()
    risk_markers = (
        "위험이 있습니다",
        "위험이 있",
        "발생 위험",
        "발생할 수 있습니다",
        "우려가 있습니다",
    )
    if not any(marker in lowered for marker in risk_markers):
        return False
    actionable_markers = (
        "확인하십시오",
        "점검",
        "차단",
        "정지",
        "설치",
        "준수",
        "작업자가 없는지",
        "동작하는지",
    )
    return not any(marker in lowered for marker in actionable_markers)


def _is_reference_title_candidate(text: str) -> bool:
    lowered = text.casefold()
    return any(
        marker in lowered
        for marker in (
            "포스터",
            "기술지침",
            "안전보건에 관한 지침",
            "안전작업 지침",
            "작업 지침",
            "사고사례",
        )
    )


def _short_strings_for_card(
    candidates: list[SentenceCandidate],
    *,
    groups: set[str],
    terms: tuple[str, ...],
    limit: int,
) -> list[str]:
    values: list[str] = []
    for candidate in _candidate_subset(
        candidates,
        groups=groups,
        terms=terms,
        limit=limit,
        allow_fallback=False,
    ):
        text = _compact_phrase(candidate.content, max_chars=42)
        text = _final_card_phrase(candidate.content) or text
        if text and text not in values:
            values.append(text)
    return values


def _task_strings_from_candidates(
    candidates: list[SentenceCandidate],
    *,
    groups: set[str],
    limit: int,
) -> list[str]:
    values: list[str] = []
    for candidate in _candidate_subset(
        candidates,
        groups=groups,
        terms=ACTION_TERMS,
        limit=limit,
        allow_fallback=False,
        require_action=True,
    ):
        text = _compact_phrase(candidate.content, max_chars=38)
        text = _final_card_phrase(candidate.content) or text
        if text and text not in values:
            values.append(text)
    return values


def _component_fallback_sentence(
    main_roles: list[EvidenceBackedItem],
    source: ChatSource,
) -> str:
    if main_roles:
        return f"{_final_sentence(main_roles[0].content)}이 필요한 장치입니다."
    title = source.original_filename or source.title or "검색된 문서"
    return f"{title} 근거에서 확인된 부품 정보입니다."


def _component_additional_needed(
    main_roles: list[EvidenceBackedItem],
    usage_locations: list[EvidenceBackedItem],
    precautions: list[EvidenceBackedItem],
) -> list[str]:
    needed: list[str] = []
    if not main_roles:
        needed.append("부품 기능 근거")
    if not usage_locations:
        needed.append("사용 위치 근거")
    if not precautions:
        needed.append("주의사항 근거")
    return needed


def _maintenance_reference_items_from_sources(
    sources: list[ChatSource],
    *,
    question: str,
    limit: int,
) -> list[EvidenceBackedItem]:
    items: list[EvidenceBackedItem] = []
    seen: set[str] = set()
    for source in sources:
        source_type = canonical_document_type(source.source_type)
        if source_type not in MAINTENANCE_REFERENCE_SOURCE_TYPES:
            continue
        if question and not _reference_source_relevant_to_question(source, question):
            continue
        content = _maintenance_reference_content(source, question=question)
        if not content or content in seen:
            continue
        items.append(
            EvidenceBackedItem(
                content=content,
                evidence_chunk_ids=[source.chunk_id],
            )
        )
        seen.add(content)
        if len(items) >= limit:
            break
    return items


def _maintenance_reference_content(source: ChatSource, *, question: str = "") -> str:
    source_type = canonical_document_type(source.source_type)
    label = _maintenance_reference_label(source_type)
    if not label:
        return ""
    summary = _maintenance_reference_summary(source, question=question)
    title = _maintenance_reference_display_title(
        source,
        summary=summary,
        question=question,
    )
    if title and summary:
        return f"[{label}] {title}: {summary}"
    if title:
        return f"[{label}] {title}"
    if summary:
        return f"[{label}] {summary}"
    return ""


def _maintenance_reference_display_title(
    source: ChatSource,
    *,
    summary: str,
    question: str,
) -> str:
    source_type = canonical_document_type(source.source_type)
    raw_title = _clean_source_excerpt(source.title or source.original_filename or "")
    text = _clean_source_excerpt(
        " ".join(
            value
            for value in (question, source.title, source.section, source.excerpt, summary)
            if value
        )
    ).casefold()
    if source_type == "public_guide":
        key = _maintenance_semantic_key(text)
        if key == "safety_distance":
            return "안전거리 확보 기준"
        if key in {"alignment", "detection_response"}:
            return "검출·정지 기능 확인 기준"
        if key == "wiring_power":
            return "정격·전원·배선 안전 기준"
        if key == "mounting":
            return "장착 위치와 고정 상태 기준"
        if key == "interference":
            return "간섭 방지 및 오동작 예방 기준"
        if "컨베이어" in text and any(term in text for term in ("청소", "정비", "수리", "정지", "재가동")):
            return "컨베이어 정비 전 정지 및 재가동 방지 기준"
        if "비상정지" in text:
            return "비상정지장치 설치 및 작동 기준"
        if "인터락" in text:
            return "인터락 방호장치 사용 기준"
    if source_type == "public_media" and summary:
        return "안전자료 요약"
    return raw_title or "검색 근거"


def _maintenance_reference_summary(source: ChatSource, *, question: str) -> str:
    text = _clean_source_excerpt(source.excerpt)
    source_type = canonical_document_type(source.source_type)
    lowered = text.casefold()
    key = _maintenance_semantic_key(text)
    if source_type == "public_law" and key in {"guarding", "alignment", "detection_response"}:
        return "방호장치의 정의, 구성 요소, 안전기능과 관련된 조항입니다."
    if key == "safety_distance":
        return "설치 위치와 안전거리 확보 기준을 확인할 수 있는 근거입니다."
    if key == "alignment":
        return "검출부 정렬과 감지 기능 확인에 관한 근거입니다."
    if key == "detection_response":
        return "검출·차단 시 설비 반응 또는 정지 기능 확인에 관한 근거입니다."
    if key == "wiring_power":
        return "정격·전원·배선 조건 확인에 관한 근거입니다."
    if key == "mounting":
        return "장착 위치와 고정 상태 확인에 관한 근거입니다."
    if key == "interference":
        return "간섭 방지와 오동작 예방에 관한 근거입니다."
    if "컨베이어" in lowered and any(term in lowered for term in ("청소", "정비", "수리")):
        return "컨베이어 청소·정비 전 운전 정지와 재가동 방지 조치에 관한 근거입니다."
    if source_type == "public_incident":
        sentence = _best_reference_sentence(source, question=question, max_chars=90)
        return sentence or "질문과 유사한 사고 원인 및 예방대책을 확인할 수 있는 사례입니다."
    return _best_reference_sentence(source, question=question, max_chars=90)


def _best_reference_sentence(
    source: ChatSource,
    *,
    question: str,
    max_chars: int,
) -> str:
    question_terms = _relevance_terms(question)
    best: tuple[float, str] | None = None
    for index, sentence in enumerate(_split_candidate_sentences(source.excerpt)):
        cleaned = _clean_source_excerpt(sentence)
        if not _has_meaningful_text(cleaned) or _looks_like_meta_item(cleaned):
            continue
        score = 0.0
        terms = _relevance_terms(cleaned)
        if question_terms:
            score += len(question_terms & terms) / max(len(question_terms), 1)
        if any(
            term.casefold() in cleaned.casefold()
            for term in ACTION_TERMS + PRECHECK_TERMS + HAZARD_TERMS
        ):
            score += 0.3
        score -= index * 0.02
        if best is None or score > best[0]:
            best = (score, cleaned)
    if best is None:
        return ""
    return _compact_phrase(_polite_document_sentence(best[1]), max_chars=max_chars)


def _maintenance_reference_label(source_type: str) -> str:
    if source_type == "public_law":
        return "관련 법령"
    if source_type == "public_guide":
        return "안전 가이드"
    if source_type == "public_media":
        return "안전자료"
    if source_type == "public_incident":
        return "사고사례"
    if source_type == "company_policy":
        return "회사 기준"
    return ""


def _source_relevant_to_question(source: ChatSource, question: str) -> bool:
    haystack = " ".join(
        value
        for value in (source.title, source.section, source.excerpt)
        if value
    )
    return _item_relevant_to_question(haystack, question)


REFERENCE_TOPIC_ALIASES: tuple[tuple[str, ...], ...] = (
    (
        "라이트커튼",
        "광전자식방호장치",
        "감응식방호장치",
        "광선식안전장치",
    ),
    (
        "비전센서",
        "영상센서",
        "카메라센서",
        "비전카메라",
    ),
    (
        "근접센서",
        "근접스위치",
    ),
)


def _reference_source_relevant_to_question(
    source: ChatSource,
    question: str,
) -> bool:
    """Require a topic match before showing laws, guides, policies, or incidents.

    Generic safety terms such as stop, guarding, or inspection are intentionally
    insufficient because they previously allowed unrelated references into the
    answer.
    """

    subject = _question_subject_phrase(question)
    if not subject:
        return _source_relevant_to_question(source, question)
    question_topic = re.sub(r"[^0-9a-z가-힣]", "", subject.casefold())
    source_text = " ".join(
        value
        for value in (
            source.title,
            source.original_filename,
            source.section,
            source.excerpt,
        )
        if value
    ).casefold()
    source_topic = re.sub(r"[^0-9a-z가-힣]", "", source_text)
    if question_topic and question_topic in source_topic:
        return True
    return any(
        any(alias in question_topic for alias in aliases)
        and any(alias in source_topic for alias in aliases)
        for aliases in REFERENCE_TOPIC_ALIASES
    )


def _risk_labels_for_summary(
    analysis: QueryAnalysis | None,
    hazards: Iterable[MaintenanceHazard],
    *,
    sources: Iterable[ChatSource] = (),
) -> list[str]:
    labels: list[str] = []
    evidence_text = " ".join(
        _clean_source_excerpt(value)
        for source in sources
        for value in (source.title, source.section, source.excerpt)
        if value
    )
    hazard_list = list(hazards)
    hazard_labels = [
        label
        for hazard in hazard_list
        for label in (_risk_label(hazard.content), _risk_label(hazard.name))
        if label
    ]

    def add(value: str | None, *, require_evidence: bool = False) -> None:
        if not value:
            return
        normalized = _risk_label(value)
        if (
            require_evidence
            and normalized
            and normalized not in hazard_labels
            and not _risk_label_supported_by_text(normalized, evidence_text)
        ):
            return
        if normalized and normalized != "기타" and normalized not in labels:
            labels.append(normalized)

    if analysis is not None:
        add(analysis.occurrence_type, require_evidence=True)
        for value in analysis.explicit_risk_factors:
            add(value, require_evidence=True)
    for hazard in hazard_list:
        add(hazard.content)
        add(hazard.name)
    return labels[:3]


def _risk_label_supported_by_text(label: str, text: str) -> bool:
    lowered = text.casefold()
    support_terms = {
        "끼임": (
            "끼임",
            "협착",
            "말려",
            "운전 정지",
            "재기동",
            "방호",
            "위험구역",
            "lock",
            "isolate",
        ),
        "감전": ("감전", "누전", "전원", "전기"),
        "떨어짐": ("떨어짐", "추락", "고소", "발판"),
        "넘어짐": ("넘어짐", "전도", "미끄러"),
        "맞음": ("맞음", "낙하", "비래"),
        "부딪힘": ("부딪힘", "충돌"),
        "깔림": ("깔림",),
        "화재": ("화재",),
        "폭발": ("폭발",),
        "질식": ("질식", "산소"),
        "중독": ("중독", "유해가스"),
        "베임": ("베임", "절단"),
        "찔림": ("찔림",),
        "안전기능 실패": (
            "오검출",
            "미검출",
            "검출",
            "정지 실패",
            "비상정지",
            "오동작",
            "복귀불량",
            "주파수 간섭",
        ),
    }.get(label, (label,))
    return any(term in lowered for term in support_terms)


def _risk_label(text: str) -> str:
    lowered = _clean_source_excerpt(text).casefold()
    if any(
        term in lowered
        for term in (
            "끼임",
            "협착",
            "말려",
            "불시기동",
            "재기동",
            "운전 정지",
            "안전거리",
            "위험구역",
            "방호구역",
            "방호장치",
            "caught-in",
            "caught in",
        )
    ):
        return "끼임"
    if "감전" in lowered:
        return "감전"
    if "추락" in lowered or "떨어짐" in lowered:
        return "떨어짐"
    if "넘어짐" in lowered or "전도" in lowered:
        return "넘어짐"
    if "낙하" in lowered or "맞음" in lowered:
        return "맞음"
    if "충돌" in lowered or "부딪힘" in lowered:
        return "부딪힘"
    if "깔림" in lowered:
        return "깔림"
    if "화재" in lowered:
        return "화재"
    if "폭발" in lowered:
        return "폭발"
    if "질식" in lowered:
        return "질식"
    if "중독" in lowered:
        return "중독"
    if "베임" in lowered or "절단" in lowered:
        return "베임"
    if "찔림" in lowered:
        return "찔림"
    if (
        "오검출" in lowered
        or "정지 실패" in lowered
        or "검출" in lowered
        or "비상정지" in lowered
        or "오동작" in lowered
        or "복귀불량" in lowered
        or "주파수 간섭" in lowered
    ):
        return "안전기능 실패"
    return ""


def _core_warning_from_labels(labels: list[str]) -> str:
    if labels:
        return (
            f"예상 핵심 위험은 {', '.join(labels[:3])}입니다. "
            "해당 위험이 통제되지 않으면 작업을 시작하지 말고 안전관리자 확인을 받으세요."
        )
    return (
        "예상 핵심 위험을 단정할 근거가 부족합니다. "
        "작업을 시작하기 전에 현장 조건과 문서 근거를 안전관리자와 확인하세요."
    )


def _core_warning(
    pre_checks: list[EvidenceBackedItem],
    hazards: list[MaintenanceHazard],
    manual_steps: list[EvidenceBackedItem],
) -> str:
    if pre_checks:
        return f"{_final_card_phrase(pre_checks[0].content) or '작업 전 확인'} 후 작업하세요."
    if hazards:
        return f"{_final_card_phrase(hazards[0].content) or '위험요인'} 확인하세요."
    if manual_steps:
        return f"{_final_card_phrase(manual_steps[0].content) or '작업 절차'} 확인하세요."
    return "검증된 근거 확인 후 작업하세요."


def _additional_needed_items(
    *,
    pre_checks: list[EvidenceBackedItem],
    manual_steps: list[EvidenceBackedItem],
    references: list[EvidenceBackedItem],
) -> list[str]:
    needed: list[str] = []
    if not pre_checks:
        needed.append("작업 전 확인 기준")
    if not manual_steps:
        needed.append("승인된 작업 절차")
    if not references:
        needed.append("관련 안전 기준 또는 사고사례")
    return needed


def _hazard_name(content: str, raw_content: str = "") -> str:
    haystack = f"{raw_content} {content}".casefold()
    for term in HAZARD_TERMS:
        if term.casefold() in haystack:
            return _compact_phrase(term, max_chars=24)
    return "위험요인"


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


def _compact_phrase(text: str, *, max_chars: int = MAX_CARD_ITEM_CHARS) -> str:
    cleaned = _clean_source_excerpt(text)
    cleaned = cleaned.strip(" -•*[]()")
    cleaned = re.sub(r"[;；]\s*", ". ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    if len(cleaned) > max_chars:
        cleaned = cleaned[: max_chars - 3].rstrip(" ,.;:·-") + "..."
    return cleaned


def _final_sentence(text: str) -> str:
    content = _final_card_phrase(text)
    if content:
        return content
    cleaned = _compact_phrase(text, max_chars=MAX_FINAL_CARD_CHARS)
    return cleaned.rstrip(". ")


def _final_card_phrase(text: str) -> str:
    cleaned = _clean_source_excerpt(text)
    cleaned = cleaned.strip(" -•*[]()")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned or _looks_like_bad_card_text(cleaned, enforce_length=False):
        return ""
    lowered = cleaned.casefold()
    for terms, phrase in CONCISE_RULES:
        if all(term.casefold() in lowered for term in terms):
            return phrase
    tokens = [
        token
        for token in re.findall(r"[0-9A-Za-z가-힣]+", cleaned)
        if len(token) >= 2 and token.casefold() not in RELEVANCE_STOPWORDS
    ]
    useful = [token for token in tokens if not token.endswith(("하십시오", "합니다"))]
    if not useful:
        return ""
    phrase = " ".join(useful[:3])
    if not phrase:
        return ""
    if len(phrase) > MAX_FINAL_CARD_CHARS:
        phrase = phrase[:MAX_FINAL_CARD_CHARS].rstrip()
    if _looks_like_bad_card_text(phrase):
        return ""
    return phrase


def _looks_like_bad_card_text(text: str, *, enforce_length: bool = True) -> bool:
    normalized = " ".join(str(text).split())
    if not normalized:
        return True
    if normalized in GENERIC_QWEN_FALLBACK_ANSWERS:
        return True
    if normalized.startswith(BAD_CARD_PREFIXES):
        return True
    if normalized.endswith(BAD_CARD_SUFFIXES):
        return True
    if "..." in normalized:
        return True
    if (
        enforce_length
        and len(normalized) > MAX_FINAL_CARD_CHARS
        and not normalized.endswith(("니다.", "습니다."))
    ):
        return True
    return False


def _checklist_phrase(text: str) -> str:
    content = _final_card_phrase(text) or _compact_phrase(text, max_chars=42)
    content = re.sub(r"(하십시오|합니다|하세요|한다|할 것|해야 함|하여야 함)\.?$", "", content).strip()
    content = content.rstrip(" .")
    return content


def _checklist_content_from_pre_check(text: str) -> str:
    raw_content = _clean_source_excerpt(text)
    if (
        raw_content
        and len(raw_content) <= MAX_CARD_ITEM_CHARS
        and not _looks_like_meta_item(raw_content)
        and not _looks_like_bad_card_text(raw_content, enforce_length=False)
    ):
        content = raw_content
    else:
        content = _checklist_phrase(text)
    if not content:
        return ""
    return _to_tbm_action_phrase(content)


def _to_tbm_action_phrase(text: str) -> str:
    content = _clean_source_excerpt(text).strip(" .")
    content = re.sub(r"\s*(을|를)?\s*확인합니다\.?$", " 확인", content)
    content = re.sub(r"\s*(을|를)?\s*점검합니다\.?$", " 점검", content)
    content = re.sub(r"\s*(을|를)?\s*검사합니다\.?$", " 검사", content)
    content = re.sub(
        r"(하십시오|합니다|하세요|한다|할 것|해야 함|하여야 함)\.?$",
        "",
        content,
    ).strip(" .")
    if not content:
        return ""
    lowered = content.casefold()
    if "베어링" in lowered and any(term in lowered for term in ("손상", "치수", "하우징")):
        return "베어링·축·하우징의 손상 및 치수 확인하기"
    if ("베어링" in lowered or "축" in lowered) and "정렬" in lowered:
        return "축·베어링 정렬 상태 확인하기"
    if "윤활" in lowered:
        return "지정 윤활제의 종류·주입량 확인하기"
    if "이상음" in lowered or "과열" in lowered:
        return "시운전 후 베어링 이상음·과열 여부 확인하기"
    if any(term in lowered for term in ("전원 차단", "전원을 차단", "잠금", "lockout")):
        return "전원 차단 후 잠금·표지 부착 상태 확인하기"
    if "광축 표시등" in lowered:
        return "투광기·수광기의 상·하단 광축 표시등 정렬 확인하기"
    if "상호간섭" in lowered or "상호 간섭" in lowered or "차광판" in lowered:
        return "복수 라이트커튼의 상호간섭 방지·차광판 적용 확인하기"
    if any(term in lowered for term in ("외란광", "직사광선", "스포트라이트", "반사광")):
        return "외란광·반사광이 수광기에 직접 입사하지 않는지 확인하기"
    if ("검출영역" in lowered or "검출 영역" in lowered) and any(
        term in lowered for term in ("통과", "우회", "가드")
    ):
        return "위험부 접근 시 검출영역 통과·우회 방지 구조 확인하기"
    key = _maintenance_semantic_key(content)
    if key == "metal_clearance":
        return "주변 금속·장애물 이격거리 확인하기"
    if key == "interference":
        return "인접 장치 간 간섭 방지 거리 확인하기"
    if key == "detection_distance":
        return "검출거리와 설정거리 기준 대조하기"
    if key == "mounting":
        return "장착 위치와 고정 상태 확인하기"
    if key == "contamination_damage":
        return "오염·손상 및 보호 조치 확인하기"
    if key == "wiring_power":
        return "정격·전원·배선 조건 확인하기"
    if key == "safety_distance":
        return "안전거리 실제 측정값과 제조사 기준 대조하기"
    if key == "alignment":
        return "검출부 정렬 상태 맞추기"
    if key == "detection_response":
        if "설치 후" in lowered or "반응" in lowered:
            return "설치 후 정상 반응 여부 확인하기"
        return "검출·차단 시 설비 반응 시험하기"
    if key == "danger_zone":
        return "방호구역 안쪽 작업자 유무 직접 확인하기"
    if key == "guarding":
        return "방호장치 설치 위치와 고정 상태 확인하기"
    if key == "lockout":
        return "전원 차단 후 잠금·표지 부착 상태 확인하기"
    if key == "emergency_stop":
        return "비상정지장치 접근·작동 상태 점검하기"
    if key in {"cleaning", "machine_stop"}:
        return "운전 정지 후 재가동 방지 조치 확인하기"
    if key == "settings":
        return "설정값과 기능 적용 상태 확인하기"
    if key == "mechanical_rotation":
        return "회전부 고정·윤활 상태 확인하기"
    if _is_stop_condition_phrase(content):
        condition_head = re.split(
            r"확인할 수 없|유지할 수 없|작동하지|동작하지|멈추지|정지하지|"
            r"발생하면|발생 시|없으면|않으면|불가|이상|고장|오검출|불량",
            content,
            maxsplit=1,
        )[0].strip(" .,/·-")
        condition_head = re.sub(r"(을|를|이|가|은|는)$", "", condition_head).strip()
        if condition_head:
            content = condition_head
    if content.endswith(("하기", "않기", "말기")):
        if "위험요인" in content and content.endswith("않기"):
            return "위험요인과 통제조치 공유하기"
        return content
    content = content.replace("/재가동 방지", " 및 재가동 방지")
    if content.endswith(("확인", "점검", "검사", "시험", "측정", "정렬", "교육")):
        return f"{content}하기"
    if content.endswith(("방지", "금지", "준수", "유지", "확보")):
        return f"{content}하기"
    if content.endswith("적합"):
        return f"{content} 여부 확인하기"
    return f"{content} 확인하기"


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


def _verified_items_by_type(
    items: Iterable[EvidenceBackedItem],
    allowed_ids: set[str],
    source_by_id: dict[str, ChatSource],
    allowed_types: frozenset[str],
) -> list:
    verified: list[EvidenceBackedItem] = []
    for item in items:
        normalized_item = _verified_item(item, allowed_ids)
        if normalized_item is None:
            continue
        if all(
            chunk_id in source_by_id
            and canonical_document_type(source_by_id[chunk_id].source_type)
            in allowed_types
            for chunk_id in normalized_item.evidence_chunk_ids
        ):
            verified.append(normalized_item)
    return verified


def _verified_item(
    item: EvidenceBackedItem,
    allowed_ids: set[str],
    *,
    compact_for_card: bool = False,
) -> EvidenceBackedItem | None:
    if not item.evidence_chunk_ids:
        return None
    if not set(item.evidence_chunk_ids).issubset(allowed_ids):
        return None
    content = _verified_content(item.content, compact_for_card=compact_for_card)
    if content is None:
        return None
    return item.model_copy(update={"content": content})


def _verified_content(content: str, *, compact_for_card: bool = False) -> str | None:
    cleaned = _clean_source_excerpt(content)
    if not _has_meaningful_text(cleaned):
        return None
    if _looks_like_meta_item(cleaned):
        return None
    if _looks_like_bad_card_text(cleaned, enforce_length=False):
        return None
    if compact_for_card and len(cleaned) > MAX_CARD_ITEM_CHARS:
        return _compact_phrase(cleaned, max_chars=MAX_CARD_ITEM_CHARS)
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
