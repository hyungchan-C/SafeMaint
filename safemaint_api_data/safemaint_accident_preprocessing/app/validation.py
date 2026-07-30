from __future__ import annotations

from datetime import date
import re
from typing import Any

from app.preprocess import (
    extract_domestic_accident_date,
    extract_domestic_location,
    extract_full_date_from_body,
    find_known_glued_boundaries,
    location_detail_is_source_backed,
    location_detail_is_suspicious,
    location_has_affiliation_priority_error,
    normalize_title_date,
    parse_fatal_title,
    parse_location,
    resolve_domestic_location_verified,
    resolve_fatal_location_verified,
)

NULL_LITERALS = {"null", "none", "nan"}
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HTML_RESIDUE_RE = re.compile(r"<(?:img|br|p|div|script|style|span|table|tr|td)\b", re.I)
DOMESTIC_NON_ACCIDENT_TITLE_RE = re.compile(
    r"(?:세미나|간담회|교육|행사|공지|안내|자료|압축파일|사례모음|통계|목록|다운로드)",
    re.I,
)
FATAL_DOUBLE_SPACE_RE = re.compile(r"※[^\S\r\n]{2,}위")
FATAL_JOINED_GWANGJU_RE = re.compile(r"\[[^\]]*,\s*경기\s*광주(?:시)?\]")

CONTAMINATION_RE = re.compile(
    r"(?:피\s*해\s*정\s*도|공\s*정|재\s*해\s*유\s*형|사\s*고\s*유\s*형|"
    r"날\s*짜|공\s*사\s*금\s*액|재\s*해\s*개\s*요|재\s*해\s*원\s*인)\s*[:：]",
    re.I,
)


def _assert_unique(rows: list[dict[str, Any]], key: str, table: str) -> None:
    values = [row.get(key) for row in rows]
    if len(values) != len(set(values)):
        raise ValueError(f"{table}.{key}에 중복값이 있습니다.")


def _assert_sequential_ids(rows: list[dict[str, Any]], table: str) -> None:
    ids = [row.get("id") for row in rows]
    if ids != list(range(1, len(rows) + 1)):
        raise ValueError(f"{table}.id가 1부터 연속적이지 않습니다.")


def _is_valid_iso_date(value: str) -> bool:
    if not ISO_DATE_RE.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def _location_tuple(row: dict[str, Any]) -> tuple[Any, Any, Any, Any]:
    return (
        row.get("location"),
        row.get("location_sido"),
        row.get("location_sigungu"),
        row.get("location_detail"),
    )


def _parts_tuple(parts: Any) -> tuple[Any, Any, Any, Any]:
    if not parts.valid:
        return None, None, None, None
    return parts.location, parts.sido, parts.sigungu, parts.detail


def validate_all(
    *,
    domestic_rows: list[dict[str, Any]],
    fatal_rows: list[dict[str, Any]],
    source_stats: dict[str, dict[str, Any]],
    requested_sources: set[str],
    full_run: bool,
) -> dict[str, Any]:
    if "domestic" in requested_sources:
        _assert_unique(domestic_rows, "id", "domestic_cases")
        _assert_unique(domestic_rows, "boardno", "domestic_cases")
        _assert_sequential_ids(domestic_rows, "domestic_cases")
    if "fatal" in requested_sources:
        _assert_unique(fatal_rows, "id", "fatal_cases")
        _assert_sequential_ids(fatal_rows, "fatal_cases")

    quality: dict[str, int | bool] = {
        "domestic_missing_required_rows": 0,
        "domestic_null_literal_rows": 0,
        "domestic_html_residue_rows": 0,
        "domestic_metadata_contamination_rows": 0,
        "domestic_date_priority_errors": 0,
        "domestic_invalid_iso_date_rows": 0,
        "domestic_non_accident_date_rows": 0,
        "domestic_explicit_region_label_missing_rows": 0,
        "domestic_location_priority_errors": 0,
        "domestic_invalid_location_rows": 0,
        "domestic_location_component_errors": 0,
        "domestic_suspicious_location_detail_rows": 0,
        "domestic_location_affiliation_priority_errors": 0,
        "domestic_glued_boundary_rows": 0,
        "fatal_missing_required_rows": 0,
        "fatal_html_residue_rows": 0,
        "fatal_date_priority_errors": 0,
        "fatal_invalid_iso_date_rows": 0,
        "fatal_location_priority_errors": 0,
        "fatal_invalid_location_rows": 0,
        "fatal_location_component_errors": 0,
        "fatal_suspicious_location_detail_rows": 0,
        "fatal_location_affiliation_priority_errors": 0,
        "fatal_glued_boundary_rows": 0,
        "fatal_double_space_rows": 0,
        "fatal_joined_gyeonggi_gwangju_errors": 0,
    }

    domestic_enrichment = {
        "body_date_iso_rows": 0,
        "date_null_rows": 0,
        "explicit_location_rows": 0,
        "location_null_rows": 0,
    }

    for row in domestic_rows:
        if not str(row.get("boardno") or "").strip() or not str(row.get("title") or "").strip():
            quality["domestic_missing_required_rows"] += 1

        content = row.get("content_text")
        if isinstance(content, str) and content.strip().lower() in NULL_LITERALS:
            quality["domestic_null_literal_rows"] += 1
        if isinstance(content, str) and HTML_RESIDUE_RE.search(content):
            quality["domestic_html_residue_rows"] += 1
        if isinstance(content, str) and find_known_glued_boundaries(content):
            quality["domestic_glued_boundary_rows"] += 1

        contaminated = False
        for field in ("detailed_business", "causal_object"):
            value = str(row.get(field) or "")
            if CONTAMINATION_RE.search(value) or "\n" in value or len(value) > 120:
                contaminated = True
        if contaminated:
            quality["domestic_metadata_contamination_rows"] += 1

        expected_date = extract_domestic_accident_date(content, row.get("title"))
        actual_date = row.get("accident_date_text")
        if actual_date and DOMESTIC_NON_ACCIDENT_TITLE_RE.search(str(row.get("title") or "")):
            quality["domestic_non_accident_date_rows"] += 1
        if actual_date != expected_date:
            quality["domestic_date_priority_errors"] += 1
        if expected_date:
            domestic_enrichment["body_date_iso_rows"] += 1
            if not _is_valid_iso_date(str(actual_date or "")):
                quality["domestic_invalid_iso_date_rows"] += 1
        else:
            domestic_enrichment["date_null_rows"] += 1

        initial = extract_domestic_location(content)
        expected = resolve_domestic_location_verified(initial, content)
        expected_tuple = _parts_tuple(expected)
        if _location_tuple(row) != expected_tuple:
            quality["domestic_location_priority_errors"] += 1
        if isinstance(content, str) and re.search(r"(?:^|\s)\d*\.?\s*지\s*역\s*[:：]", content):
            labeled = extract_domestic_location(content)
            if labeled and not row.get("location"):
                quality["domestic_explicit_region_label_missing_rows"] += 1

        actual = parse_location(row.get("location"))
        if row.get("location") and not actual.valid:
            quality["domestic_invalid_location_rows"] += 1
        if _location_tuple(row)[1:] != expected_tuple[1:]:
            quality["domestic_location_component_errors"] += 1

        detail = row.get("location_detail")
        if location_detail_is_suspicious(detail) or not location_detail_is_source_backed(content, detail):
            quality["domestic_suspicious_location_detail_rows"] += 1
        if location_has_affiliation_priority_error(content, row.get("location")):
            quality["domestic_location_affiliation_priority_errors"] += 1

        if expected.valid:
            domestic_enrichment["explicit_location_rows"] += 1
        else:
            domestic_enrichment["location_null_rows"] += 1

    fatal_enrichment = {
        "body_date_iso_rows": 0,
        "title_date_iso_rows": 0,
        "date_null_rows": 0,
        "location_consistent_rows": 0,
        "title_location_rows": 0,
        "body_location_rows": 0,
        "title_typo_corrected_rows": 0,
        "title_province_only_rows": 0,
        "location_conflict_rows": 0,
        "location_null_rows": 0,
    }

    for row in fatal_rows:
        if not str(row.get("title_raw") or "").strip() or not str(row.get("title") or "").strip():
            quality["fatal_missing_required_rows"] += 1

        content = row.get("content_text")
        if isinstance(content, str) and HTML_RESIDUE_RE.search(content):
            quality["fatal_html_residue_rows"] += 1
        if isinstance(content, str) and find_known_glued_boundaries(content):
            quality["fatal_glued_boundary_rows"] += 1
        if isinstance(content, str) and FATAL_DOUBLE_SPACE_RE.search(content):
            quality["fatal_double_space_rows"] += 1
        if FATAL_JOINED_GWANGJU_RE.search(str(row.get("title_raw") or "")):
            actual_joined = parse_location(row.get("location"))
            if not (
                actual_joined.valid
                and actual_joined.sido == "경기도"
                and actual_joined.sigungu
                and actual_joined.sigungu.split()[0] == "광주시"
            ):
                quality["fatal_joined_gyeonggi_gwangju_errors"] += 1

        _, title_date, title_location, _ = parse_fatal_title(row.get("title_raw"))
        body_date = extract_full_date_from_body(content)
        title_date_iso = normalize_title_date(title_date, content)
        expected_date = body_date or title_date_iso
        actual_date = row.get("accident_date_text")
        if actual_date != expected_date:
            quality["fatal_date_priority_errors"] += 1
        if actual_date and not _is_valid_iso_date(str(actual_date)):
            quality["fatal_invalid_iso_date_rows"] += 1
        if body_date:
            fatal_enrichment["body_date_iso_rows"] += 1
        elif title_date_iso:
            fatal_enrichment["title_date_iso_rows"] += 1
        else:
            fatal_enrichment["date_null_rows"] += 1

        expected, decision = resolve_fatal_location_verified(title_location, content)
        expected_tuple = _parts_tuple(expected)
        if _location_tuple(row) != expected_tuple:
            quality["fatal_location_priority_errors"] += 1

        actual = parse_location(row.get("location"))
        if row.get("location") and not actual.valid:
            quality["fatal_invalid_location_rows"] += 1
        if _location_tuple(row)[1:] != expected_tuple[1:]:
            quality["fatal_location_component_errors"] += 1

        source_text = "\n".join(value for value in (row.get("title_raw"), content) if value)
        detail = row.get("location_detail")
        if location_detail_is_suspicious(detail) or not location_detail_is_source_backed(source_text, detail):
            quality["fatal_suspicious_location_detail_rows"] += 1
        if location_has_affiliation_priority_error(content, row.get("location")):
            quality["fatal_location_affiliation_priority_errors"] += 1

        decision_key = {
            "consistent": "location_consistent_rows",
            "title": "title_location_rows",
            "body": "body_location_rows",
            "body_corrected_title_typo": "title_typo_corrected_rows",
            "title_province_only": "title_province_only_rows",
            "conflict": "location_conflict_rows",
            "none": "location_null_rows",
        }.get(decision, "location_null_rows")
        fatal_enrichment[decision_key] += 1

    quality["passed"] = all(value == 0 for key, value in quality.items() if key != "passed")

    sources_complete = all(
        bool(source_stats[source].get("complete"))
        for source in requested_sources
        if source in source_stats
    )
    rows_present = all(
        (source != "domestic" or bool(domestic_rows))
        and (source != "fatal" or bool(fatal_rows))
        for source in requested_sources
    )
    preprocessing_ready = full_run and sources_complete and rows_present and bool(quality["passed"])

    return {
        "passed": bool(quality["passed"]),
        "requested_sources": sorted(requested_sources),
        "sources_complete": sources_complete,
        "rows_present": rows_present,
        "common_columns_aligned": True,
        "domestic_enrichment": domestic_enrichment,
        "fatal_enrichment": fatal_enrichment,
        "quality_validation": quality,
        "preprocessing_ready": preprocessing_ready,
        "downstream_rag_tables_generated": False,
    }
