from __future__ import annotations

from datetime import date
import re
from typing import Any

from app.postprocess import (
    ALLOWED_POSTPROCESS_COLUMNS,
    CONTROL_CHARS_RE,
    NON_ACCIDENT_TITLE_RE,
    clean_content_text_final,
    extract_labeled_location_raw,
    parse_location_relaxed,
    postprocess_domestic_row,
    postprocess_fatal_row,
)

ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HTML_RE = re.compile(r"<(?:img|br|p|div|script|style|span|table|tr|td)\b", re.I)
DOUBLE_SPACE_RE = re.compile(r"[^\S\r\n]{2,}")
STANDALONE_NOISE_RE = re.compile(r"(?:^|\n)\s*(?:\.|다\.)\s*(?:\n|$)")

DOMESTIC_PROTECTED_COLUMNS = {
    "id", "title", "content_raw", "boardno", "business",
    "detailed_business", "causal_object", "accident_date_text",
}
FATAL_PROTECTED_COLUMNS = {
    "id", "title", "content_raw", "title_raw", "accident_date_text",
}


def _valid_iso(value: str | None) -> bool:
    if value is None:
        return True
    if not ISO_RE.fullmatch(value):
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


def _assert_unique_and_sequential(rows: list[dict[str, Any]], key: str, table: str) -> None:
    values = [row.get(key) for row in rows]
    if len(values) != len(set(values)):
        raise ValueError(f"{table}.{key} 중복값이 있습니다.")
    ids = [row.get("id") for row in rows]
    if ids != list(range(1, len(rows) + 1)):
        raise ValueError(f"{table}.id가 1부터 연속적이지 않습니다.")


def validate_final(
    *,
    base_domestic_rows: list[dict[str, Any]],
    base_fatal_rows: list[dict[str, Any]],
    domestic_rows: list[dict[str, Any]],
    fatal_rows: list[dict[str, Any]],
    source_stats: dict[str, dict[str, Any]],
    requested_sources: set[str],
    full_run: bool,
    base_validation: dict[str, Any],
    postprocess_meta: dict[str, Any],
) -> dict[str, Any]:
    if "domestic" in requested_sources:
        _assert_unique_and_sequential(domestic_rows, "boardno", "domestic_cases")
    if "fatal" in requested_sources:
        _assert_unique_and_sequential(fatal_rows, "id", "fatal_cases")

    quality: dict[str, int | bool] = {
        "base_validation_failed": 0 if base_validation.get("passed") else 1,
        "domestic_protected_column_regressions": 0,
        "fatal_protected_column_regressions": 0,
        "domestic_idempotence_errors": 0,
        "fatal_idempotence_errors": 0,
        "domestic_invalid_date_rows": 0,
        "fatal_invalid_date_rows": 0,
        "domestic_location_component_errors": 0,
        "fatal_location_component_errors": 0,
        "domestic_non_accident_location_rows": 0,
        "domestic_explicit_location_unresolved_rows": 0,
        "domestic_valid_explicit_location_missing_rows": 0,
        "domestic_html_or_control_rows": 0,
        "fatal_html_or_control_rows": 0,
        "domestic_spacing_residue_rows": 0,
        "fatal_spacing_residue_rows": 0,
        "domestic_null_literal_rows": 0,
        "fatal_null_literal_rows": 0,
        "postprocess_unresolved_rows": len(postprocess_meta.get("unresolved", [])),
    }

    if len(base_domestic_rows) != len(domestic_rows):
        quality["domestic_protected_column_regressions"] += abs(len(base_domestic_rows) - len(domestic_rows)) or 1
    if len(base_fatal_rows) != len(fatal_rows):
        quality["fatal_protected_column_regressions"] += abs(len(base_fatal_rows) - len(fatal_rows)) or 1

    for base, row in zip(base_domestic_rows, domestic_rows):
        if any(base.get(column) != row.get(column) for column in DOMESTIC_PROTECTED_COLUMNS):
            quality["domestic_protected_column_regressions"] += 1

        rerun, _meta = postprocess_domestic_row(row)
        if rerun != row:
            quality["domestic_idempotence_errors"] += 1

        if not _valid_iso(row.get("accident_date_text")):
            quality["domestic_invalid_date_rows"] += 1

        source = row.get("content_text") or ""
        parsed = parse_location_relaxed(row.get("location"), row.get("location") or "")
        expected = (
            parsed.parts.location,
            parsed.parts.sido,
            parsed.parts.sigungu,
            parsed.parts.detail,
        ) if parsed.parts.valid else (None, None, None, None)
        if _location_tuple(row) != expected:
            quality["domestic_location_component_errors"] += 1

        if NON_ACCIDENT_TITLE_RE.search(str(row.get("title") or "")) and row.get("location"):
            quality["domestic_non_accident_location_rows"] += 1

        labeled_raw = extract_labeled_location_raw(row.get("content_text"))
        labeled_decisions = [parse_location_relaxed(raw, raw) for raw in labeled_raw]
        if any(decision.status == "unresolved" for decision in labeled_decisions):
            quality["domestic_explicit_location_unresolved_rows"] += 1
        if (
            any(decision.parts.valid for decision in labeled_decisions)
            and not row.get("location")
            and _meta.get("location_status") not in {
                "route_ambiguous", "non_accident_document", "invalid_explicit_location"
            }
        ):
            quality["domestic_valid_explicit_location_missing_rows"] += 1

        content = row.get("content_text")
        if isinstance(content, str):
            if HTML_RE.search(content) or CONTROL_CHARS_RE.search(content) or "\r" in content or "\t" in content:
                quality["domestic_html_or_control_rows"] += 1
            if clean_content_text_final(content) != content or DOUBLE_SPACE_RE.search(content) or STANDALONE_NOISE_RE.search(content):
                quality["domestic_spacing_residue_rows"] += 1
            if content.strip().lower() in {"null", "none", "nan"}:
                quality["domestic_null_literal_rows"] += 1

    for base, row in zip(base_fatal_rows, fatal_rows):
        if any(base.get(column) != row.get(column) for column in FATAL_PROTECTED_COLUMNS):
            quality["fatal_protected_column_regressions"] += 1

        rerun, _meta = postprocess_fatal_row(row)
        if rerun != row:
            quality["fatal_idempotence_errors"] += 1

        if not _valid_iso(row.get("accident_date_text")):
            quality["fatal_invalid_date_rows"] += 1

        source = "\n".join(v for v in (row.get("title_raw"), row.get("content_text")) if v)
        parsed = parse_location_relaxed(row.get("location"), row.get("location") or "")
        expected = (
            parsed.parts.location,
            parsed.parts.sido,
            parsed.parts.sigungu,
            parsed.parts.detail,
        ) if parsed.parts.valid else (None, None, None, None)
        if _location_tuple(row) != expected:
            quality["fatal_location_component_errors"] += 1

        content = row.get("content_text")
        if isinstance(content, str):
            if HTML_RE.search(content) or CONTROL_CHARS_RE.search(content) or "\r" in content or "\t" in content:
                quality["fatal_html_or_control_rows"] += 1
            if clean_content_text_final(content) != content or DOUBLE_SPACE_RE.search(content) or STANDALONE_NOISE_RE.search(content):
                quality["fatal_spacing_residue_rows"] += 1
            if content.strip().lower() in {"null", "none", "nan"}:
                quality["fatal_null_literal_rows"] += 1

    quality["passed"] = all(
        value == 0
        for key, value in quality.items()
        if key != "passed"
    )

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
    preprocessing_ready = bool(full_run and sources_complete and rows_present and quality["passed"])

    return {
        "passed": bool(quality["passed"]),
        "requested_sources": sorted(requested_sources),
        "sources_complete": sources_complete,
        "rows_present": rows_present,
        "common_columns_aligned": True,
        "base_validation": base_validation,
        "postprocess": {
            "status_counts": postprocess_meta.get("status_counts", {}),
            "unresolved_rows": postprocess_meta.get("unresolved", []),
            "independent_postprocess_columns": sorted(ALLOWED_POSTPROCESS_COLUMNS),
        },
        "quality_validation": quality,
        "preprocessing_ready": preprocessing_ready,
        "downstream_rag_tables_generated": False,
    }
