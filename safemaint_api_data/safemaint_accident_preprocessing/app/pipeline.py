from __future__ import annotations

import logging
from typing import Any

from app import fetch_domestic, fetch_fatal
from app.config import Settings
from app.exporters import export_all
from app.validation import validate_all as validate_base
from app.final_validation import validate_final
from app.postprocess import postprocess_rows

LOGGER = logging.getLogger(__name__)


def _skipped_stats() -> dict[str, Any]:
    return {
        "requested": False,
        "api_total_count": 0,
        "pages_fetched": 0,
        "raw_items_fetched": 0,
        "unique_rows_exported": 0,
        "duplicates_removed": 0,
        "excluded_rows": 0,
        "complete": True,
        "stopped_reason": "not_requested",
        "filters": {},
    }


def run(settings: Settings, *, only: str = "all") -> dict[str, object]:
    if only not in {"all", "domestic", "fatal"}:
        raise ValueError("only은 all, domestic, fatal 중 하나여야 합니다.")

    requested_sources = {"domestic", "fatal"} if only == "all" else {only}
    settings.validate_for_sources(requested_sources)

    if "domestic" in requested_sources:
        domestic_rows, domestic_stats = fetch_domestic.run(settings)
        domestic_stats["requested"] = True
    else:
        domestic_rows, domestic_stats = [], _skipped_stats()

    if "fatal" in requested_sources:
        fatal_rows, fatal_stats = fetch_fatal.run(settings)
        fatal_stats["requested"] = True
    else:
        fatal_rows, fatal_stats = [], _skipped_stats()

    source_stats = {"domestic": domestic_stats, "fatal": fatal_stats}

    if settings.max_pages == 0:
        incomplete = [
            source
            for source in requested_sources
            if not bool(source_stats[source].get("complete"))
        ]
        if incomplete:
            raise RuntimeError(
                "전체 페이지 수집이 완료되지 않았습니다: "
                + ", ".join(sorted(incomplete))
            )

    # 기존 정상 전처리 결과를 먼저 검증하고 그대로 보존한다.
    base_domestic_rows = [dict(row) for row in domestic_rows]
    base_fatal_rows = [dict(row) for row in fatal_rows]
    base_validation = validate_base(
        domestic_rows=base_domestic_rows,
        fatal_rows=base_fatal_rows,
        source_stats=source_stats,
        requested_sources=requested_sources,
        full_run=settings.max_pages == 0,
    )

    # 새 날짜·위치·본문 보완은 독립 후처리 단계에서 허용 컬럼만 변경한다.
    domestic_rows, fatal_rows, postprocess_meta = postprocess_rows(
        base_domestic_rows, base_fatal_rows
    )
    validation = validate_final(
        base_domestic_rows=base_domestic_rows,
        base_fatal_rows=base_fatal_rows,
        domestic_rows=domestic_rows,
        fatal_rows=fatal_rows,
        source_stats=source_stats,
        requested_sources=requested_sources,
        full_run=settings.max_pages == 0,
        base_validation=base_validation,
        postprocess_meta=postprocess_meta,
    )

    paths, manifest = export_all(
        settings.output_dir,
        domestic_rows=domestic_rows,
        fatal_rows=fatal_rows,
        source_stats=source_stats,
        validation=validation,
        requested_sources=requested_sources,
        max_pages=settings.max_pages,
    )

    LOGGER.info(
        "완료: domestic=%s fatal=%s output=%s",
        len(domestic_rows),
        len(fatal_rows),
        settings.output_dir,
    )
    LOGGER.info("accident_documents/accident_chunks는 현재 생성하지 않습니다.")

    return {
        "domestic_cases": domestic_rows,
        "fatal_cases": fatal_rows,
        "source_stats": source_stats,
        "validation": manifest["validation"],
        "manifest": manifest,
        "paths": paths,
    }
