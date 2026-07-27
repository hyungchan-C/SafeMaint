from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from threading import Lock
from typing import Any
from uuid import UUID

import numpy as np
import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row

from rag_service.config import Settings
from rag_service.document_types import (
    COMPONENT_DOCUMENT_TYPES,
    MAINTENANCE_DOCUMENT_TYPES,
    MANUAL_DOCUMENT_TYPES,
    canonical_document_type,
)
from rag_service.schemas import ChatRequest, ChatSource, InternalChatRequest


logger = logging.getLogger(__name__)


class RetrievalError(RuntimeError):
    """Raised when the model or vector database cannot produce grounded results."""


GENERIC_QUERY_TERMS = frozenset(
    {
        "how",
        "method",
        "please",
        "safety",
        "work",
        "작업",
        "방법",
        "안전",
        "관련",
        "문서",
        "요약",
        "요약해",
        "요약해줘",
        "정리",
        "정리해",
        "정리해줘",
        "내용",
        "파일",
        "첨부",
        "첨부한",
        "확인",
        "확인해줘",
        "확인해",
        "교체",
        "교체법",
        "교체작업",
        "교체할",
        "청소",
        "청소법",
        "청소할",
        "점검",
        "점검할",
        "정비",
        "설치",
        "설치법",
        "설치시",
        "설치할",
        "설치하려",
        "설치하려고",
        "설치하기",
        "주의사항",
        "주의",
        "절차",
        "알려줘",
        "알려주세요",
        "알려",
        "설명",
        "어디에",
        "쓰는",
        "사용",
        "용도",
        "거야",
        "할거야",
        "예정",
        "예정이야",
        "뭐야",
        "무엇",
        "무엇이야",
        "뭔지",
        "뭐하는",
        "정의",
        "해주세요",
        "하려고",
        "합니다",
        "할게",
        "내부",
        "내부를",
        "어떻게",
        "replacement",
        "installation",
        "어디",
        "확인사항",
    }
)
MAINTENANCE_OCCURRENCE_EXPANSIONS = {
    "끼임": ("끼임", "협착", "방호", "인터락", "위험구역"),
    "감전": ("감전", "전원", "차단", "절연", "접지"),
    "화재": ("화재", "점화", "과열", "소화", "가연"),
    "폭발": ("폭발", "압력", "가스", "인화", "점화"),
    "떨어짐": ("떨어짐", "추락", "고소", "난간", "발판"),
    "넘어짐": ("넘어짐", "전도", "미끄러짐", "통로", "바닥"),
    "맞음": ("맞음", "낙하", "비래", "충돌", "보호구"),
    "부딪힘": ("부딪힘", "충돌", "이동", "접근", "시야"),
    "깔림": ("깔림", "전도", "하중", "지지", "고정"),
    "질식": ("질식", "밀폐", "환기", "산소", "가스"),
    "중독": ("중독", "유해", "가스", "환기", "노출"),
    "베임": ("베임", "절단", "날", "칼날", "보호구"),
    "찔림": ("찔림", "날카로운", "파편", "보호구", "정리"),
}
MAINTENANCE_ACTION_QUERY_EXPANSIONS = {
    "청소": ("청소", "정지", "전원", "차단", "잠금", "재가동", "끼임", "협착", "사고", "예방"),
    "세척": ("세척", "정지", "전원", "차단", "잠금", "재가동", "끼임", "협착", "사고", "예방"),
    "점검": ("점검", "정지", "차단", "방호", "인터락", "위험", "사고", "예방"),
    "검사": ("검사", "정지", "차단", "방호", "인터락", "위험", "사고", "예방"),
    "교체": ("교체", "정지", "전원", "차단", "잠금", "격리", "끼임", "협착", "사고", "예방"),
    "정비": ("정비", "정지", "전원", "차단", "잠금", "격리", "끼임", "협착", "사고", "예방"),
    "보수": ("보수", "정지", "전원", "차단", "잠금", "격리", "끼임", "협착", "사고", "예방"),
    "설치": ("설치", "고정", "위치", "정격", "배선", "오동작", "주의", "기준"),
}
MAINTENANCE_ACTION_TERMS = frozenset(
    {
        "교체",
        "청소",
        "점검",
        "검사",
        "정비",
        "보수",
        "설치",
        "배선",
        "세척",
        "정렬",
        "조정",
        "해체",
        "분리",
        "연결",
        "운반",
        "차단",
        "격리",
        "잠금",
        "replace",
        "clean",
        "inspect",
        "check",
        "maintain",
        "install",
        "wire",
        "align",
        "adjust",
        "isolate",
        "lock",
    }
)
SAFETY_CONTROL_ACTION_TERMS = frozenset(
    {
        "차단",
        "격리",
        "잠금",
        "isolate",
        "lock",
    }
)
INCIDENT_MAINTENANCE_ACTION_TERMS = frozenset(
    {
        "교체",
        "청소",
        "세척",
        "점검",
        "검사",
        "정비",
        "보수",
        "해체",
        "분리",
        "replace",
        "clean",
        "inspect",
        "check",
        "maintain",
    }
)
KOREAN_TOKEN_SUFFIXES = (
    "으로는",
    "에서는",
    "에게는",
    "이라는",
    "이란",
    "인가요",
    "입니다",
    "이에요",
    "이야",
    "에서",
    "에게",
    "으로",
    "부터",
    "까지",
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "의",
    "에",
    "로",
    "과",
    "와",
    "도",
    "만",
)
SAFETY_SIGNAL_TERMS = (
    "위험",
    "안전",
    "주의",
    "경고",
    "금지",
    "방호",
    "보호",
    "차단",
    "격리",
    "재가동",
    "인터락",
    "정지",
    "승인",
    "hazard",
    "warning",
    "caution",
    "lockout",
    "tagout",
    "interlock",
)
DOCUMENT_CATEGORY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("overview", ("개요", "소개", "목차", "표지", "overview", "introduction")),
    ("safety", ("안전", "주의", "경고", "위험", "금지", "safety", "warning", "caution")),
    ("procedure", ("설치", "점검", "검사", "정비", "보수", "배선", "설정", "교체", "절차", "install", "inspect", "maintenance", "wiring")),
    ("specification", ("사양", "규격", "모델", "구성", "정격", "치수", "spec", "model", "configuration")),
    ("troubleshooting", ("오류", "에러", "경고", "고장", "trouble", "error", "alarm")),
)


DOCUMENT_ALIAS_STOPWORDS = GENERIC_QUERY_TERMS | frozenset(
    {
        "manual",
        "series",
        "model",
        "version",
        "pdf",
        "ko",
        "kr",
        "user",
        "guide",
        "catalog",
        "제품",
        "매뉴얼",
        "사용설명서",
        "취급설명서",
        "설명서",
        "카탈로그",
        "시리즈",
        "버전",
        "문서",
    }
)
DOCUMENT_ALIAS_SUFFIXES = (
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
    "차단기",
    "릴레이",
    "드라이버",
    "인버터",
    "설비",
    "장비",
    "기계",
)
DOCUMENT_SPEC_TERMS = (
    "정격",
    "전원",
    "전압",
    "전류",
    "배선",
    "결선",
    "검출",
    "감지",
    "거리",
    "간격",
    "이격",
    "치수",
    "토크",
    "하중",
    "회전",
    "윤활",
    "온도",
    "습도",
    "설치",
    "장착",
    "고정",
    "체결",
    "정렬",
    "간섭",
    "노이즈",
    "오동작",
    "고장",
    "손상",
    "보호",
    "방호",
    "차단",
    "정지",
    "시험",
    "점검",
    "설정",
)
DOCUMENT_INFO_QUERY_TERMS = (
    "정의",
    "역할",
    "기능",
    "용도",
    "구성",
    "사양",
    "모델",
    "부품",
    "사용",
)


def normalize_text(value: str) -> str:
    return "\n".join(line.strip() for line in value.strip().splitlines() if line.strip())


def psycopg_database_url(value: str) -> str:
    if value.startswith("postgresql+psycopg://"):
        return "postgresql://" + value.removeprefix("postgresql+psycopg://")
    return value


def normalize_source_types(
    source_types: Sequence[str] | None,
) -> tuple[str, ...] | None:
    if source_types is None:
        return None
    normalized: list[str] = []
    for source_type in source_types:
        value = canonical_document_type(source_type)
        if not value:
            raise ValueError("source_types must not contain blank values")
        if value not in normalized:
            normalized.append(value)
    if not normalized:
        raise ValueError("source_types must not be empty; use None for all sources")
    return tuple(normalized)


def normalize_document_ids(
    document_ids: Sequence[UUID] | None,
) -> tuple[UUID, ...] | None:
    if document_ids is None:
        return None
    normalized = tuple(dict.fromkeys(document_ids))
    if not normalized:
        raise ValueError("document_ids must not be empty; use None for all documents")
    return normalized


def scope_sql(
    source_types: tuple[str, ...] | None,
    document_ids: tuple[UUID, ...] | None,
) -> tuple[str, list[object]]:
    clauses: list[str] = []
    parameters: list[object] = []
    if source_types is not None:
        clauses.append("AND d.document_type_code = ANY(%s)")
        parameters.append(list(source_types))
    if document_ids is not None:
        clauses.append("AND d.id = ANY(%s)")
        parameters.append(list(document_ids))
    return "\n".join(clauses), parameters


def tokenize(value: str) -> tuple[str, ...]:
    tokens: list[str] = []
    for token in re.findall(r"[0-9A-Za-z가-힣_-]+", value):
        normalized = token.casefold().strip("_-")
        if len(normalized) < 2:
            continue
        stripped = _strip_korean_suffix(normalized)
        if stripped != normalized and len(stripped) >= 2:
            tokens.append(stripped)
        else:
            tokens.append(normalized)
    return tuple(dict.fromkeys(tokens))


def _strip_korean_suffix(token: str) -> str:
    if not re.search(r"[가-힣]", token):
        return token
    for suffix in KOREAN_TOKEN_SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 2:
            return token[: -len(suffix)]
    return token


def _term_matches(term: str, text: str) -> bool:
    # Older public safety material frequently uses the spelling "콘베이어".
    # Treat it as the same topic as the modern spelling "컨베이어".
    term = term.replace("콘베이어", "컨베이어")
    text = text.replace("콘베이어", "컨베이어")
    if re.fullmatch(r"[a-z]{1,3}", term):
        if re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text):
            return True
        return f"{term}□" in text or f"{term}_" in text or f"{term}-" in text
    if term in text:
        return True
    compact_term = _compact_for_phrase(term)
    compact_text = _compact_for_phrase(text)
    if len(compact_term) >= 4 and compact_term in compact_text:
        return True
    return len(term) >= 4 and any(
        len(candidate) >= 4 and (candidate in term or term in candidate)
        for candidate in tokenize(text)
    )


def lexical_score(query_terms: Sequence[str], text: str) -> float:
    if not query_terms:
        return 0.0
    normalized = text.casefold()
    matched = sum(1 for term in query_terms if _term_matches(term, normalized))
    return matched / len(query_terms)


def action_terms(value: str) -> tuple[str, ...]:
    terms: list[str] = []
    for token in tokenize(value):
        for action in MAINTENANCE_ACTION_TERMS:
            if token.startswith(action) or action in token:
                terms.append(action)
                break
    return tuple(dict.fromkeys(terms))


def _incident_maintenance_actions_compatible(
    query_actions: Sequence[str],
    row_actions: Sequence[str],
) -> bool:
    """Treat maintenance work variants as related only for accident evidence.

    Accident datasets commonly describe a replacement accident as maintenance,
    inspection, cleaning, or repair work.  Requiring the exact verb hides useful
    cases even when the equipment topic matches.
    """

    return bool(
        set(query_actions).intersection(INCIDENT_MAINTENANCE_ACTION_TERMS)
        and set(row_actions).intersection(INCIDENT_MAINTENANCE_ACTION_TERMS)
    )


def _generic_query_expansion_terms(value: str) -> tuple[str, ...]:
    lowered = value.casefold()
    terms: list[str] = list(topic_terms(value))
    if any(action in lowered for action in ("설치", "장착", "고정", "체결", "install")):
        terms.extend(
            (
                "설치",
                "장착",
                "고정",
                "체결",
                "위치",
                "간격",
                "거리",
                "정격",
                "전원",
                "배선",
                "결선",
                "환경",
                "오동작",
                "주의",
                "기준",
                "치수",
                "토크",
                "시험",
                "확인",
            )
        )
    if any(action in lowered for action in ("점검", "검사", "정비", "보수", "교체", "청소", "세척")):
        terms.extend(
            (
                "점검",
                "검사",
                "정비",
                "보수",
                "교체",
                "청소",
                "정지",
                "차단",
                "잠금",
                "격리",
                "재가동",
                "방호",
                "보호",
                "위험",
                "사고",
                "예방",
            )
        )
    if any(term in lowered for term in ("뭐야", "무엇", "정의", "설명", "알려", "용도", "사용")):
        terms.extend(DOCUMENT_INFO_QUERY_TERMS)
    terms.extend(term for term in DOCUMENT_SPEC_TERMS if term in lowered)
    return tuple(dict.fromkeys(terms))


def maintenance_query_expansions(value: str) -> tuple[str, ...]:
    terms: list[str] = []
    for action in action_terms(value):
        terms.extend(MAINTENANCE_ACTION_QUERY_EXPANSIONS.get(action, ()))
    terms.extend(_generic_query_expansion_terms(value))
    return tuple(dict.fromkeys(terms))


def safety_signal_score(text: str) -> float:
    if not text:
        return 0.0
    matched = sum(1 for term in SAFETY_SIGNAL_TERMS if term.casefold() in text)
    return min(1.0, matched / 3)


def topic_terms(value: str) -> tuple[str, ...]:
    return tuple(term for term in tokenize(value) if term not in GENERIC_QUERY_TERMS)


def _compact_for_phrase(value: str) -> str:
    return re.sub(
        r"[\s_-]+",
        "",
        value.casefold().replace("콘베이어", "컨베이어"),
    )


def domain_phrase_group_indexes(value: str) -> tuple[int, ...]:
    return ()


def domain_phrase_query_terms(value: str) -> tuple[str, ...]:
    return _generic_query_expansion_terms(value)


def _domain_phrase_matches(indexes: Sequence[int], text: str) -> bool:
    return False


def _domain_phrase_negative_matches(indexes: Sequence[int], text: str) -> bool:
    return False


def _topic_phrase_terms(value: str) -> tuple[str, ...]:
    return topic_terms(value)[:2]


def _topic_phrase_matches(terms: Sequence[str], text: str) -> bool:
    if len(terms) < 2:
        return False
    compact_phrase = "".join(_compact_for_phrase(term) for term in terms)
    return bool(compact_phrase) and compact_phrase in _compact_for_phrase(text)


def _conveyor_incident_topic_matches(
    terms: Sequence[str],
    text: str,
    *,
    source_type: str,
) -> bool:
    """Keep conveyor incidents relevant to a belt-conveyor maintenance query.

    Public incident titles commonly say only "콘베이어" even when the selected
    equipment/manual uses "컨베이어 벨트". Requiring the separate word "벨트"
    discarded those otherwise relevant accident cases.
    """

    if source_type != "public_incident":
        return False
    normalized_terms = tuple(
        term.replace("콘베이어", "컨베이어") for term in terms
    )
    if not any("컨베이어" in term for term in normalized_terms):
        return False
    normalized_text = text.replace("콘베이어", "컨베이어")
    if (
        "스크류컨베이어" in normalized_text
        and not any("스크류컨베이어" in term for term in normalized_terms)
    ):
        return False
    return "컨베이어" in normalized_text


def _topic_match_threshold(term_count: int) -> int:
    # A single short/generic term (e.g. "커튼") matching anywhere is not
    # enough evidence of relevance on its own -- it also matches unrelated
    # compound words like "커튼월"/"커튼박스". Require every term when there
    # are only one or two, and a clear majority (>=60%) for longer phrases,
    # so an isolated coincidental hit can no longer admit an unrelated
    # document.
    if term_count <= 2:
        return term_count
    return max(2, -(-term_count * 3 // 5))


def _maintenance_expansion_match(query_terms: Sequence[str], text: str) -> bool:
    if not query_terms:
        return False
    matched = sum(1 for term in query_terms if _term_matches(term, text))
    return matched >= min(2, len(query_terms))


def _combined_row_text(row: dict[str, Any]) -> str:
    return " ".join(
        str(value)
        for value in (
            row["title"],
            row["section"],
            row["original_filename"],
            row["source_type"],
            row.get("document_metadata"),
            row["content"],
        )
        if value
    )


def _document_category(row: dict[str, Any]) -> str:
    text = _combined_row_text(row).casefold()
    page_values = (row.get("page_start"), row.get("page"))
    if any(isinstance(page, int) and page <= 2 for page in page_values):
        return "overview"
    for category, keywords in DOCUMENT_CATEGORY_KEYWORDS:
        if any(keyword.casefold() in text for keyword in keywords):
            return category
    return "general"


def _diversify_document_ranked(
    ranked: list[tuple[float, dict[str, Any], float, float]],
) -> list[tuple[float, dict[str, Any], float, float]]:
    selected: list[tuple[float, dict[str, Any], float, float]] = []
    used_chunks: set[str] = set()
    for category, _ in DOCUMENT_CATEGORY_KEYWORDS:
        for item in ranked:
            row = item[1]
            chunk_id = str(row["chunk_id"])
            if chunk_id not in used_chunks and _document_category(row) == category:
                selected.append(item)
                used_chunks.add(chunk_id)
                break
    for item in ranked:
        chunk_id = str(item[1]["chunk_id"])
        if chunk_id not in used_chunks:
            selected.append(item)
            used_chunks.add(chunk_id)
    return selected


def topics_overlap(left: Sequence[str], right: Sequence[str]) -> bool:
    if not left or not right:
        return False
    left_text = " ".join(left)
    right_text = " ".join(right)
    return any(_term_matches(term, right_text) for term in left) or any(
        _term_matches(term, left_text) for term in right
    )


def expanded_occurrence_terms(
    occurrence_type: str | None,
    intent: str | None,
) -> tuple[str, ...]:
    # "기타" is the classifier's catch-all "unclassified" label, not an actual
    # hazard type. Using it as a keyword would let the very common word "기타"
    # (matching any document with an "기타 ..." section heading) bypass the
    # topic-relevance filter for completely unrelated documents.
    if not occurrence_type or occurrence_type == "기타":
        return ()
    if intent == "maintenance_guide":
        return MAINTENANCE_OCCURRENCE_EXPANSIONS.get(
            occurrence_type,
            (occurrence_type,),
        )
    return (occurrence_type,)


def _document_profile_text(profile: dict[str, Any]) -> str:
    return " ".join(
        str(profile.get(key) or "")
        for key in ("title", "original_filename", "metadata", "source_type", "sample_text")
    )


DOCUMENT_PROFILE_FIELDS = (
    "product_names",
    "model_names",
    "aliases",
    "equipment",
    "components",
    "supported_tasks",
    "safety_topics",
    "summary_points",
    "document_keywords",
)


def _metadata_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _document_profile_data(profile: dict[str, Any]) -> dict[str, Any]:
    metadata = _metadata_dict(profile.get("metadata"))
    value = profile.get("document_profile")
    if isinstance(value, dict):
        return value
    value = metadata.get("document_profile")
    return value if isinstance(value, dict) else {}


def _profile_values(profile: dict[str, Any], *fields: str) -> tuple[str, ...]:
    data = _document_profile_data(profile)
    values: list[str] = []
    for field in fields:
        raw_values = data.get(field)
        if isinstance(raw_values, str):
            raw_values = [raw_values]
        if not isinstance(raw_values, list):
            continue
        for raw_value in raw_values:
            value = _normalized_document_alias(str(raw_value or ""))
            if _is_useful_document_alias(value) and value not in values:
                values.append(value)
    return tuple(values)


def _profile_text(profile: dict[str, Any]) -> str:
    data = _document_profile_data(profile)
    pieces: list[str] = []
    for field in DOCUMENT_PROFILE_FIELDS:
        values = data.get(field)
        if isinstance(values, list):
            pieces.extend(str(value) for value in values if value)
        elif isinstance(values, str):
            pieces.append(values)
    return " ".join(pieces)


def _row_document_profile(row: dict[str, Any]) -> dict[str, Any] | None:
    value = row.get("document_profile")
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    metadata = _metadata_dict(row.get("document_metadata"))
    profile = metadata.get("document_profile")
    return profile if isinstance(profile, dict) else None


def _normalized_document_alias(value: str) -> str:
    normalized = " ".join(value.split()).strip(" .,:;·-/[]()")
    normalized = re.sub(r"^(?:제품|문서|매뉴얼|시리즈)\s+", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def _alias_tokens(value: str) -> tuple[str, ...]:
    return tuple(
        token.casefold()
        for token in re.findall(r"[0-9A-Za-z가-힣□_-]+", value)
        if len(token) >= 2
    )


def _is_useful_document_alias(value: str) -> bool:
    normalized = _normalized_document_alias(value)
    if len(normalized) < 2 or len(normalized) > 48:
        return False
    tokens = _alias_tokens(normalized)
    if not tokens:
        return False
    if all(token in DOCUMENT_ALIAS_STOPWORDS for token in tokens):
        return False
    if normalized.casefold() in DOCUMENT_ALIAS_STOPWORDS:
        return False
    if normalized in {"제품", "장비", "설비", "기계", "문서", "매뉴얼", "사용 설명서"}:
        return False
    return True


def _document_alias_candidates(text: str) -> tuple[str, ...]:
    suffix_pattern = "|".join(re.escape(suffix) for suffix in DOCUMENT_ALIAS_SUFFIXES)
    candidates: list[str] = []
    for match in re.finditer(
        rf"([0-9A-Za-z가-힣□·/()+_-]+(?:\s+[0-9A-Za-z가-힣□·/()+_-]+){{0,4}}\s*(?:{suffix_pattern}))",
        text,
        flags=re.IGNORECASE,
    ):
        candidates.append(match.group(1))
    for match in re.finditer(
        r"\b((?:AC|DC)?\s*\d+\s*선식|[0-9]+\s*[-~]\s*[0-9]+\s*V|[0-9]+\s*wire)\b",
        text,
        flags=re.IGNORECASE,
    ):
        candidates.append(match.group(1))
    return tuple(candidates)


def _document_model_aliases(text: str) -> tuple[str, ...]:
    aliases: list[str] = []

    def add(value: str) -> None:
        normalized = _normalized_document_alias(value)
        if _is_useful_document_alias(normalized) and normalized not in aliases:
            aliases.append(normalized)

    metadata = text.replace("_", " ").replace("-", " ")
    for token in re.findall(r"(?<![A-Za-z0-9])([A-Za-z]{1,8}[A-Za-z0-9]{0,16})(?![A-Za-z0-9])", metadata):
        lowered = token.casefold()
        if lowered in DOCUMENT_ALIAS_STOPWORDS:
            continue
        if token.isupper() or any(char.isdigit() for char in token):
            add(token)
            if 2 <= len(token) <= 8:
                add(f"{token} Series")
    for token in re.findall(r"(?<![A-Za-z0-9])([A-Za-z]{1,8}[A-Za-z0-9_-]{1,24})(?![A-Za-z0-9])", text):
        lowered = token.casefold().strip("_-")
        if lowered in DOCUMENT_ALIAS_STOPWORDS:
            continue
        if any(char.isdigit() for char in token) or token.upper() == token:
            add(token)
    return tuple(aliases)


def _document_profile_aliases(profile: dict[str, Any]) -> tuple[str, ...]:
    text = _document_profile_text(profile)
    aliases: list[str] = []

    def add(*values: str) -> None:
        for value in values:
            normalized = _normalized_document_alias(value)
            if _is_useful_document_alias(normalized) and normalized not in aliases:
                aliases.append(normalized)

    title = str(profile.get("title") or "")
    filename = str(profile.get("original_filename") or "")
    metadata_text = f"{title} {filename}"
    add(
        *_profile_values(
            profile,
            "product_names",
            "model_names",
            "aliases",
            "components",
            "equipment",
        )
    )
    for alias in _document_model_aliases(metadata_text):
        add(alias)
    for alias in _document_alias_candidates(metadata_text):
        add(alias)
    for alias in _document_alias_candidates(text[:8000]):
        add(alias)
    for term in DOCUMENT_SPEC_TERMS:
        if term in text:
            add(term)
    return tuple(dict.fromkeys(aliases))


def _document_profile_score(
    question: str,
    profile: dict[str, Any],
    aliases: Sequence[str],
) -> float:
    question_text = question.casefold()
    question_compact = _compact_for_phrase(question_text)
    profile_text = _document_profile_text(profile).casefold()
    structured_profile_text = _profile_text(profile).casefold()
    metadata_text = " ".join(
        str(profile.get(key) or "")
        for key in ("title", "original_filename", "metadata", "source_type")
    ).casefold()
    structured_aliases = _profile_values(
        profile,
        "product_names",
        "model_names",
        "aliases",
    )
    score = 0.0
    for alias in structured_aliases:
        alias_compact = _compact_for_phrase(alias)
        if alias_compact and alias_compact in question_compact:
            score += 4.0 + min(len(alias_compact), 24) * 0.05
    for alias in aliases:
        alias_compact = _compact_for_phrase(alias)
        if not alias_compact:
            continue
        if alias_compact in question_compact:
            score += 2.4 + min(len(alias_compact), 20) * 0.04
        elif alias_compact in _compact_for_phrase(profile_text):
            alias_terms = topic_terms(alias)
            question_terms = topic_terms(question)
            if alias_terms and topics_overlap(alias_terms, question_terms):
                score += 0.45

    for term in topic_terms(question):
        if _term_matches(term, metadata_text):
            score += 1.2
        elif _term_matches(term, structured_profile_text):
            score += 1.0
        elif _term_matches(term, profile_text):
            score += 0.55
    if "series" in question_text and any(
        _compact_for_phrase(alias) in question_compact for alias in aliases
    ):
        score += 0.7
    return score


def _document_route_query_terms(
    profile: dict[str, Any],
    aliases: Sequence[str],
) -> tuple[str, ...]:
    terms: list[str] = []
    terms.extend(
        _profile_values(
            profile,
            "product_names",
            "model_names",
            "aliases",
            "components",
            "equipment",
            "supported_tasks",
            "safety_topics",
            "document_keywords",
        )
    )
    for alias in aliases:
        if 2 <= len(alias) <= 36:
            terms.append(alias)
    profile_text = _document_profile_text(profile)
    terms.extend(term for term in DOCUMENT_SPEC_TERMS if term in profile_text)
    terms.extend(topic_terms(str(profile.get("title") or ""))[:8])
    terms.extend(topic_terms(str(profile.get("original_filename") or ""))[:8])
    return tuple(dict.fromkeys(terms))[:24]


class BgeM3Embedder:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._model = None
        self._load_lock = Lock()
        self._encode_lock = Lock()

    def _get_model(self):
        if self._model is None:
            with self._load_lock:
                if self._model is None:
                    from sentence_transformers import SentenceTransformer

                    self._model = SentenceTransformer(
                        self.settings.model_name,
                        device=self.settings.device,
                        cache_folder=self.settings.model_cache_dir,
                    )
        return self._model

    def encode(self, text: str) -> np.ndarray:
        normalized = normalize_text(text)
        if not normalized:
            raise RetrievalError("The search query is empty.")
        with self._encode_lock:
            vector = self._get_model().encode(
                [normalized],
                batch_size=1,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )[0]
        return np.asarray(vector, dtype=np.float32)

    def encode_many(self, texts: Sequence[str], batch_size: int = 16) -> np.ndarray:
        normalized = [normalize_text(text) for text in texts]
        if not normalized or any(not text for text in normalized):
            raise RetrievalError("Embedding input must contain non-empty text.")
        with self._encode_lock:
            vectors = self._get_model().encode(
                normalized,
                batch_size=batch_size,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        return np.asarray(vectors, dtype=np.float32)


class PgvectorRetriever:
    def __init__(self, settings: Settings, embedder: BgeM3Embedder | None = None) -> None:
        self.settings = settings
        self.embedder = embedder or BgeM3Embedder(settings)

    def build_search_query(self, request: ChatRequest) -> str:
        analysis_keywords = request.analysis.search_keywords if request.analysis else []
        context_values: Sequence[str | None] = (
            request.context.equipment_name,
            request.context.manufacturer,
            request.context.model_number,
            request.context.component_name,
            request.context.task_type,
            request.context.energy_source,
            request.context.task_description,
        )
        context_text = " ".join(
            value.strip() for value in context_values if value and value.strip()
        )
        question_topics = topic_terms(request.question)
        context_topics = topic_terms(context_text)
        include_context = not question_topics or topics_overlap(
            question_topics, context_topics
        )
        relevant_analysis_keywords = [
            keyword
            for keyword in analysis_keywords
            if keyword.casefold() not in request.question.casefold()
            and (
                not question_topics
                or topics_overlap(question_topics, topic_terms(keyword))
            )
        ]
        intent = request.analysis.question_intent if request.analysis else None
        occurrence_type = request.analysis.occurrence_type if request.analysis else None
        occurrence_terms = expanded_occurrence_terms(occurrence_type, intent)
        if intent != "maintenance_guide" and occurrence_type:
            occurrence_terms = (
                occurrence_terms
                if not question_topics
                or topics_overlap(question_topics, topic_terms(occurrence_type))
                else ()
            )
        values: list[str | None] = [request.question]
        domain_alias_terms = domain_phrase_query_terms(
            " ".join(value for value in (request.question, context_text) if value)
        )
        if domain_alias_terms:
            values.append(" ".join(domain_alias_terms))
        if include_context:
            values.append(context_text)
        if request.analysis:
            target_values = [
                *request.analysis.equipment,
                *request.analysis.component,
            ]
            relevant_targets = [
                value
                for value in target_values
                if (
                    not question_topics
                    or topics_overlap(question_topics, topic_terms(value))
                )
            ]
            explicit_risks = [
                value
                for value in request.analysis.explicit_risk_factors
                if (
                    value.casefold() in request.question.casefold()
                    or not question_topics
                    or topics_overlap(question_topics, topic_terms(value))
                )
            ]
            values.extend(
                (
                    " ".join(relevant_targets),
                    " ".join(occurrence_terms),
                    request.analysis.work_type,
                    (
                        " ".join(maintenance_query_expansions(request.question))
                        if intent == "maintenance_guide"
                        else None
                    ),
                    " ".join(explicit_risks),
                    " ".join(request.analysis.energy_sources),
                    " ".join(relevant_analysis_keywords),
                )
            )
        return " ".join(value.strip() for value in values if value and value.strip())

    @staticmethod
    def _intent_source_types(request: InternalChatRequest) -> tuple[str, ...] | None:
        intent = request.analysis.question_intent if request.analysis else None
        if intent == "component_info":
            source_types = list(COMPONENT_DOCUMENT_TYPES)
            question = request.question.casefold()
            if not any(term in question for term in ("법", "법령", "규정", "기준")):
                source_types.remove("public_law")
            return tuple(source_types)
        if intent == "maintenance_guide":
            return MAINTENANCE_DOCUMENT_TYPES
        return None

    @staticmethod
    def _active_version_clause() -> str:
        return """
          (
              (d.current_version_id IS NULL AND dc.document_version_id IS NULL)
              OR (
                  dc.document_version_id = d.current_version_id
                  AND dv.status = 'active'
                  AND dv.is_active = true
              )
          )
        """

    def ready_chunk_count(
        self,
        source_types: Sequence[str] | None = None,
        document_ids: Sequence[UUID] | None = None,
    ) -> int:
        resolved_source_types = normalize_source_types(
            self.settings.source_types if source_types is None else source_types
        )
        resolved_document_ids = normalize_document_ids(document_ids)
        scope_clause, scope_parameters = scope_sql(
            resolved_source_types,
            resolved_document_ids,
        )
        query = f"""
            SELECT COUNT(*)
            FROM document_chunks dc
            JOIN documents d ON d.id = dc.document_id
            JOIN document_types dt ON dt.code = d.document_type_code
            LEFT JOIN document_versions dv ON dv.id = dc.document_version_id
            WHERE d.lifecycle_status = 'active'
              AND d.deleted_at IS NULL
              AND dt.is_active = true
              AND dt.scope = 'public'
              AND d.access_level = 'public'
              AND {self._active_version_clause()}
              {scope_clause}
              AND dc.embedding_status = 'ready'
              AND dc.embedding IS NOT NULL
              AND dc.embedding_model = %s
        """
        with psycopg.connect(
            psycopg_database_url(self.settings.database_url),
            connect_timeout=5,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, (*scope_parameters, self.settings.model_name))
                return int(cursor.fetchone()[0])

    def _route_selected_documents(
        self,
        request: InternalChatRequest,
        source_types: tuple[str, ...] | None,
        explicit_document_ids: tuple[UUID, ...] | None,
    ) -> tuple[tuple[UUID, ...] | None, tuple[str, ...]]:
        selected_documents = request.context.effective_document_ids()
        if (
            explicit_document_ids is not None
            or len(selected_documents) <= 1
            or request.context.selected_document_version_ids
        ):
            return None, ()
        profiles = self._load_selected_document_profiles(
            request,
            selected_documents,
            source_types,
        )
        if len(profiles) <= 1:
            return None, ()

        scored: list[tuple[float, dict[str, Any], tuple[str, ...]]] = []
        for profile in profiles:
            aliases = _document_profile_aliases(profile)
            score = _document_profile_score(request.question, profile, aliases)
            scored.append((score, profile, aliases))
        scored.sort(key=lambda item: item[0], reverse=True)
        best_score, best_profile, best_aliases = scored[0]
        second_score = scored[1][0] if len(scored) > 1 else 0.0
        if best_score < 2.0 or best_score - second_score < 0.85:
            return None, ()
        try:
            document_id = UUID(str(best_profile["document_id"]))
        except ValueError:
            return None, ()
        return (document_id,), _document_route_query_terms(best_profile, best_aliases)

    def _load_selected_document_profiles(
        self,
        request: InternalChatRequest,
        selected_documents: Sequence[UUID],
        source_types: tuple[str, ...] | None,
    ) -> list[dict[str, Any]]:
        allowed_source_types = tuple(source_types or MANUAL_DOCUMENT_TYPES)
        query = """
            SELECT
                d.id::text AS document_id,
                d.title,
                d.metadata::text AS metadata,
                d.metadata->'document_profile' AS document_profile,
                d.document_type_code AS source_type,
                dv.id::text AS document_version_id,
                dv.original_filename,
                COALESCE(sample.sample_text, '') AS sample_text
            FROM documents d
            JOIN document_types dt ON dt.code = d.document_type_code
            LEFT JOIN document_versions dv
              ON dv.document_id = d.id
             AND (
                  (
                      d.lifecycle_status = 'active'
                      AND (
                          d.current_version_id = dv.id
                          OR (d.current_version_id IS NULL AND dv.is_active = true)
                      )
                  )
                  OR (
                      d.lifecycle_status = 'review_required'
                      AND dv.status = 'review_required'
                      AND dv.uploaded_by_user_id = %s::uuid
                      AND dv.version_number = (
                          SELECT MAX(owner_version.version_number)
                          FROM document_versions owner_version
                          WHERE owner_version.document_id = d.id
                            AND owner_version.status = 'review_required'
                            AND owner_version.uploaded_by_user_id = %s::uuid
                      )
                  )
             )
            LEFT JOIN LATERAL (
                SELECT string_agg(chunk.content, ' ' ORDER BY chunk.sort_bucket, chunk.chunk_index) AS sample_text
                FROM (
                    SELECT
                        dc.content,
                        dc.chunk_index,
                        CASE
                            WHEN COALESCE(dc.page_number, dc.page_start, 9999) <= 4 THEN 0
                            ELSE 1
                        END AS sort_bucket
                    FROM document_chunks dc
                    WHERE dc.document_id = d.id
                      AND (dv.id IS NULL OR dc.document_version_id = dv.id)
                      AND dc.embedding_status = 'ready'
                      AND dc.embedding IS NOT NULL
                      AND dc.embedding_model = %s
                    ORDER BY
                        CASE
                            WHEN COALESCE(dc.page_number, dc.page_start, 9999) <= 4 THEN 0
                            ELSE 1
                        END,
                        dc.chunk_index
                    LIMIT 40
                ) chunk
            ) sample ON true
            WHERE d.deleted_at IS NULL
              AND d.id = ANY(%s::uuid[])
              AND d.document_type_code = ANY(%s)
              AND dt.is_active = true
              AND (
                  (
                      d.lifecycle_status = 'active'
                      AND (
                          (
                              dt.scope = 'public'
                              AND d.access_level = 'public'
                          )
                          OR (
                              %s
                              AND dt.scope = 'company'
                              AND (%s OR d.access_level <> 'private')
                              AND (%s OR d.site_id::text = ANY(%s))
                          )
                      )
                  )
                  OR (
                      d.lifecycle_status = 'review_required'
                      AND dt.scope = 'company'
                      AND dv.id IS NOT NULL
                  )
              )
        """
        with psycopg.connect(
            psycopg_database_url(self.settings.database_url),
            connect_timeout=5,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    query,
                    (
                        request.access_scope.requester_user_id,
                        request.access_scope.requester_user_id,
                        self.settings.model_name,
                        list(selected_documents),
                        list(allowed_source_types),
                        request.access_scope.allow_company,
                        request.access_scope.allow_private,
                        request.access_scope.all_sites,
                        request.access_scope.site_ids,
                    ),
                )
                return list(cursor.fetchall())

    def _candidate_query(
        self,
        scope_clause: str,
    ) -> str:
        return f"""
            WITH candidates AS (
                SELECT
                    d.id::text AS document_id,
                    dc.id::text AS chunk_id,
                    dc.chunk_index,
                    d.title,
                    d.document_type_code AS source_type,
                    dt.scope AS document_scope,
                    d.metadata::text AS document_metadata,
                    d.metadata->'document_profile' AS document_profile,
                    dv.id::text AS document_version_id,
                    dv.original_filename,
                    dv.version_number AS document_version,
                    COALESCE(dc.metadata->>'section', dc.section_path->>0) AS section,
                    dc.content,
                    dc.content_hash,
                    COALESCE(dc.page_number, dc.page_start) AS page,
                    dc.page_start,
                    dc.page_end,
                    d.publisher,
                    d.source_url AS url,
                    1 - (dc.embedding <=> %s) AS similarity,
                    ts_rank_cd(
                        to_tsvector('simple', COALESCE(dc.content, '')),
                        plainto_tsquery('simple', %s)
                    ) AS postgres_keyword_score
                FROM document_chunks dc
                JOIN documents d ON d.id = dc.document_id
                JOIN document_types dt ON dt.code = d.document_type_code
                LEFT JOIN document_versions dv ON dv.id = dc.document_version_id
                WHERE d.deleted_at IS NULL
                  AND dt.is_active = true
                  AND (
                      (
                          d.lifecycle_status = 'active'
                          AND {self._active_version_clause()}
                          AND (
                              (
                                  dt.scope = 'public'
                                  AND d.access_level = 'public'
                              )
                              OR (
                                  %s
                                  AND dt.scope = 'company'
                                  AND (%s OR d.access_level <> 'private')
                                  AND (%s OR d.site_id::text = ANY(%s))
                              )
                          )
                      )
                      OR (
                          NOT %s
                          AND d.id = ANY(%s::uuid[])
                          AND dt.scope = 'company'
                          AND dc.document_version_id = dv.id
                          AND dv.status = 'review_required'
                          AND dv.uploaded_by_user_id = %s::uuid
                          AND dv.version_number = (
                              SELECT MAX(owner_version.version_number)
                              FROM document_versions owner_version
                              WHERE owner_version.document_id = d.id
                                AND owner_version.status = 'review_required'
                                AND owner_version.uploaded_by_user_id = %s::uuid
                          )
                      )
                  )
                  AND (
                      %s
                      OR d.id = ANY(%s::uuid[])
                      OR (
                          %s
                          AND dt.scope = 'public'
                          AND d.access_level = 'public'
                      )
                  )
                  AND (
                      %s
                      OR dv.id = ANY(%s::uuid[])
                      OR (
                          %s
                          AND dt.scope = 'public'
                          AND d.access_level = 'public'
                      )
                  )
                  {scope_clause}
                  AND dc.embedding_status = 'ready'
                  AND dc.embedding IS NOT NULL
                  AND dc.embedding_model = %s
                  AND dc.embedding_dimension = %s
            )
            SELECT *
            FROM candidates
            WHERE similarity >= %s OR postgres_keyword_score > 0
            ORDER BY
                (similarity * 0.75 + LEAST(postgres_keyword_score, 1.0) * 0.25) DESC,
                chunk_id
            LIMIT %s
        """

    @staticmethod
    def _adjacent_query(seed_count: int) -> str:
        seed_predicates = " OR ".join(
            (
                "(d.id::text = %s "
                "AND COALESCE(dc.document_version_id::text, '') = %s "
                "AND dc.chunk_index BETWEEN %s AND %s)"
            )
            for _ in range(seed_count)
        )
        return f"""
            SELECT
                d.id::text AS document_id,
                dc.id::text AS chunk_id,
                dc.chunk_index,
                d.title,
                d.document_type_code AS source_type,
                dt.scope AS document_scope,
                d.metadata::text AS document_metadata,
                d.metadata->'document_profile' AS document_profile,
                dv.id::text AS document_version_id,
                dv.original_filename,
                dv.version_number AS document_version,
                COALESCE(dc.metadata->>'section', dc.section_path->>0) AS section,
                dc.content,
                dc.content_hash,
                COALESCE(dc.page_number, dc.page_start) AS page,
                dc.page_start,
                dc.page_end,
                d.publisher,
                d.source_url AS url,
                0.0::double precision AS similarity,
                0.0::double precision AS postgres_keyword_score
            FROM document_chunks dc
            JOIN documents d ON d.id = dc.document_id
            JOIN document_types dt ON dt.code = d.document_type_code
            LEFT JOIN document_versions dv ON dv.id = dc.document_version_id
            WHERE d.deleted_at IS NULL
              AND dt.is_active = true
              AND dc.embedding_status = 'ready'
              AND dc.embedding IS NOT NULL
              AND dc.embedding_model = %s
              AND ({seed_predicates})
            ORDER BY d.id, dc.document_version_id, dc.chunk_index
        """

    def _load_adjacent_rows(
        self,
        cursor: Any,
        seed_rows: Sequence[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        window = max(0, self.settings.document_neighbor_window)
        seeds: list[tuple[str, str, int, float]] = []
        seen: set[tuple[str, str, int]] = set()
        for row in seed_rows:
            if row.get("chunk_index") is None:
                continue
            key = (
                str(row["document_id"]),
                str(row.get("document_version_id") or ""),
                int(row["chunk_index"]),
            )
            if key in seen:
                continue
            seen.add(key)
            seeds.append((*key, float(row["similarity"])))
        if not seeds or window == 0:
            return []

        parameters: list[object] = [self.settings.model_name]
        for document_id, version_id, chunk_index, _ in seeds:
            parameters.extend(
                (
                    document_id,
                    version_id,
                    max(0, chunk_index - window),
                    chunk_index + window,
                )
            )
        cursor.execute(self._adjacent_query(len(seeds)), tuple(parameters))
        adjacent_rows = list(cursor.fetchall())
        for row in adjacent_rows:
            document_id = str(row["document_id"])
            version_id = str(row.get("document_version_id") or "")
            chunk_index = int(row["chunk_index"])
            nearest_scores = [
                max(
                    self.settings.min_similarity,
                    similarity - abs(seed_index - chunk_index) * 0.01,
                )
                for seed_document_id, seed_version_id, seed_index, similarity in seeds
                if seed_document_id == document_id
                and seed_version_id == version_id
                and abs(seed_index - chunk_index) <= window
            ]
            if nearest_scores:
                row["similarity"] = max(nearest_scores)
        return adjacent_rows

    def search(
        self,
        request: InternalChatRequest,
        source_types: Sequence[str] | None = None,
        document_ids: Sequence[UUID] | None = None,
    ) -> list[ChatSource]:
        configured_source_types = (
            self.settings.source_types
            if source_types is None and self.settings.source_types is not None
            else source_types
        )
        resolved_source_types = normalize_source_types(
            configured_source_types
            if configured_source_types is not None
            else self._intent_source_types(request)
        )
        resolved_document_ids = normalize_document_ids(document_ids)
        intent = (
            request.analysis.question_intent
            if request.analysis
            else None
        )
        if (
            intent == "document_qa"
            and not resolved_document_ids
            and not request.context.effective_document_ids()
            and not request.context.selected_document_version_ids
        ):
            logger.info(
                "rag_retrieval %s",
                json.dumps(
                    {
                        "request_id": request.request_id,
                        "question_intent": intent,
                        "candidate_bucket_counts": {},
                        "selected_bucket_counts": {},
                        "selected_sources": [],
                        "fallback_reason": "document_not_selected",
                    },
                    ensure_ascii=False,
                ),
            )
            return []
        routed_document_ids, route_query_terms = self._route_selected_documents(
            request,
            resolved_source_types,
            resolved_document_ids,
        )
        if routed_document_ids is not None:
            resolved_document_ids = routed_document_ids
            request = request.model_copy(
                update={
                    "context": request.context.model_copy(
                        update={
                            "selected_document_ids": list(routed_document_ids),
                            "selected_document_version_ids": [],
                        }
                    )
                }
            )
        scope_clause, scope_parameters = scope_sql(
            resolved_source_types,
            resolved_document_ids,
        )
        search_query = self.build_search_query(request)
        if route_query_terms:
            search_query = f"{search_query} {' '.join(route_query_terms)}"
        vector = self.embedder.encode(search_query)
        selected_documents = request.context.effective_document_ids()
        selected_versions = tuple(request.context.selected_document_version_ids)
        include_all_documents = not selected_documents
        include_all_versions = not selected_versions
        include_public_supplement = self._include_public_supplement(
            request,
            document_ids,
        )
        parameters = (
            vector,
            search_query,
            request.access_scope.allow_company,
            request.access_scope.allow_private,
            request.access_scope.all_sites,
            request.access_scope.site_ids,
            include_all_documents,
            list(selected_documents),
            request.access_scope.requester_user_id,
            request.access_scope.requester_user_id,
            include_all_documents,
            list(selected_documents),
            include_public_supplement,
            include_all_versions,
            list(selected_versions),
            include_public_supplement,
            *scope_parameters,
            self.settings.model_name,
            int(vector.shape[0]),
            self.settings.min_similarity,
            self._candidate_limit(
                request.analysis.question_intent if request.analysis else None
            ),
        )
        with psycopg.connect(
            psycopg_database_url(self.settings.database_url),
            connect_timeout=5,
            row_factory=dict_row,
        ) as connection:
            register_vector(connection)
            with connection.cursor() as cursor:
                cursor.execute(self._candidate_query(scope_clause), parameters)
                rows = list(cursor.fetchall())
                if intent == "maintenance_guide":
                    existing_chunk_ids = {str(row["chunk_id"]) for row in rows}
                    for supplemental_source_types in (
                        ("company_policy",),
                        ("public_law", "public_guide", "public_media"),
                        ("public_incident",),
                    ):
                        supplemental_clause, supplemental_scope_parameters = scope_sql(
                            normalize_source_types(supplemental_source_types),
                            None,
                        )
                        supplemental_parameters = (
                            vector,
                            search_query,
                            request.access_scope.allow_company,
                            request.access_scope.allow_private,
                            request.access_scope.all_sites,
                            request.access_scope.site_ids,
                            True,
                            [],
                            request.access_scope.requester_user_id,
                            request.access_scope.requester_user_id,
                            True,
                            [],
                            True,
                            True,
                            [],
                            True,
                            *supplemental_scope_parameters,
                            self.settings.model_name,
                            int(vector.shape[0]),
                            self.settings.min_similarity,
                            max(20, self.settings.candidate_k),
                        )
                        cursor.execute(
                            self._candidate_query(supplemental_clause),
                            supplemental_parameters,
                        )
                        for row in cursor.fetchall():
                            chunk_id = str(row["chunk_id"])
                            if chunk_id not in existing_chunk_ids:
                                rows.append(row)
                                existing_chunk_ids.add(chunk_id)
                if intent == "document_qa":
                    adjacent_rows = self._load_adjacent_rows(cursor, rows)
                    existing_chunk_ids = {
                        str(row["chunk_id"]) for row in rows
                    }
                    rows.extend(
                        row
                        for row in adjacent_rows
                        if str(row["chunk_id"]) not in existing_chunk_ids
                    )
        sources = self._rerank(request, rows)
        candidate_bucket_counts: dict[str, int] = {}
        for row in rows:
            bucket = self._source_bucket(str(row.get("source_type") or ""))
            candidate_bucket_counts[bucket] = (
                candidate_bucket_counts.get(bucket, 0) + 1
            )
        selected_bucket_counts: dict[str, int] = {}
        for source in sources:
            bucket = self._source_bucket(source.source_type)
            selected_bucket_counts[bucket] = (
                selected_bucket_counts.get(bucket, 0) + 1
            )
        logger.info(
            "rag_retrieval %s",
            json.dumps(
                {
                    "request_id": request.request_id,
                    "question_intent": (
                        request.analysis.question_intent
                        if request.analysis
                        else None
                    ),
                    "candidate_bucket_counts": candidate_bucket_counts,
                    "selected_bucket_counts": selected_bucket_counts,
                    "selected_sources": [
                        {
                            "document_id": source.document_id,
                            "chunk_id": source.chunk_id,
                            "document_type": source.source_type,
                            "score": source.reranker_score,
                        }
                        for source in sources
                    ],
                },
                ensure_ascii=False,
            ),
        )
        return sources

    @staticmethod
    def _include_public_supplement(
        request: InternalChatRequest,
        document_ids: tuple[UUID, ...] | None,
    ) -> bool:
        intent = request.analysis.question_intent if request.analysis else None
        return bool(
            request.context.effective_document_ids()
            and request.access_scope.allow_company
            and document_ids is None
            and intent in {"maintenance_guide", "component_info"}
        )

    def _rerank(
        self,
        request: InternalChatRequest,
        rows: Sequence[dict[str, Any]],
    ) -> list[ChatSource]:
        query = self.build_search_query(request)
        query_terms = tokenize(query)
        query_action_terms = action_terms(request.question)
        context_topic_values = [
            request.context.equipment_name,
            request.context.manufacturer,
            request.context.model_number,
            request.context.component_name,
        ]
        if request.analysis:
            context_topic_values.extend(request.analysis.equipment)
            context_topic_values.extend(request.analysis.component)
        context_topics = topic_terms(
            " ".join(value for value in context_topic_values if value)
        )
        question_topics = topic_terms(request.question)
        active_topic_terms = question_topics or context_topics
        if not active_topic_terms:
            active_topic_terms = tuple(
                term for term in query_terms if term not in GENERIC_QUERY_TERMS
            )
        intent = request.analysis.question_intent if request.analysis else None
        domain_phrase_indexes = domain_phrase_group_indexes(
            " ".join(
                value
                for value in (
                    request.question,
                    *context_topic_values,
                )
                if value
            )
        )
        maintenance_expansion_terms = (
            maintenance_query_expansions(request.question)
            if intent == "maintenance_guide"
            else ()
        )
        occurrence_terms = expanded_occurrence_terms(
            request.analysis.occurrence_type if request.analysis else None,
            intent,
        )
        selected_document_ids = {
            str(document_id) for document_id in request.context.effective_document_ids()
        }
        selected_topic_phrase_terms = _topic_phrase_terms(request.question)
        selected_documents_match_topic_phrase = bool(
            selected_document_ids
            and selected_topic_phrase_terms
            and any(
                str(row["document_id"]) in selected_document_ids
                and _topic_phrase_matches(selected_topic_phrase_terms, _combined_row_text(row))
                for row in rows
            )
        )

        ranked: list[tuple[float, dict[str, Any], float, float]] = []
        seen_hashes: set[str] = set()
        for row in rows:
            content = str(row["content"])
            metadata_text = " ".join(
                str(value)
                for value in (
                    row["title"],
                    row["section"],
                    row["original_filename"],
                    row["source_type"],
                    row.get("document_metadata"),
                )
                if value
            )
            combined = f"{metadata_text} {content}".casefold()
            row_document_id = str(row["document_id"])
            document_scope = str(row["document_scope"] or "").casefold()
            source_type = canonical_document_type(str(row["source_type"] or ""))
            is_selected_document = row_document_id in selected_document_ids
            domain_phrase_match = _domain_phrase_matches(
                domain_phrase_indexes,
                combined,
            )
            topic_phrase_match = _topic_phrase_matches(
                selected_topic_phrase_terms,
                combined,
            )
            conveyor_incident_topic_match = _conveyor_incident_topic_matches(
                active_topic_terms,
                combined,
                source_type=source_type,
            )
            topic_phrase_partial_collision = bool(
                selected_documents_match_topic_phrase
                and selected_topic_phrase_terms
                and not topic_phrase_match
                and any(_term_matches(term, combined) for term in selected_topic_phrase_terms)
            )
            if (
                domain_phrase_indexes
                and _domain_phrase_negative_matches(domain_phrase_indexes, combined)
                and not domain_phrase_match
            ):
                continue
            occurrence_match = bool(
                intent == "maintenance_guide"
                and occurrence_terms
                and source_type
                in {
                    "company_policy",
                    "public_law",
                    "public_guide",
                    "public_media",
                    "public_incident",
                }
                and any(_term_matches(term, combined) for term in occurrence_terms)
            )
            maintenance_safety_match = bool(
                intent == "maintenance_guide"
                and source_type in MAINTENANCE_DOCUMENT_TYPES
                and _maintenance_expansion_match(maintenance_expansion_terms, combined)
            )
            if (
                domain_phrase_indexes
                and document_scope == "public"
                and not is_selected_document
                and not domain_phrase_match
                and not occurrence_match
            ):
                continue
            if (
                intent == "maintenance_guide"
                and selected_documents_match_topic_phrase
                and not is_selected_document
                and document_scope == "public"
                and not topic_phrase_match
                and not conveyor_incident_topic_match
                and not occurrence_match
                and not (maintenance_safety_match and not topic_phrase_partial_collision)
            ):
                continue
            topic_match_count = sum(
                1 for term in active_topic_terms if _term_matches(term, combined)
            )
            has_topic_match = (
                not active_topic_terms
                or topic_match_count >= _topic_match_threshold(len(active_topic_terms))
                or conveyor_incident_topic_match
            )
            if (
                not has_topic_match
                and is_selected_document
                and len(selected_document_ids) == 1
                and source_type in MANUAL_DOCUMENT_TYPES
            ):
                relaxed_threshold = max(
                    1,
                    _topic_match_threshold(len(active_topic_terms)) - 1,
                )
                has_topic_match = topic_match_count >= relaxed_threshold
            if (
                active_topic_terms
                and not has_topic_match
                and not occurrence_match
                and not maintenance_safety_match
                and not domain_phrase_match
            ):
                continue
            keyword = lexical_score(query_terms, combined)
            similarity = float(row["similarity"])
            if (
                similarity < self.settings.min_similarity
                and keyword < self.settings.min_keyword_score
            ):
                continue
            content_hash = str(row["content_hash"])
            if content_hash in seen_hashes:
                continue
            seen_hashes.add(content_hash)
            metadata_score = lexical_score(
                active_topic_terms, metadata_text.casefold()
            )
            action_score = lexical_score(query_action_terms, combined)
            safety_score = safety_signal_score(combined)
            retrieval_score = max(0.0, similarity) * 0.7 + keyword * 0.3
            reranker_score = retrieval_score * 0.9 + metadata_score * 0.1
            if domain_phrase_match:
                reranker_score += 0.18
                if source_type in MANUAL_DOCUMENT_TYPES:
                    reranker_score += 0.12
            if intent == "maintenance_guide":
                if source_type in MANUAL_DOCUMENT_TYPES:
                    reranker_score += 0.08
                elif source_type in {
                    "company_policy",
                    "public_law",
                    "public_guide",
                    "public_media",
                }:
                    reranker_score += 0.03
                elif source_type == "public_incident":
                    reranker_score += 0.01
                reranker_score += action_score * 0.12 + safety_score * 0.04
                if query_action_terms and action_score == 0.0 and document_scope == "public":
                    reranker_score = max(0.0, reranker_score - 0.06)
                row_action_terms = action_terms(combined)
                if (
                    query_action_terms
                    and row_action_terms
                    and not set(query_action_terms).intersection(row_action_terms)
                    and not (
                        source_type == "public_incident"
                        and has_topic_match
                        and _incident_maintenance_actions_compatible(
                            query_action_terms,
                            row_action_terms,
                        )
                    )
                    and not (
                        maintenance_safety_match
                        and set(row_action_terms).issubset(SAFETY_CONTROL_ACTION_TERMS)
                    )
                ):
                    continue
            elif intent == "component_info":
                if source_type == "component_manual":
                    reranker_score += 0.08
                elif source_type == "equipment_manual":
                    reranker_score += 0.05
                elif source_type == "public_guide":
                    reranker_score += 0.02
                elif source_type == "public_incident":
                    continue
            row["source_type"] = source_type
            ranked.append((reranker_score, row, keyword, retrieval_score))

        ranked.sort(key=lambda item: (-item[0], item[1]["chunk_id"]))
        if intent == "document_qa":
            ranked = _diversify_document_ranked(ranked)
        per_document: dict[str, int] = {}
        per_bucket: dict[str, int] = {}
        sources: list[ChatSource] = []
        max_chunks_per_document = self.settings.max_chunks_per_document
        if intent == "document_qa":
            max_chunks_per_document = max(
                max_chunks_per_document,
                min(6, self.settings.document_top_k),
            )
        result_limit = self._result_limit(intent)
        bucket_quotas = self._bucket_quotas(intent)
        ordered_ranked = self._maintenance_group_first_ranked(ranked) if intent == "maintenance_guide" else ranked
        for reranker_score, row, keyword, retrieval_score in ordered_ranked:
            document_id = row["document_id"]
            if per_document.get(document_id, 0) >= max_chunks_per_document:
                continue
            bucket = self._source_bucket(str(row["source_type"]))
            quota = bucket_quotas.get(bucket)
            if quota is not None and per_bucket.get(bucket, 0) >= quota:
                continue
            per_document[document_id] = per_document.get(document_id, 0) + 1
            per_bucket[bucket] = per_bucket.get(bucket, 0) + 1
            sources.append(
                ChatSource(
                    document_id=document_id,
                    document_version_id=row.get("document_version_id"),
                    chunk_id=row["chunk_id"],
                    title=row["title"],
                    source_type=row["source_type"],
                    document_scope=row["document_scope"],
                    original_filename=row["original_filename"],
                    document_version=row["document_version"],
                    section=row["section"],
                    excerpt=str(row["content"])[:700],
                    page=row["page"],
                    page_start=row["page_start"],
                    page_end=row["page_end"],
                    publisher=row["publisher"],
                    url=row["url"],
                    similarity=float(row["similarity"]),
                    keyword_score=keyword,
                    retrieval_score=max(0.0, retrieval_score),
                    reranker_score=max(0.0, reranker_score),
                    document_profile=_row_document_profile(row),
                )
            )
            if len(sources) >= result_limit:
                break
        return sources

    def _candidate_limit(self, intent: str | None) -> int:
        base = max(self.settings.candidate_k, self._result_limit(intent))
        if intent == "maintenance_guide":
            return max(base, self.settings.maintenance_top_k * 8, 80)
        return base

    @staticmethod
    def _maintenance_group_first_ranked(
        ranked: list[tuple[float, dict[str, Any], float, float]],
    ) -> list[tuple[float, dict[str, Any], float, float]]:
        buckets = (
            "manual",
            "company_policy",
            "public_law",
            "public_guide",
            "public_media",
            "public_incident",
        )
        selected: list[tuple[float, dict[str, Any], float, float]] = []
        used_chunks: set[str] = set()
        for bucket in buckets:
            for item in ranked:
                row = item[1]
                chunk_id = str(row["chunk_id"])
                if chunk_id in used_chunks:
                    continue
                if PgvectorRetriever._source_bucket(str(row["source_type"])) == bucket:
                    selected.append(item)
                    used_chunks.add(chunk_id)
                    break
        for item in ranked:
            chunk_id = str(item[1]["chunk_id"])
            if chunk_id not in used_chunks:
                selected.append(item)
                used_chunks.add(chunk_id)
        return selected

    def _result_limit(self, intent: str | None) -> int:
        if intent == "document_qa":
            return max(1, self.settings.document_top_k)
        if intent == "component_info":
            return max(1, self.settings.component_top_k)
        if intent == "maintenance_guide":
            return max(1, self.settings.maintenance_top_k)
        return max(1, self.settings.top_k)

    def _bucket_quotas(self, intent: str | None) -> dict[str, int]:
        if intent != "maintenance_guide":
            return {}
        return {
            "manual": max(0, self.settings.maintenance_manual_quota),
            "company_policy": max(
                0, self.settings.maintenance_company_policy_quota
            ),
            "public_law": max(0, self.settings.maintenance_law_quota),
            "public_guide": max(0, self.settings.maintenance_guide_quota),
            "public_incident": max(
                0, self.settings.maintenance_incident_quota
            ),
            "public_media": max(0, self.settings.maintenance_guide_quota),
        }

    @staticmethod
    def _source_bucket(source_type: str) -> str:
        canonical = canonical_document_type(source_type)
        if canonical in MANUAL_DOCUMENT_TYPES:
            return "manual"
        return canonical
