#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SafeMaint AI - 안전보건법령 스마트검색 API 수집·전처리기

범위:
- 한국산업안전보건공단 안전보건법령 스마트검색 API(category=0) 수집
- 검색어/페이지 실행 기록, 고유 문서, 검색어-문서 연결 저장
- CSV/JSONL 출력 및 중단 후 이어받기

범위 밖:
- 다른 공공데이터 통합
- rag_documents / rag_chunks 생성
- 청킹, 임베딩, pgvector, LLM, RAG 검색

실행 환경: Python 3.10+, requests
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import math
import os
import re
import sys
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable, Mapping, MutableMapping, Sequence
from urllib.parse import quote, urlencode

import requests
from requests import Response, Session
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import RequestException, Timeout


API_URL = "http://apis.data.go.kr/B552468/srch/smartSearch"
REQUEST_CATEGORY = 0
MAX_RETRIES = 3
RETRY_DELAYS = (2, 4, 8)
VALID_TERM_TYPES = {
    "equipment",
    "part",
    "task",
    "hazard",
    "safety_measure",
    "combined",
}
VALID_RUN_STATUSES = {"success", "error", "quota_exceeded", "interrupted"}

ID_NAMESPACE = uuid.UUID("3b13e867-a761-55bf-9b22-01cf765d2f68")

CATEGORY_NAMES: dict[str, str] = {
    "0": "전체 검색",
    "1": "산업안전보건법",
    "2": "산업안전보건법 시행령",
    "3": "산업안전보건법 시행규칙",
    "4": "산업안전보건기준에 관한 규칙",
    "5": "고시·훈령·예규",
    "6": "안전보건 미디어",
    "7": "KOSHA GUIDE",
    "8": "중대재해처벌법",
    "9": "중대재해처벌법 시행령",
    "11": "유해·위험작업의 취업 제한에 관한 규칙",
}

SOURCE_TYPES: dict[str, str] = {
    "1": "law",
    "2": "law",
    "3": "law",
    "4": "law",
    "5": "notice",
    "6": "media",
    "7": "kosha_guide",
    "8": "law",
    "9": "law",
    "11": "law",
}

TABLE_COLUMNS: dict[str, list[str]] = {
    "smart_search_terms": [
        "term_id",
        "term",
        "normalized_term",
        "term_type",
        "priority",
        "term_source",
        "is_active",
        "created_at",
        "updated_at",
    ],
    "smart_search_runs": [
        "run_id",
        "term_id",
        "search_value",
        "request_category",
        "page_no",
        "num_of_rows",
        "total_count",
        "total_pages",
        "associated_words_json",
        "category_count_json",
        "result_code",
        "result_msg",
        "http_status",
        "status",
        "error_message",
        "requested_at",
        "completed_at",
        "elapsed_seconds",
    ],
    "smart_search_documents": [
        "document_id",
        "source_doc_id",
        "category_code",
        "category_name",
        "source_type",
        "title",
        "content_raw",
        "content_clean",
        "keyword_raw",
        "keyword_clean",
        "filepath",
        "image_path_json",
        "med_thumb_yn",
        "media_style",
        "content_hash",
        "first_seen_at",
        "last_seen_at",
        "updated_at",
    ],
    "smart_search_hits": [
        "hit_id",
        "run_id",
        "term_id",
        "document_id",
        "result_group",
        "rank_no",
        "score",
        "matched_content_raw",
        "matched_content_clean",
        "highlight_content_raw",
        "highlight_content_clean",
        "collected_at",
        "updated_at",
    ],
}

PRIMARY_KEYS = {
    "smart_search_terms": "term_id",
    "smart_search_runs": "run_id",
    "smart_search_documents": "document_id",
    "smart_search_hits": "hit_id",
}

INTEGER_FIELDS = {
    "smart_search_terms": {"priority"},
    "smart_search_runs": {
        "request_category",
        "page_no",
        "num_of_rows",
        "total_count",
        "total_pages",
        "http_status",
    },
    "smart_search_documents": set(),
    "smart_search_hits": {"rank_no"},
}

FLOAT_FIELDS = {
    "smart_search_terms": set(),
    "smart_search_runs": {"elapsed_seconds"},
    "smart_search_documents": set(),
    "smart_search_hits": {"score"},
}

BOOLEAN_FIELDS = {
    "smart_search_terms": {"is_active"},
    "smart_search_runs": set(),
    "smart_search_documents": set(),
    "smart_search_hits": set(),
}


DEFAULT_TERM_GROUPS: list[tuple[str, list[str]]] = [
    (
        "equipment",
        [
            "컨베이어",
            "벨트 컨베이어",
            "체인 컨베이어",
            "롤러 컨베이어",
            "프레스",
            "사출성형기",
            "압출기",
            "산업용 로봇",
            "협동로봇",
            "공작기계",
            "크레인",
            "호이스트",
            "펌프",
            "압축기",
            "회전체",
            "유압장치",
            "공압장치",
            "배전반",
            "제어반",
            "자동화 설비",
        ],
    ),
    (
        "part",
        [
            "베어링",
            "벨트",
            "체인",
            "스프로킷",
            "풀리",
            "축",
            "커플링",
            "기어",
            "감속기",
            "모터",
            "실린더",
            "밸브",
            "유압호스",
            "공압호스",
            "센서",
            "근접센서",
            "광전센서",
            "인버터",
            "PLC",
            "인터록",
            "비상정지장치",
            "안전릴레이",
            "방호장치",
        ],
    ),
    (
        "task",
        [
            "설비 점검",
            "예방정비",
            "고장정비",
            "부품 교체",
            "분해 작업",
            "조립 작업",
            "설비 청소",
            "이물질 제거",
            "윤활 작업",
            "벨트 장력 조절",
            "축 정렬",
            "센서 교정",
            "배선 작업",
            "시운전",
            "설비 재가동",
            "금형 교체",
            "필터 교체",
            "호스 교체",
        ],
    ),
    (
        "hazard",
        [
            "끼임",
            "말림",
            "감김",
            "협착",
            "절단",
            "충돌",
            "낙하",
            "추락",
            "감전",
            "화재",
            "폭발",
            "화상",
            "질식",
            "누출",
            "압력 분출",
            "잔류압력",
            "잔류에너지",
            "갑작스러운 재가동",
            "중량물 낙하",
            "회전체 접촉",
        ],
    ),
    (
        "safety_measure",
        [
            "에너지 차단",
            "전원 차단",
            "잠금표지",
            "잠금장치",
            "록아웃 태그아웃",
            "LOTO",
            "잔류에너지 제거",
            "잔류압력 제거",
            "무전압 확인",
            "회전정지 확인",
            "방호장치 설치",
            "인터록 확인",
            "비상정지 확인",
            "접근 통제",
            "작업허가",
            "정비작업 안전조치",
            "재가동 방지",
            "안전관리자 확인",
        ],
    ),
    (
        "combined",
        [
            "컨베이어 베어링 교체",
            "컨베이어 벨트 교체",
            "컨베이어 체인 교체",
            "컨베이어 이물질 제거",
            "컨베이어 청소 작업",
            "컨베이어 끼임",
            "컨베이어 정비 안전",
            "컨베이어 전원 차단",
            "회전체 정비",
            "회전체 끼임",
            "회전체 청소",
            "베어링 교체 작업",
            "베어링 교체 안전",
            "모터 교체 작업",
            "감속기 교체 작업",
            "벨트 장력 조절",
            "프레스 금형 교체",
            "프레스 정비",
            "프레스 끼임",
            "산업용 로봇 정비",
            "로봇 재가동 방지",
            "센서 교체 작업",
            "근접센서 점검",
            "유압장치 정비",
            "유압호스 교체",
            "유압장치 잔류압력",
            "공압장치 정비",
            "공압실린더 교체",
            "공압장치 잔류압력",
            "배전반 점검",
            "제어반 점검",
            "전기설비 정비",
            "정비작업 감전",
            "정비작업 에너지 차단",
            "정비작업 전원 차단",
            "정비작업 잠금표지",
            "정비작업 재가동 방지",
            "인터록 우회",
            "인터록 해제",
            "방호장치 해제",
            "비상정지장치 점검",
            "설비 시운전 안전",
            "설비 재가동 안전",
            "중량물 부품 교체",
            "크레인 정비",
            "호이스트 점검",
        ],
    ),
]


QUOTA_MESSAGE_PATTERNS = (
    "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR",
    "요청 한도",
    "요청한도",
    "한도 초과",
    "트래픽 제한",
    "트래픽제한",
    "요청 제한",
    "요청제한",
    "service requests exceeds",
    "quota exceeded",
    "too many requests",
)

AUTH_MESSAGE_PATTERNS = (
    "SERVICE_KEY_IS_NOT_REGISTERED_ERROR",
    "DEADLINE_HAS_EXPIRED_ERROR",
    "UNREGISTERED_IP_ERROR",
    "등록되지 않은 서비스키",
    "등록되지 않은 인증키",
    "인증키가 등록되지",
    "인증키 오류",
    "유효하지 않은 인증키",
    "이용기간 만료",
    "등록되지 않은 IP",
    "서비스 접근 거부",
    "접근 거부",
    "access denied",
    "invalid service key",
)

TRANSIENT_MESSAGE_PATTERNS = (
    "UNKNOWN_ERROR",
    "INTERNAL_SERVER_ERROR",
    "SERVICE UNAVAILABLE",
    "일시적",
    "서버 오류",
    "서버장애",
    "temporarily unavailable",
)


class CollectorError(Exception):
    """수집기 기본 예외."""


class MaxRequestsReached(CollectorError):
    """사용자가 지정한 실제 API 요청 수에 도달."""


class ApiOutcomeError(CollectorError):
    """API 응답 분류 결과를 담는 예외."""

    def __init__(
        self,
        message: str,
        *,
        kind: str = "error",
        retryable: bool = False,
        result_code: str = "",
        result_msg: str = "",
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable
        self.result_code = result_code
        self.result_msg = result_msg
        self.http_status = http_status


@dataclass
class ParsedPage:
    result_code: str
    result_msg: str
    http_status: int
    requested_page_no: int
    response_page_no: int | None
    num_of_rows: int
    total_count: int
    total_pages: int
    associated_words_json: str
    category_count_json: str
    items: list[dict[str, Any]]
    total_media: list[dict[str, Any]]


@dataclass
class RequestBudget:
    max_requests: int | None
    actual_requests: int = 0

    def consume(self) -> None:
        if self.max_requests is not None and self.actual_requests >= self.max_requests:
            raise MaxRequestsReached
        self.actual_requests += 1


@dataclass
class CollectorState:
    terms: dict[str, dict[str, Any]] = field(default_factory=dict)
    runs: dict[str, dict[str, Any]] = field(default_factory=dict)
    documents: dict[str, dict[str, Any]] = field(default_factory=dict)
    hits: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class TermProgress:
    complete: bool
    total_count: int | None
    total_pages: int | None
    success_pages: set[int]
    page_size: int | None


@dataclass
class PageCounters:
    new_documents: int = 0
    updated_documents: int = 0
    duplicate_documents: int = 0
    new_hits: int = 0


@dataclass
class SessionCounters:
    processed_term_ids: set[str] = field(default_factory=set)
    new_documents: int = 0
    updated_documents: int = 0
    duplicate_documents: int = 0
    new_hits: int = 0


class TextExtractor(HTMLParser):
    """script/style을 제외하고 HTML 텍스트만 안전하게 추출한다."""

    BLOCK_TAGS = {
        "br",
        "p",
        "div",
        "li",
        "tr",
        "td",
        "th",
        "section",
        "article",
        "header",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered in {"script", "style"}:
            self.skip_depth += 1
        elif not self.skip_depth and lowered in self.BLOCK_TAGS:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in {"script", "style"} and self.skip_depth:
            self.skip_depth -= 1
        elif not self.skip_depth and lowered in self.BLOCK_TAGS:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self.skip_depth:
            self.parts.append(data)

    def text(self) -> str:
        return "".join(self.parts)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def format_duration(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def normalize_term(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\t", " ").replace("\r", " ").replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text.lower()


def deterministic_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join("" if part is None else str(part) for part in parts)
    value = uuid.uuid5(ID_NAMESPACE, f"{prefix}\x1e{material}").hex
    return f"{prefix}_{value}"


def raw_string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, dict, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=isinstance(value, dict))
    return str(value)


def decode_literal_unicode_angles(text: str) -> str:
    replacements = {
        r"\u003c": "<",
        r"\u003C": "<",
        r"\u003e": ">",
        r"\u003E": ">",
        r"\u0026": "&",
        r"\u0022": '"',
        r"\u0027": "'",
    }
    result = text
    for old, new in replacements.items():
        result = result.replace(old, new)
    return result


def clean_html_text(value: Any) -> str:
    text = raw_string(value)
    if not text:
        return ""
    text = decode_literal_unicode_angles(text)
    for _ in range(3):
        unescaped = html.unescape(text)
        if unescaped == text:
            break
        text = unescaped
    parser = TextExtractor()
    try:
        parser.feed(text)
        parser.close()
        text = parser.text()
    except Exception:
        text = re.sub(r"<[^>]*>", " ", text)
    text = html.unescape(text)
    text = text.replace("\u00a0", " ").replace("\u3000", " ").replace("\ufeff", " ")
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def clean_keyword(value: Any) -> str:
    text = clean_html_text(value)
    if not text:
        return ""
    parts = re.split(r"\s*,\s*", text)
    seen: set[str] = set()
    cleaned: list[str] = []
    for part in parts:
        token = re.sub(r"\s+", " ", part).strip()
        key = token.lower()
        if token and key not in seen:
            seen.add(key)
            cleaned.append(token)
    return ", ".join(cleaned)


def normalize_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, dict):
        return [value]
    return []


def normalize_image_path(value: Any) -> str:
    if value is None or value == "":
        items: list[Any] = []
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            items = []
        else:
            try:
                parsed = json.loads(stripped)
            except (json.JSONDecodeError, TypeError):
                parsed = stripped
            if isinstance(parsed, list):
                items = parsed
            else:
                items = [parsed]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        items = [value]

    normalized: list[Any] = []
    seen: set[str] = set()
    for item in items:
        if item is None:
            continue
        if isinstance(item, (dict, list)):
            normalized_item: Any = item
            key = json.dumps(item, ensure_ascii=False, sort_keys=True)
        else:
            normalized_item = str(item).strip()
            if not normalized_item:
                continue
            key = normalized_item
        if key not in seen:
            seen.add(key)
            normalized.append(normalized_item)
    return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))


def normalize_med_thumb(value: Any) -> str:
    text = clean_html_text(value).upper()
    if text in {"Y", "YES", "TRUE", "1"}:
        return "Y"
    if text in {"N", "NO", "FALSE", "0"}:
        return "N"
    return ""


def safe_int(value: Any, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return int(value)
    try:
        return int(float(str(value).strip().replace(",", "")))
    except (TypeError, ValueError):
        return default


def safe_float(value: Any, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    try:
        number = float(str(value).strip().replace(",", ""))
    except (TypeError, ValueError):
        return default
    if math.isnan(number) or math.isinf(number):
        return default
    return number


def safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return default
    return str(value).strip().lower() in {"1", "true", "y", "yes", "on"}


def normalize_category(value: Any) -> str:
    number = safe_int(value)
    return str(number) if number is not None else "0"


def category_name(category_code: str) -> str:
    return CATEGORY_NAMES.get(category_code, f"기타({category_code})")


def source_type(category_code: str) -> str:
    return SOURCE_TYPES.get(category_code, "unknown")


def stable_json(value: Any, empty_default: Any) -> str:
    if value is None or value == "":
        value = empty_default
    return json.dumps(value, ensure_ascii=False, sort_keys=isinstance(value, dict), separators=(",", ":"))


def calculate_content_hash(
    category_code: str,
    source_doc_id: str,
    title: str,
    content_clean: str,
    filepath: str,
) -> str:
    material = "\x1f".join(
        [category_code, source_doc_id, title, content_clean, filepath]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def normalized_key_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def get_field(item: Mapping[str, Any], *aliases: str) -> Any:
    for alias in aliases:
        if alias in item:
            return item[alias]
    normalized = {normalized_key_name(str(key)): value for key, value in item.items()}
    for alias in aliases:
        key = normalized_key_name(alias)
        if key in normalized:
            return normalized[key]
    return None


def default_term_specs() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    priority = 1
    for term_type, terms in DEFAULT_TERM_GROUPS:
        for term in terms:
            rows.append(
                {
                    "term": term,
                    "term_type": term_type,
                    "priority": priority,
                    "term_source": "default",
                }
            )
            priority += 1
    return rows


def load_terms_file(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"추가 검색어 파일을 찾을 수 없습니다: {path}")

    suffix = path.suffix.lower()
    result: list[dict[str, Any]] = []
    if suffix == ".txt":
        with path.open("r", encoding="utf-8-sig") as file:
            for line in file:
                term = line.strip()
                if term:
                    result.append(
                        {
                            "term": term,
                            "term_type": "combined",
                            "priority": None,
                            "term_source": "user_file",
                        }
                    )
        return result

    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            if not reader.fieldnames:
                raise ValueError("CSV 검색어 파일에 헤더가 없습니다.")
            field_map = {name.strip().lower(): name for name in reader.fieldnames}
            if "term" not in field_map:
                raise ValueError("CSV 검색어 파일에는 term 컬럼이 반드시 필요합니다.")
            for row in reader:
                term = (row.get(field_map["term"]) or "").strip()
                if not term:
                    continue
                type_field = field_map.get("term_type")
                priority_field = field_map.get("priority")
                raw_type = (row.get(type_field) or "combined").strip().lower() if type_field else "combined"
                term_type = raw_type if raw_type in VALID_TERM_TYPES else "combined"
                priority = safe_int(row.get(priority_field)) if priority_field else None
                result.append(
                    {
                        "term": term,
                        "term_type": term_type,
                        "priority": priority,
                        "term_source": "user_file",
                    }
                )
        return result

    raise ValueError("--terms-file은 .txt 또는 .csv 파일만 지원합니다.")


def merge_terms(
    existing: MutableMapping[str, dict[str, Any]],
    specs: Iterable[dict[str, Any]],
) -> int:
    by_normalized: dict[str, str] = {
        row["normalized_term"]: term_id for term_id, row in existing.items()
    }
    max_priority = max((safe_int(row.get("priority"), 0) or 0 for row in existing.values()), default=0)
    added = 0
    now = utc_now()

    for spec in specs:
        term = re.sub(r"\s+", " ", str(spec.get("term") or "")).strip()
        normalized = normalize_term(term)
        if not normalized or normalized in by_normalized:
            continue

        raw_type = str(spec.get("term_type") or "combined").strip().lower()
        term_type = raw_type if raw_type in VALID_TERM_TYPES else "combined"
        requested_priority = safe_int(spec.get("priority"))
        if requested_priority is None or requested_priority <= 0:
            max_priority += 1
            priority = max_priority
        else:
            priority = requested_priority
            max_priority = max(max_priority, priority)

        term_id = deterministic_id("term", normalized)
        row = {
            "term_id": term_id,
            "term": term,
            "normalized_term": normalized,
            "term_type": term_type,
            "priority": priority,
            "term_source": spec.get("term_source") or "user_file",
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        }
        existing[term_id] = row
        by_normalized[normalized] = term_id
        added += 1
    return added


def coerce_loaded_row(table: str, row: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for column in TABLE_COLUMNS[table]:
        value = row.get(column, "")
        if column in INTEGER_FIELDS[table]:
            result[column] = safe_int(value)
        elif column in FLOAT_FIELDS[table]:
            result[column] = safe_float(value)
        elif column in BOOLEAN_FIELDS[table]:
            result[column] = safe_bool(value)
        else:
            result[column] = "" if value is None else str(value)
    return result


def load_existing_csv(path: Path, table: str) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames:
            raise ValueError(f"기존 파일의 헤더를 읽을 수 없습니다: {path}")
        missing = [column for column in TABLE_COLUMNS[table] if column not in reader.fieldnames]
        if missing:
            raise ValueError(
                f"기존 {path.name}에 필수 컬럼이 없습니다: {', '.join(missing)}"
            )
        key_name = PRIMARY_KEYS[table]
        rows: dict[str, dict[str, Any]] = {}
        for line_no, raw_row in enumerate(reader, start=2):
            row = coerce_loaded_row(table, raw_row)
            key = str(row.get(key_name) or "")
            if not key:
                raise ValueError(f"{path.name} {line_no}행의 {key_name}이 비어 있습니다.")
            rows[key] = row
        return rows


def load_existing_jsonl(path: Path, table: str) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    key_name = PRIMARY_KEYS[table]
    rows: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as file:
        for line_no, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path.name} {line_no}행 JSON 오류: {exc}") from exc
            if not isinstance(raw, dict):
                raise ValueError(f"{path.name} {line_no}행이 JSON 객체가 아닙니다.")
            row = coerce_loaded_row(table, raw)
            key = str(row.get(key_name) or "")
            if not key:
                raise ValueError(f"{path.name} {line_no}행의 {key_name}이 비어 있습니다.")
            rows[key] = row
    return rows


def loaded_value_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return format(value, ".15g")
    return str(value)


def load_table(output_dir: Path, table: str) -> dict[str, dict[str, Any]]:
    csv_path = output_dir / f"{table}.csv"
    jsonl_path = output_dir / f"{table}.jsonl"
    if csv_path.exists():
        csv_rows = load_existing_csv(csv_path, table)
        if jsonl_path.exists():
            jsonl_rows = load_existing_jsonl(jsonl_path, table)
            if set(csv_rows) != set(jsonl_rows):
                raise ValueError(f"기존 {table} CSV와 JSONL의 기본키 집합이 다릅니다.")
            for key in csv_rows:
                for column in TABLE_COLUMNS[table]:
                    if loaded_value_text(csv_rows[key].get(column)) != loaded_value_text(jsonl_rows[key].get(column)):
                        raise ValueError(
                            f"기존 {table} CSV/JSONL 불일치: key={key}, column={column}"
                        )
        return csv_rows
    if jsonl_path.exists():
        print(f"[복구] {csv_path.name}이 없어 {jsonl_path.name}에서 읽습니다.", flush=True)
        return load_existing_jsonl(jsonl_path, table)
    return {}


def validate_loaded_state(state: CollectorState) -> None:
    normalized_to_id: dict[str, str] = {}
    for term_id, row in state.terms.items():
        normalized = row.get("normalized_term") or normalize_term(row.get("term"))
        if not normalized:
            raise ValueError(f"검색어 {term_id}의 normalized_term이 비어 있습니다.")
        other = normalized_to_id.get(str(normalized))
        if other and other != term_id:
            raise ValueError(f"중복 normalized_term이 존재합니다: {normalized}")
        normalized_to_id[str(normalized)] = term_id

    document_unique: dict[tuple[str, str], str] = {}
    for document_id, row in state.documents.items():
        key = (str(row.get("category_code") or ""), str(row.get("source_doc_id") or ""))
        other = document_unique.get(key)
        if other and other != document_id:
            raise ValueError(f"중복 문서 키가 존재합니다: {key}")
        document_unique[key] = document_id

    hit_unique: dict[tuple[str, str], str] = {}
    for hit_id, row in state.hits.items():
        key = (str(row.get("term_id") or ""), str(row.get("document_id") or ""))
        other = hit_unique.get(key)
        if other and other != hit_id:
            raise ValueError(f"중복 검색어-문서 연결이 존재합니다: {key}")
        hit_unique[key] = hit_id


def load_state(output_dir: Path) -> CollectorState:
    state = CollectorState(
        terms=load_table(output_dir, "smart_search_terms"),
        runs=load_table(output_dir, "smart_search_runs"),
        documents=load_table(output_dir, "smart_search_documents"),
        hits=load_table(output_dir, "smart_search_hits"),
    )
    validate_loaded_state(state)
    return state


def term_priority_map(state: CollectorState) -> dict[str, int]:
    return {
        term_id: safe_int(row.get("priority"), 10**9) or 10**9
        for term_id, row in state.terms.items()
    }


def sorted_table_rows(state: CollectorState, table: str) -> list[dict[str, Any]]:
    if table == "smart_search_terms":
        rows = list(state.terms.values())
        return sorted(
            rows,
            key=lambda row: (
                safe_int(row.get("priority"), 10**9) or 10**9,
                str(row.get("created_at") or ""),
                str(row.get("term_id") or ""),
            ),
        )
    priorities = term_priority_map(state)
    if table == "smart_search_runs":
        rows = list(state.runs.values())
        return sorted(
            rows,
            key=lambda row: (
                priorities.get(str(row.get("term_id") or ""), 10**9),
                safe_int(row.get("request_category"), 0) or 0,
                safe_int(row.get("page_no"), 0) or 0,
                str(row.get("run_id") or ""),
            ),
        )
    if table == "smart_search_documents":
        rows = list(state.documents.values())
        return sorted(
            rows,
            key=lambda row: (
                safe_int(row.get("category_code"), 10**9) or 10**9,
                str(row.get("source_doc_id") or ""),
                str(row.get("document_id") or ""),
            ),
        )
    rows = list(state.hits.values())
    return sorted(
        rows,
        key=lambda row: (
            priorities.get(str(row.get("term_id") or ""), 10**9),
            str(row.get("document_id") or ""),
            str(row.get("hit_id") or ""),
        ),
    )


def csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def create_temp_path(target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
        delete=False,
    )
    temp_path = Path(handle.name)
    handle.close()
    return temp_path


def write_csv_temp(path: Path, table: str, rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=TABLE_COLUMNS[table], extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: csv_value(row.get(column)) for column in TABLE_COLUMNS[table]})
        file.flush()
        os.fsync(file.fileno())


def write_jsonl_temp(path: Path, table: str, rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for row in rows:
            payload = {column: row.get(column) for column in TABLE_COLUMNS[table]}
            file.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
            file.write("\n")
        file.flush()
        os.fsync(file.fileno())


def atomic_write_csv(target: Path, table: str, rows: Sequence[Mapping[str, Any]]) -> Path:
    temp = create_temp_path(target)
    try:
        write_csv_temp(temp, table, rows)
        os.replace(temp, target)
    except Exception:
        temp.unlink(missing_ok=True)
        raise
    return target


def atomic_write_jsonl(target: Path, table: str, rows: Sequence[Mapping[str, Any]]) -> Path:
    temp = create_temp_path(target)
    try:
        write_jsonl_temp(temp, table, rows)
        os.replace(temp, target)
    except Exception:
        temp.unlink(missing_ok=True)
        raise
    return target


def save_all_outputs(state: CollectorState, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    pending: list[tuple[Path, Path]] = []
    try:
        for table in TABLE_COLUMNS:
            rows = sorted_table_rows(state, table)
            csv_target = output_dir / f"{table}.csv"
            jsonl_target = output_dir / f"{table}.jsonl"
            csv_temp = create_temp_path(csv_target)
            jsonl_temp = create_temp_path(jsonl_target)
            write_csv_temp(csv_temp, table, rows)
            write_jsonl_temp(jsonl_temp, table, rows)
            pending.append((csv_temp, csv_target))
            pending.append((jsonl_temp, jsonl_target))

        for temp, target in pending:
            os.replace(temp, target)
    finally:
        for temp, _ in pending:
            temp.unlink(missing_ok=True)


def canonical_compare_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return format(value, ".15g")
    return str(value)


def validate_csv_jsonl_pair(output_dir: Path, table: str) -> tuple[int, int]:
    csv_path = output_dir / f"{table}.csv"
    jsonl_path = output_dir / f"{table}.jsonl"
    csv_rows = load_existing_csv(csv_path, table)
    jsonl_rows = load_existing_jsonl(jsonl_path, table)
    if set(csv_rows) != set(jsonl_rows):
        raise ValueError(f"{table} CSV와 JSONL의 기본키 집합이 다릅니다.")
    for key in csv_rows:
        for column in TABLE_COLUMNS[table]:
            left = canonical_compare_value(csv_rows[key].get(column))
            right = canonical_compare_value(jsonl_rows[key].get(column))
            if left != right:
                raise ValueError(
                    f"{table} CSV/JSONL 불일치: key={key}, column={column}"
                )
    return len(csv_rows), len(jsonl_rows)


def build_request_url(
    service_key: str,
    *,
    page_no: int,
    num_of_rows: int,
    search_value: str,
    category: int = REQUEST_CATEGORY,
) -> str:
    key = service_key.strip()
    if not key:
        raise ValueError("인증키가 비어 있습니다.")
    encoded_key = key if re.search(r"%[0-9A-Fa-f]{2}", key) else quote(key, safe="")
    query = urlencode(
        {
            "pageNo": page_no,
            "numOfRows": num_of_rows,
            "searchValue": search_value,
            "category": category,
        },
        doseq=False,
    )
    return f"{API_URL}?serviceKey={encoded_key}&{query}"


def parse_xml_error(text: str) -> dict[str, str]:
    result = {"errMsg": "", "returnAuthMsg": "", "returnReasonCode": ""}
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return result
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag in result and element.text:
            result[tag] = element.text.strip()
    return result


def contains_pattern(message: str, patterns: Sequence[str]) -> bool:
    lowered = message.lower()
    return any(pattern.lower() in lowered for pattern in patterns)


def classify_api_error(
    *,
    result_code: str,
    result_msg: str,
    http_status: int | None,
    extra_message: str = "",
) -> ApiOutcomeError:
    combined = " | ".join(part for part in [result_code, result_msg, extra_message] if part)
    if http_status == 429 or result_code == "22" or contains_pattern(combined, QUOTA_MESSAGE_PATTERNS):
        return ApiOutcomeError(
            combined or "API 요청 한도 초과",
            kind="quota_exceeded",
            retryable=False,
            result_code=result_code,
            result_msg=result_msg,
            http_status=http_status,
        )
    if contains_pattern(combined, AUTH_MESSAGE_PATTERNS) or http_status == 403:
        return ApiOutcomeError(
            combined or "인증 또는 접근 권한 오류",
            kind="authentication_error",
            retryable=False,
            result_code=result_code,
            result_msg=result_msg,
            http_status=http_status,
        )
    if http_status == 404:
        return ApiOutcomeError(
            combined or "API 주소를 찾을 수 없습니다.",
            kind="fatal_error",
            retryable=False,
            result_code=result_code,
            result_msg=result_msg,
            http_status=http_status,
        )
    retryable = (
        http_status in {500, 502, 503, 504}
        or contains_pattern(combined, TRANSIENT_MESSAGE_PATTERNS)
    )
    return ApiOutcomeError(
        combined or "API 오류",
        kind="error",
        retryable=retryable,
        result_code=result_code,
        result_msg=result_msg,
        http_status=http_status,
    )


def parse_api_response(response: Response, requested_page_no: int, requested_num_rows: int) -> ParsedPage:
    status = response.status_code
    text = response.text or ""

    if status != 200:
        xml = parse_xml_error(text)
        extra = " | ".join(value for value in xml.values() if value)
        raise classify_api_error(
            result_code=xml.get("returnReasonCode", ""),
            result_msg=xml.get("returnAuthMsg", "") or xml.get("errMsg", ""),
            http_status=status,
            extra_message=extra or text[:500],
        )

    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError) as exc:
        xml = parse_xml_error(text)
        if any(xml.values()):
            raise classify_api_error(
                result_code=xml.get("returnReasonCode", ""),
                result_msg=xml.get("returnAuthMsg", "") or xml.get("errMsg", ""),
                http_status=status,
                extra_message=" | ".join(value for value in xml.values() if value),
            ) from exc
        if re.search(r"<\s*html\b", text, re.IGNORECASE):
            raise ApiOutcomeError(
                "JSON 대신 HTML 오류 페이지가 반환되었습니다.",
                kind="error",
                retryable=True,
                http_status=status,
            ) from exc
        raise ApiOutcomeError(
            "API 응답 JSON 파싱에 실패했습니다.",
            kind="error",
            retryable=True,
            http_status=status,
        ) from exc

    if not isinstance(payload, dict):
        raise ApiOutcomeError("API 응답 최상위 구조가 객체가 아닙니다.", retryable=True, http_status=status)

    response_root = payload.get("response", payload)
    if not isinstance(response_root, dict):
        raise ApiOutcomeError("response 구조가 객체가 아닙니다.", retryable=True, http_status=status)

    header = response_root.get("header") or payload.get("header") or {}
    if not isinstance(header, dict):
        header = {}
    result_code = str(header.get("resultCode") or header.get("result_code") or "")
    result_msg = str(header.get("resultMsg") or header.get("result_msg") or "")

    if result_code and result_code not in {"00", "0"}:
        raise classify_api_error(
            result_code=result_code,
            result_msg=result_msg,
            http_status=status,
        )

    body = response_root.get("body") or payload.get("body")
    if not isinstance(body, dict):
        raise ApiOutcomeError(
            "API 응답에 body 객체가 없습니다.",
            kind="error",
            retryable=True,
            result_code=result_code,
            result_msg=result_msg,
            http_status=status,
        )

    items_container = body.get("items")
    if isinstance(items_container, dict):
        items = normalize_list(items_container.get("item"))
    else:
        items = []
    items = [item for item in items if isinstance(item, dict)]

    total_media = [item for item in normalize_list(body.get("total_media")) if isinstance(item, dict)]

    total_count = safe_int(body.get("totalCount"))
    if total_count is None:
        raise ApiOutcomeError(
            "API 응답의 totalCount를 확인할 수 없습니다.",
            kind="error",
            retryable=True,
            result_code=result_code,
            result_msg=result_msg,
            http_status=status,
        )

    response_num_rows = safe_int(body.get("numOfRows"), requested_num_rows) or requested_num_rows
    effective_num_rows = response_num_rows if response_num_rows > 0 else requested_num_rows
    total_pages = max(1, math.ceil(total_count / effective_num_rows)) if total_count else 1
    response_page_no = safe_int(body.get("pageNo"))

    associated = body.get("associated_word", body.get("associatedWord", []))
    category_count = body.get("categorycount", body.get("categoryCount", {}))

    return ParsedPage(
        result_code=result_code or "00",
        result_msg=result_msg or "NORMAL_SERVICE",
        http_status=status,
        requested_page_no=requested_page_no,
        response_page_no=response_page_no,
        num_of_rows=effective_num_rows,
        total_count=total_count,
        total_pages=total_pages,
        associated_words_json=stable_json(associated, []),
        category_count_json=stable_json(category_count, {}),
        items=items,
        total_media=total_media,
    )


def request_page(
    session: Session,
    budget: RequestBudget,
    *,
    service_key: str,
    page_no: int,
    num_of_rows: int,
    search_value: str,
    timeout: float,
) -> ParsedPage:
    url = build_request_url(
        service_key,
        page_no=page_no,
        num_of_rows=num_of_rows,
        search_value=search_value,
        category=REQUEST_CATEGORY,
    )
    last_error: ApiOutcomeError | None = None

    for attempt in range(MAX_RETRIES + 1):
        budget.consume()
        try:
            response = session.get(url, timeout=timeout)
            return parse_api_response(response, page_no, num_of_rows)
        except MaxRequestsReached:
            raise
        except (Timeout, RequestsConnectionError) as exc:
            error = ApiOutcomeError(
                f"네트워크 오류: {exc}",
                kind="error",
                retryable=True,
            )
        except RequestException as exc:
            error = ApiOutcomeError(
                f"HTTP 요청 오류: {exc}",
                kind="error",
                retryable=False,
                http_status=getattr(getattr(exc, "response", None), "status_code", None),
            )
        except ApiOutcomeError as exc:
            error = exc

        last_error = error
        if not error.retryable or attempt >= MAX_RETRIES:
            raise error
        delay = RETRY_DELAYS[attempt]
        print(
            f"[재시도 {attempt + 1}/{MAX_RETRIES}] {error} 오류, {delay}초 후 다시 요청합니다.",
            flush=True,
        )
        time.sleep(delay)

    raise last_error or ApiOutcomeError("알 수 없는 요청 오류")


def build_document_from_item(item: Mapping[str, Any], now: str) -> dict[str, Any]:
    category_code = normalize_category(get_field(item, "category", "category_code"))
    title = clean_html_text(get_field(item, "title", "subject", "name"))
    content_value = get_field(item, "content", "contents", "matched_content", "snippet")
    content_raw = raw_string(content_value)
    content_clean = clean_html_text(content_value)
    filepath = clean_html_text(get_field(item, "filepath", "file_path", "url", "link"))
    keyword_value = get_field(item, "keyword", "keywords")
    keyword_raw = raw_string(keyword_value)
    keyword_clean = clean_keyword(keyword_value)
    source_doc_id = clean_html_text(get_field(item, "doc_id", "docId", "document_id", "id"))

    if not source_doc_id:
        fallback_material = "\x1f".join([category_code, title, filepath, content_clean])
        fallback = hashlib.sha256(fallback_material.encode("utf-8")).hexdigest()
        source_doc_id = f"missing_{fallback}"

    document_id = deterministic_id("doc", category_code, source_doc_id)
    return {
        "document_id": document_id,
        "source_doc_id": source_doc_id,
        "category_code": category_code,
        "category_name": category_name(category_code),
        "source_type": source_type(category_code),
        "title": title,
        "content_raw": content_raw,
        "content_clean": content_clean,
        "keyword_raw": keyword_raw,
        "keyword_clean": keyword_clean,
        "filepath": filepath,
        "image_path_json": normalize_image_path(
            get_field(item, "image_path", "imagePath", "images", "image")
        ),
        "med_thumb_yn": normalize_med_thumb(get_field(item, "med_thumb_yn", "medThumbYn")),
        "media_style": clean_html_text(get_field(item, "media_style", "mediaStyle")),
        "content_hash": calculate_content_hash(
            category_code,
            source_doc_id,
            title,
            content_clean,
            filepath,
        ),
        "first_seen_at": now,
        "last_seen_at": now,
        "updated_at": now,
    }


def upsert_document(
    state: CollectorState,
    item: Mapping[str, Any],
    now: str,
) -> tuple[str, str]:
    incoming = build_document_from_item(item, now)
    document_id = incoming["document_id"]
    existing = state.documents.get(document_id)
    if existing is None:
        state.documents[document_id] = incoming
        return document_id, "new"

    merged = dict(existing)
    mutable_fields = [
        "category_name",
        "source_type",
        "title",
        "content_raw",
        "content_clean",
        "keyword_raw",
        "keyword_clean",
        "filepath",
        "image_path_json",
        "med_thumb_yn",
        "media_style",
    ]
    changed = False
    for field_name in mutable_fields:
        new_value = incoming.get(field_name)
        old_value = existing.get(field_name)
        # API가 일부 선택 필드를 누락한 경우 기존의 유효한 값을 보존한다.
        selected = new_value if new_value not in (None, "", "[]") else old_value
        if selected != old_value:
            changed = True
        merged[field_name] = selected

    merged["last_seen_at"] = now
    merged["content_hash"] = calculate_content_hash(
        str(merged.get("category_code") or "0"),
        str(merged.get("source_doc_id") or ""),
        str(merged.get("title") or ""),
        str(merged.get("content_clean") or ""),
        str(merged.get("filepath") or ""),
    )
    if merged["content_hash"] != existing.get("content_hash"):
        changed = True
    merged["updated_at"] = now if changed else existing.get("updated_at") or now
    merged["first_seen_at"] = existing.get("first_seen_at") or now
    state.documents[document_id] = merged
    return document_id, "updated" if changed else "duplicate"


def better_score(new: float | None, old: float | None) -> float | None:
    if old is None:
        return new
    if new is None:
        return old
    return max(new, old)


def better_rank(new: int | None, old: int | None) -> int | None:
    if old is None:
        return new
    if new is None:
        return old
    return min(new, old)


def upsert_hit(
    state: CollectorState,
    *,
    run_id: str,
    term_id: str,
    document_id: str,
    result_group: str,
    rank_no: int,
    item: Mapping[str, Any],
    now: str,
) -> bool:
    hit_id = deterministic_id("hit", term_id, document_id)
    matched_value = get_field(item, "content", "contents", "matched_content", "snippet")
    highlight_value = get_field(
        item,
        "highlight_content",
        "highlightContent",
        "highlight",
        "highlighted_content",
    )
    incoming = {
        "hit_id": hit_id,
        "run_id": run_id,
        "term_id": term_id,
        "document_id": document_id,
        "result_group": result_group,
        "rank_no": rank_no,
        "score": safe_float(get_field(item, "score", "similarity", "api_score")),
        "matched_content_raw": raw_string(matched_value),
        "matched_content_clean": clean_html_text(matched_value),
        "highlight_content_raw": raw_string(highlight_value),
        "highlight_content_clean": clean_html_text(highlight_value),
        "collected_at": now,
        "updated_at": now,
    }
    existing = state.hits.get(hit_id)
    if existing is None:
        state.hits[hit_id] = incoming
        return True

    merged = dict(existing)
    old_score = safe_float(existing.get("score"))
    new_score = safe_float(incoming.get("score"))
    old_rank = safe_int(existing.get("rank_no"))
    new_rank = safe_int(incoming.get("rank_no"))
    selected_score = better_score(new_score, old_score)
    selected_rank = better_rank(new_rank, old_rank)

    changed = selected_score != old_score or selected_rank != old_rank
    merged["score"] = selected_score
    merged["rank_no"] = selected_rank

    if len(incoming["matched_content_clean"]) > len(str(existing.get("matched_content_clean") or "")):
        merged["matched_content_raw"] = incoming["matched_content_raw"]
        merged["matched_content_clean"] = incoming["matched_content_clean"]
        changed = True

    old_highlight = str(existing.get("highlight_content_clean") or "")
    new_highlight = incoming["highlight_content_clean"]
    if new_highlight and (not old_highlight or len(new_highlight) > len(old_highlight)):
        merged["highlight_content_raw"] = incoming["highlight_content_raw"]
        merged["highlight_content_clean"] = new_highlight
        changed = True

    if changed:
        merged["run_id"] = run_id
        merged["updated_at"] = now
    merged["collected_at"] = existing.get("collected_at") or now
    # result_group은 최초 발견값을 유지한다.
    state.hits[hit_id] = merged
    return False


def upsert_run(
    state: CollectorState,
    *,
    term_id: str,
    search_value: str,
    page_no: int,
    num_of_rows: int,
    total_count: int | None,
    total_pages: int | None,
    associated_words_json: str,
    category_count_json: str,
    result_code: str,
    result_msg: str,
    http_status: int | None,
    status: str,
    error_message: str,
    requested_at: str,
    completed_at: str,
    elapsed_seconds: float,
) -> str:
    if status not in VALID_RUN_STATUSES:
        raise ValueError(f"지원하지 않는 run status: {status}")
    run_id = deterministic_id("run", term_id, REQUEST_CATEGORY, page_no)
    state.runs[run_id] = {
        "run_id": run_id,
        "term_id": term_id,
        "search_value": search_value,
        "request_category": REQUEST_CATEGORY,
        "page_no": page_no,
        "num_of_rows": num_of_rows,
        "total_count": total_count,
        "total_pages": total_pages,
        "associated_words_json": associated_words_json,
        "category_count_json": category_count_json,
        "result_code": result_code,
        "result_msg": result_msg,
        "http_status": http_status,
        "status": status,
        "error_message": error_message[:2000],
        "requested_at": requested_at,
        "completed_at": completed_at,
        "elapsed_seconds": round(elapsed_seconds, 6),
    }
    return run_id


def successful_runs(state: CollectorState, term_id: str) -> list[dict[str, Any]]:
    return [
        row
        for row in state.runs.values()
        if row.get("term_id") == term_id
        and safe_int(row.get("request_category"), -1) == REQUEST_CATEGORY
        and row.get("status") == "success"
    ]


def calculate_term_progress(state: CollectorState, term_id: str) -> TermProgress:
    runs = successful_runs(state, term_id)
    if not runs:
        return TermProgress(False, None, None, set(), None)

    success_pages = {
        safe_int(row.get("page_no"), 0) or 0
        for row in runs
        if (safe_int(row.get("page_no"), 0) or 0) > 0
    }
    newest = max(runs, key=lambda row: str(row.get("completed_at") or row.get("requested_at") or ""))
    total_count = safe_int(newest.get("total_count"))
    total_pages = safe_int(newest.get("total_pages"))
    page_size = safe_int(newest.get("num_of_rows"))

    if total_count == 0:
        return TermProgress(1 in success_pages, 0, 1, success_pages, page_size)
    if total_pages is None and total_count is not None and page_size:
        total_pages = max(1, math.ceil(total_count / page_size))
    complete = bool(total_pages and all(page in success_pages for page in range(1, total_pages + 1)))
    return TermProgress(complete, total_count, total_pages, success_pages, page_size)


def next_missing_page(progress: TermProgress) -> int:
    if progress.total_pages is None:
        return 1
    for page in range(1, progress.total_pages + 1):
        if page not in progress.success_pages:
            return page
    return progress.total_pages + 1


def count_completed_terms(state: CollectorState) -> int:
    return sum(
        1
        for row in state.terms.values()
        if safe_bool(row.get("is_active"), True)
        and calculate_term_progress(state, str(row["term_id"])).complete
    )


def active_sorted_terms(state: CollectorState) -> list[dict[str, Any]]:
    return [
        row
        for row in sorted_table_rows(state, "smart_search_terms")
        if safe_bool(row.get("is_active"), True)
    ]


def print_start_summary(
    state: CollectorState,
    output_dir: Path,
    num_of_rows: int,
    max_requests: int | None,
) -> None:
    terms = active_sorted_terms(state)
    completed = count_completed_terms(state)
    success_runs = sum(1 for row in state.runs.values() if row.get("status") == "success")
    print("─" * 20, flush=True)
    print("수집 시작 정보", flush=True)
    print("─" * 20, flush=True)
    print(f"전체 등록 검색어: {len(terms):,}개", flush=True)
    print(f"완료 검색어: {completed:,}개", flush=True)
    print(f"이번 실행 대상 검색어: {len(terms) - completed:,}개", flush=True)
    print(f"기존 저장 문서: {len(state.documents):,}건", flush=True)
    print(f"기존 검색어-문서 연결: {len(state.hits):,}건", flush=True)
    print(f"기존 성공 페이지: {success_runs:,}개", flush=True)
    print(f"출력 폴더: {output_dir}", flush=True)
    print(f"페이지당 요청 건수: {num_of_rows:,}건", flush=True)
    maximum = "제한 없음" if max_requests is None else f"{max_requests:,}회"
    print(f"현재 실행 최대 요청: {maximum}", flush=True)
    print("─" * 20, flush=True)


def print_final_summary(
    *,
    state: CollectorState,
    output_dir: Path,
    budget: RequestBudget,
    counters: SessionCounters,
    exit_reason: str,
    started_at: float,
) -> None:
    all_terms = active_sorted_terms(state)
    completed = count_completed_terms(state)
    success_runs = sum(1 for row in state.runs.values() if row.get("status") == "success")
    print("", flush=True)
    print("─" * 20, flush=True)
    print("수집 결과", flush=True)
    print("─" * 20, flush=True)
    print(f"전체 검색어: {len(all_terms):,}개", flush=True)
    print(f"완료 검색어: {completed:,}개", flush=True)
    print(f"남은 검색어: {len(all_terms) - completed:,}개", flush=True)
    print(f"이번 실행 처리 검색어: {len(counters.processed_term_ids):,}개", flush=True)
    print(f"이번 실행 API 요청: {budget.actual_requests:,}회", flush=True)
    print(f"이번 실행 신규 문서: {counters.new_documents:,}건", flush=True)
    print(f"이번 실행 갱신 문서: {counters.updated_documents:,}건", flush=True)
    print(f"이번 실행 신규 연결: {counters.new_hits:,}건", flush=True)
    print(f"전체 저장 문서: {len(state.documents):,}건", flush=True)
    print(f"전체 검색어-문서 연결: {len(state.hits):,}건", flush=True)
    print(f"전체 성공 페이지: {success_runs:,}개", flush=True)
    print(f"종료 사유: {exit_reason}", flush=True)
    print(f"출력 폴더: {output_dir}", flush=True)
    print(f"소요 시간: {format_duration(time.monotonic() - started_at)}", flush=True)
    print("─" * 20, flush=True)


def process_page_results(
    state: CollectorState,
    *,
    parsed: ParsedPage,
    run_id: str,
    term_id: str,
    now: str,
) -> PageCounters:
    counters = PageCounters()
    groups = [("items", parsed.items), ("total_media", parsed.total_media)]
    for result_group, rows in groups:
        for rank_no, item in enumerate(rows, start=1):
            document_id, status = upsert_document(state, item, now)
            if status == "new":
                counters.new_documents += 1
            elif status == "updated":
                counters.updated_documents += 1
            else:
                counters.duplicate_documents += 1
            if upsert_hit(
                state,
                run_id=run_id,
                term_id=term_id,
                document_id=document_id,
                result_group=result_group,
                rank_no=rank_no,
                item=item,
                now=now,
            ):
                counters.new_hits += 1
    return counters


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="안전보건법령 스마트검색 API를 중단 후 이어받을 수 있게 수집합니다."
    )
    parser.add_argument("--terms-file", type=Path, help="추가 검색어 TXT 또는 CSV 경로")
    parser.add_argument("--output-dir", type=Path, default=Path("output"), help="출력 폴더 (기본: output)")
    parser.add_argument("--num-of-rows", type=int, default=100, help="페이지당 요청 건수 (기본: 100)")
    parser.add_argument("--max-requests", type=int, default=None, help="현재 실행의 최대 실제 API 요청 수")
    parser.add_argument("--sleep", type=float, default=0.1, help="정상 요청 사이 대기 초 (기본: 0.1)")
    parser.add_argument("--service-key", type=str, default=None, help="공공데이터포털 인증키")
    parser.add_argument("--timeout", type=float, default=30.0, help="요청 제한 시간 초 (기본: 30)")
    args = parser.parse_args(argv)
    if args.num_of_rows <= 0:
        parser.error("--num-of-rows는 1 이상이어야 합니다.")
    if args.max_requests is not None and args.max_requests < 0:
        parser.error("--max-requests는 0 이상이어야 합니다.")
    if args.sleep < 0:
        parser.error("--sleep은 0 이상이어야 합니다.")
    if args.timeout <= 0:
        parser.error("--timeout은 0보다 커야 합니다.")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    output_dir: Path = args.output_dir
    started_at = time.monotonic()
    budget = RequestBudget(args.max_requests)
    counters = SessionCounters()
    exit_reason = "all_completed"
    current_context: dict[str, Any] | None = None

    try:
        state = load_state(output_dir)
    except Exception as exc:
        print(f"[오류] 기존 output을 읽지 못했습니다: {exc}", flush=True)
        print("[중단] 기존 파일은 수정하지 않았습니다.", flush=True)
        return 1

    try:
        added_default = merge_terms(state.terms, default_term_specs())
        added_user = 0
        if args.terms_file:
            added_user = merge_terms(state.terms, load_terms_file(args.terms_file))
        if added_default or added_user:
            save_all_outputs(state, output_dir)
            print(
                f"[검색어 등록] 기본 {added_default:,}개 / 사용자 파일 {added_user:,}개를 추가했습니다.",
                flush=True,
            )

        service_key = (args.service_key or os.getenv("DATA_GO_KR_SERVICE_KEY") or "").strip()
        if not service_key:
            exit_reason = "authentication_error"
            save_all_outputs(state, output_dir)
            print("[오류] 공공데이터포털 인증키가 없습니다.", flush=True)
            print("Windows CMD: set DATA_GO_KR_SERVICE_KEY=발급받은_인증키", flush=True)
            print('또는: python collect_smart_search.py --service-key "발급받은_인증키"', flush=True)
            return 1

        print_start_summary(state, output_dir, args.num_of_rows, args.max_requests)
        all_terms = active_sorted_terms(state)
        session = requests.Session()
        session.headers.update(
            {
                "Accept": "application/json, application/xml;q=0.8, text/plain;q=0.5",
                "User-Agent": "SafeMaint-SmartSearch-Collector/1.0",
            }
        )

        for term_index, term_row in enumerate(all_terms, start=1):
            term_id = str(term_row["term_id"])
            term = str(term_row["term"])
            progress = calculate_term_progress(state, term_id)
            if progress.complete:
                print(f"[건너뜀] 이미 완료된 검색어: {term}", flush=True)
                continue

            page_size = progress.page_size or args.num_of_rows
            if progress.page_size and progress.page_size != args.num_of_rows:
                print(
                    f"[안내] {term}은 기존 성공 페이지와 동일하게 numOfRows={page_size}로 이어서 수집합니다.",
                    flush=True,
                )

            next_page = next_missing_page(progress)
            percent = (term_index / len(all_terms) * 100) if all_terms else 100.0
            print("", flush=True)
            print(f"[검색어 {term_index}/{len(all_terms)} | {percent:.1f}%] {term}", flush=True)
            print(f"[유형] {term_row['term_type']}", flush=True)
            print(
                f"[진행 상태] 기존 완료 페이지 {len(progress.success_pages)}개 / 다음 요청 page {next_page}",
                flush=True,
            )
            for completed_page in sorted(progress.success_pages):
                print(
                    f"[건너뜀] {term} / page {completed_page}은 이미 수집 완료",
                    flush=True,
                )

            term_started = time.monotonic()
            term_requests_before = budget.actual_requests
            term_new_docs = 0
            term_updated_docs = 0
            term_duplicate_docs = 0
            term_new_hits = 0
            term_failed = False

            while True:
                progress = calculate_term_progress(state, term_id)
                if progress.complete:
                    break
                page_no = next_missing_page(progress)
                if progress.total_pages is not None and page_no > progress.total_pages:
                    break

                counters.processed_term_ids.add(term_id)
                current_context = {
                    "term_id": term_id,
                    "term": term,
                    "page_no": page_no,
                    "num_of_rows": page_size,
                    "requested_at": utc_now(),
                    "started": time.monotonic(),
                }
                print(
                    f'[요청] 검색어="{term}" | page={page_no} | category={REQUEST_CATEGORY}',
                    flush=True,
                )

                try:
                    parsed = request_page(
                        session,
                        budget,
                        service_key=service_key,
                        page_no=page_no,
                        num_of_rows=page_size,
                        search_value=term,
                        timeout=args.timeout,
                    )
                except MaxRequestsReached:
                    exit_reason = "max_requests_reached"
                    print(
                        f"[요청 제한 도달] 이번 실행의 최대 요청 수 {args.max_requests:,}회에 도달했습니다.",
                        flush=True,
                    )
                    print("[저장 중] 현재까지 수집한 데이터를 저장합니다.", flush=True)
                    save_all_outputs(state, output_dir)
                    print("[저장 완료] 다음 실행 시 이어서 수집합니다.", flush=True)
                    raise
                except ApiOutcomeError as exc:
                    completed_at = utc_now()
                    elapsed = time.monotonic() - current_context["started"]
                    run_status = "quota_exceeded" if exc.kind == "quota_exceeded" else "error"
                    upsert_run(
                        state,
                        term_id=term_id,
                        search_value=term,
                        page_no=page_no,
                        num_of_rows=page_size,
                        total_count=None,
                        total_pages=None,
                        associated_words_json="[]",
                        category_count_json="{}",
                        result_code=exc.result_code,
                        result_msg=exc.result_msg,
                        http_status=exc.http_status,
                        status=run_status,
                        error_message=str(exc),
                        requested_at=current_context["requested_at"],
                        completed_at=completed_at,
                        elapsed_seconds=elapsed,
                    )
                    save_all_outputs(state, output_dir)
                    if exc.kind == "quota_exceeded":
                        exit_reason = "quota_exceeded"
                        print("[한도 초과] API 요청 한도에 도달했습니다.", flush=True)
                        print("[저장 중] 현재까지 수집한 데이터를 저장합니다.", flush=True)
                        print("[저장 완료] 다음 실행 시 미완료 페이지부터 이어서 수집합니다.", flush=True)
                        raise
                    if exc.kind == "authentication_error":
                        exit_reason = "authentication_error"
                        print("[오류] 인증키가 등록되지 않았거나 유효하지 않습니다.", flush=True)
                        print("[중단] 완료된 데이터는 보존되었습니다.", flush=True)
                        raise
                    if exc.kind == "fatal_error":
                        exit_reason = "fatal_error"
                        print(f"[오류] {exc}", flush=True)
                        print("[중단] 완료된 데이터는 보존되었습니다.", flush=True)
                        raise
                    print(f"[페이지 오류] {term} / page {page_no}: {exc}", flush=True)
                    term_failed = True
                    break

                completed_at = utc_now()
                elapsed = time.monotonic() - current_context["started"]
                run_id = upsert_run(
                    state,
                    term_id=term_id,
                    search_value=term,
                    page_no=page_no,
                    num_of_rows=parsed.num_of_rows,
                    total_count=parsed.total_count,
                    total_pages=parsed.total_pages,
                    associated_words_json=parsed.associated_words_json,
                    category_count_json=parsed.category_count_json,
                    result_code=parsed.result_code,
                    result_msg=parsed.result_msg,
                    http_status=parsed.http_status,
                    status="success",
                    error_message="",
                    requested_at=current_context["requested_at"],
                    completed_at=completed_at,
                    elapsed_seconds=elapsed,
                )
                page_counters = process_page_results(
                    state,
                    parsed=parsed,
                    run_id=run_id,
                    term_id=term_id,
                    now=completed_at,
                )
                save_all_outputs(state, output_dir)

                term_new_docs += page_counters.new_documents
                term_updated_docs += page_counters.updated_documents
                term_duplicate_docs += page_counters.duplicate_documents
                term_new_hits += page_counters.new_hits
                counters.new_documents += page_counters.new_documents
                counters.updated_documents += page_counters.updated_documents
                counters.duplicate_documents += page_counters.duplicate_documents
                counters.new_hits += page_counters.new_hits

                print(
                    f"[페이지 {page_no}/{parsed.total_pages} | totalCount {parsed.total_count:,}] 요청 완료",
                    flush=True,
                )
                print(
                    f"[페이지 결과] items {len(parsed.items):,}건 / total_media {len(parsed.total_media):,}건",
                    flush=True,
                )
                print(
                    "[페이지 저장] "
                    f"신규 문서 {page_counters.new_documents:,}건 / "
                    f"갱신 문서 {page_counters.updated_documents:,}건 / "
                    f"중복 문서 {page_counters.duplicate_documents:,}건 / "
                    f"신규 연결 {page_counters.new_hits:,}건",
                    flush=True,
                )
                print(
                    f"[누적] 이번 실행 요청 {budget.actual_requests:,}회 / "
                    f"전체 문서 {len(state.documents):,}건 / 전체 연결 {len(state.hits):,}건",
                    flush=True,
                )
                current_context = None
                if args.sleep:
                    time.sleep(args.sleep)

            if term_failed:
                continue

            final_progress = calculate_term_progress(state, term_id)
            if final_progress.complete:
                requests_used = budget.actual_requests - term_requests_before
                if final_progress.total_count == 0:
                    print(f'[검색어 완료] "{term}" 검색 결과 0건', flush=True)
                else:
                    print(f"[검색어 완료 {term_index}/{len(all_terms)}] {term}", flush=True)
                    print(f"- API 요청: {requests_used:,}회", flush=True)
                    print(f"- 전체 검색 결과: {(final_progress.total_count or 0):,}건", flush=True)
                    print(f"- 신규 문서: {term_new_docs:,}건", flush=True)
                    print(f"- 갱신 문서: {term_updated_docs:,}건", flush=True)
                    print(f"- 중복 문서: {term_duplicate_docs:,}건", flush=True)
                    print(f"- 신규 연결: {term_new_hits:,}건", flush=True)
                    print(f"- 소요 시간: {time.monotonic() - term_started:.1f}초", flush=True)

        save_all_outputs(state, output_dir)
        for table in TABLE_COLUMNS:
            validate_csv_jsonl_pair(output_dir, table)
        remaining_incomplete = [
            row
            for row in active_sorted_terms(state)
            if not calculate_term_progress(state, str(row["term_id"])).complete
        ]
        if remaining_incomplete:
            exit_reason = "fatal_error"
            print(
                f"[중단] 오류로 완료되지 않은 검색어가 {len(remaining_incomplete):,}개 남았습니다.",
                flush=True,
            )
        else:
            exit_reason = "all_completed"

    except MaxRequestsReached:
        pass
    except KeyboardInterrupt:
        exit_reason = "interrupted"
        print("\n[중단] Ctrl+C가 입력되었습니다.", flush=True)
        if current_context:
            elapsed = time.monotonic() - current_context["started"]
            upsert_run(
                state,
                term_id=current_context["term_id"],
                search_value=current_context["term"],
                page_no=current_context["page_no"],
                num_of_rows=current_context["num_of_rows"],
                total_count=None,
                total_pages=None,
                associated_words_json="[]",
                category_count_json="{}",
                result_code="",
                result_msg="",
                http_status=None,
                status="interrupted",
                error_message="Ctrl+C",
                requested_at=current_context["requested_at"],
                completed_at=utc_now(),
                elapsed_seconds=elapsed,
            )
        print("[저장 중] 현재까지 수집한 데이터를 저장합니다.", flush=True)
        save_all_outputs(state, output_dir)
        print("[저장 완료] 다음 실행 시 미완료 지점부터 이어서 수집합니다.", flush=True)
    except ApiOutcomeError:
        # 세부 메시지와 저장은 처리 지점에서 이미 수행했다.
        pass
    except Exception as exc:
        exit_reason = "fatal_error"
        print(f"[오류] 예상하지 못한 오류가 발생했습니다: {exc}", flush=True)
        try:
            if current_context:
                elapsed = time.monotonic() - current_context["started"]
                upsert_run(
                    state,
                    term_id=current_context["term_id"],
                    search_value=current_context["term"],
                    page_no=current_context["page_no"],
                    num_of_rows=current_context["num_of_rows"],
                    total_count=None,
                    total_pages=None,
                    associated_words_json="[]",
                    category_count_json="{}",
                    result_code="",
                    result_msg="",
                    http_status=None,
                    status="error",
                    error_message=str(exc),
                    requested_at=current_context["requested_at"],
                    completed_at=utc_now(),
                    elapsed_seconds=elapsed,
                )
            save_all_outputs(state, output_dir)
            print("[저장 완료] 완료된 데이터는 보존되었습니다.", flush=True)
        except Exception as save_exc:
            print(f"[저장 오류] 체크포인트 저장에도 실패했습니다: {save_exc}", flush=True)
    finally:
        try:
            save_all_outputs(state, output_dir)
        except Exception as exc:
            print(f"[저장 오류] 종료 직전 저장에 실패했습니다: {exc}", flush=True)
        print_final_summary(
            state=state,
            output_dir=output_dir,
            budget=budget,
            counters=counters,
            exit_reason=exit_reason,
            started_at=started_at,
        )

    return 0 if exit_reason in {
        "all_completed",
        "quota_exceeded",
        "max_requests_reached",
        "interrupted",
    } else 1


if __name__ == "__main__":
    raise SystemExit(main())
