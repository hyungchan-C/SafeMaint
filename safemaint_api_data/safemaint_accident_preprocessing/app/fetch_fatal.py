from __future__ import annotations

import logging
from typing import Any

from app.api_client import fetch_all_items
from app.config import Settings
from app.preprocess import preprocess_fatal_item
from app.schema_guard import validate_item_schema

LOGGER = logging.getLogger(__name__)


def run(settings: Settings) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw_rows, stats = fetch_all_items(
        base_url=settings.fatal_api_url,
        service_key=settings.fatal_service_key,
        service_key_type=settings.fatal_service_key_type,
        call_api_id=settings.fatal_call_api_id,
        num_of_rows=settings.api_page_size,
        timeout_seconds=settings.request_timeout_seconds,
        max_pages=settings.max_pages,
    )

    validate_item_schema(
        raw_rows,
        source_name="사고사망",
        required_columns={"keyword", "contents"},
    )

    unique: dict[tuple[str, str | None], dict[str, Any]] = {}
    excluded = 0
    for raw in raw_rows:
        try:
            row = preprocess_fatal_item(raw)
        except ValueError as exc:
            excluded += 1
            LOGGER.warning("사고사망 항목 제외: %s", exc)
            continue
        key = (str(row["title_raw"]), row["content_raw"])
        unique.setdefault(key, row)

    rows = sorted(
        unique.values(),
        key=lambda row: (str(row["title_raw"]), str(row["content_raw"] or "")),
    )
    for index, row in enumerate(rows, start=1):
        row["id"] = index

    result_stats = {
        "api_total_count": stats.api_total_count,
        "pages_fetched": stats.pages_fetched,
        "raw_items_fetched": stats.raw_items_fetched,
        "complete": stats.complete,
        "stopped_reason": stats.stopped_reason,
        "unique_rows_exported": len(rows),
        "duplicates_removed": stats.raw_items_fetched - excluded - len(rows),
        "excluded_rows": excluded,
        "filters": {},
    }
    LOGGER.info("사고사망 전처리 완료: %s건", len(rows))
    return rows, result_stats
