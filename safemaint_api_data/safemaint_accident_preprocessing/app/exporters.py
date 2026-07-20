from __future__ import annotations

import csv
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

# content_raw에 큰 HTML 문자열이 포함될 수 있으므로 기본 CSV 필드 제한을 확장한다.
csv.field_size_limit(100_000_000)

COMMON_COLUMNS = [
    "id",
    "title",
    "accident_date_text",
    "location",
    "location_sido",
    "location_sigungu",
    "location_detail",
    "content_text",
    "content_raw",
]

TABLE_COLUMNS: dict[str, list[str]] = {
    "domestic_cases": COMMON_COLUMNS
    + [
        "boardno",
        "business",
        "detailed_business",
        "causal_object",
    ],
    "fatal_cases": COMMON_COLUMNS
    + [
        "title_raw",
    ],
}
INTEGER_COLUMNS: dict[str, set[str]] = {
    "domestic_cases": {"id"},
    "fatal_cases": {"id"},
}


def _csv_value(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return "" if value is None else value


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: _csv_value(row.get(column)) for column in columns})


def write_jsonl(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            payload = {column: row.get(column) for column in columns}
            file.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def _convert_csv_row(row: dict[str, str], table_name: str) -> dict[str, Any]:
    converted: dict[str, Any] = {}
    for column in TABLE_COLUMNS[table_name]:
        value = row[column]
        if value == "":
            converted[column] = None
        elif column in INTEGER_COLUMNS[table_name]:
            converted[column] = int(value)
        else:
            converted[column] = value
    return converted


def validate_export_pairs(
    output_dir: Path,
    table_names: list[str],
) -> dict[str, Any]:
    """CSV와 JSONL을 메모리에 전부 올리지 않고 행 단위로 전수 비교한다."""
    tables: dict[str, dict[str, Any]] = {}
    total_mismatches = 0

    for table_name in table_names:
        csv_path = output_dir / f"{table_name}.csv"
        jsonl_path = output_dir / f"{table_name}.jsonl"
        csv_count = 0
        jsonl_count = 0
        mismatch_rows = 0

        with (
            csv_path.open("r", encoding="utf-8", newline="") as csv_file,
            jsonl_path.open("r", encoding="utf-8") as jsonl_file,
        ):
            reader = csv.DictReader(csv_file)
            if reader.fieldnames != TABLE_COLUMNS[table_name]:
                raise ValueError(
                    f"{csv_path.name} 컬럼 순서가 예상과 다릅니다: {reader.fieldnames}"
                )

            csv_iter = iter(reader)
            jsonl_iter = (line for line in jsonl_file if line.strip())
            while True:
                try:
                    csv_raw = next(csv_iter)
                    csv_done = False
                except StopIteration:
                    csv_raw = None
                    csv_done = True
                try:
                    jsonl_line = next(jsonl_iter)
                    jsonl_done = False
                except StopIteration:
                    jsonl_line = None
                    jsonl_done = True

                if csv_done and jsonl_done:
                    break
                if not csv_done:
                    csv_count += 1
                if not jsonl_done:
                    jsonl_count += 1
                if csv_done or jsonl_done:
                    mismatch_rows += 1
                    continue

                csv_row = _convert_csv_row(csv_raw, table_name)
                jsonl_row = json.loads(jsonl_line)
                expected = {
                    column: jsonl_row.get(column)
                    for column in TABLE_COLUMNS[table_name]
                }
                if csv_row != expected:
                    mismatch_rows += 1

        total_mismatches += mismatch_rows
        tables[table_name] = {
            "csv_rows": csv_count,
            "jsonl_rows": jsonl_count,
            "mismatch_rows": mismatch_rows,
            "passed": mismatch_rows == 0,
        }

    return {
        "tables": tables,
        "csv_jsonl_mismatches": total_mismatches,
        "passed": total_mismatches == 0,
    }


def export_all(
    output_dir: Path,
    *,
    domestic_rows: list[dict[str, Any]],
    fatal_rows: list[dict[str, Any]],
    source_stats: dict[str, dict[str, Any]],
    validation: dict[str, Any],
    requested_sources: set[str],
    max_pages: int,
) -> tuple[dict[str, Path], dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)

    all_tables = {
        "domestic_cases": domestic_rows,
        "fatal_cases": fatal_rows,
    }
    table_names = [
        table_name
        for table_name, source in (
            ("domestic_cases", "domestic"),
            ("fatal_cases", "fatal"),
        )
        if source in requested_sources
    ]

    paths: dict[str, Path] = {}
    for table_name in table_names:
        rows = all_tables[table_name]
        csv_path = output_dir / f"{table_name}.csv"
        jsonl_path = output_dir / f"{table_name}.jsonl"
        write_csv(csv_path, rows, TABLE_COLUMNS[table_name])
        write_jsonl(jsonl_path, rows, TABLE_COLUMNS[table_name])
        paths[f"{table_name}_csv"] = csv_path
        paths[f"{table_name}_jsonl"] = jsonl_path

    export_validation = validate_export_pairs(output_dir, table_names)
    final_validation = dict(validation)
    final_validation["export_validation"] = export_validation
    final_validation["passed"] = bool(validation.get("passed")) and export_validation["passed"]
    final_validation["preprocessing_ready"] = (
        bool(validation.get("preprocessing_ready")) and export_validation["passed"]
    )

    row_counts = {table_name: len(all_tables[table_name]) for table_name in table_names}
    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "run_mode": "full" if max_pages == 0 else "sample",
        "max_pages": max_pages,
        "sources": source_stats,
        "row_counts": row_counts,
        "preprocessing_rules": {
            "domestic_cases": (
                "기존 HTML/공백/null 및 업종·기인물 전처리 유지, contents 원본 보존, "
                "본문의 유효한 4자리 연도 날짜만 ISO 변환, 행정구역과 현장 근거가 명확한 "
                "위치만 추출, 사고지와 소속업체 주소를 구분하고 시설명 접두사 오인을 제거, "
                "안전한 붙어쓰기 경계만 복구, boardno 기준 중복 제거"
            ),
            "fatal_cases": (
                "본문의 연도 포함 실제 날짜 우선 ISO 변환, 제목 월일은 본문의 연도가 "
                "확인될 때만 ISO 보완, 제목·본문 위치를 행정구역 사전으로 검증, 제목 오타는 "
                "유효한 본문 위치로 보정, 사고지보다 소속업체 주소를 우선하지 않음, "
                "시설·업종명 일부를 상세지역으로 저장하지 않음, 실제 충돌은 NULL, "
                "안전한 붙어쓰기 경계만 복구"
            ),
        },
        "common_columns": COMMON_COLUMNS,
        "common_column_rules": {
            "accident_date_text": "YYYY-MM-DD 또는 null",
            "location": "검증·정규화된 전체 위치 또는 null",
            "location_sido": "정식 시도명 또는 null",
            "location_sigungu": "검증된 시군구 또는 null",
            "location_detail": "원문에 확인된 읍면동리 등 상세 위치 또는 null",
            "content_text": "HTML 제거, 공백 정리, 명확한 붙어쓰기 경계만 복구한 UTF-8 일반 텍스트",
            "content_raw": "API 원문 보존; RAG 임베딩 대상에서 제외",
        },
        "location_resolution_rules": {
            "same_hierarchy": "제목·본문이 같은 지역이면 더 구체적인 위치 사용",
            "invalid_title_valid_body": "제목 지역이 비유효하고 본문 지역이 유효하면 본문 사용",
            "title_typo": "제목 하위 지역 오타와 본문 유효 지역이 한 글자 차이면 본문 사용",
            "accident_site_priority": "소속업체 주소와 사고 발생 장소가 함께 있으면 사고 발생 장소 사용",
            "facility_prefix_rejection": "공동주택·자동차부품·수리조선소 등 시설·업종명 일부를 상세지역으로 저장하지 않음",
            "true_conflict": "제목·본문이 서로 다른 유효 지역이면 location 관련 컬럼을 null",
            "no_guess": "근거가 없으면 상위 지역이나 상세 지역을 추정하지 않음",
        },
        "validation": final_validation,
        "preprocessing_ready": bool(final_validation.get("preprocessing_ready")),
        "downstream": {
            "accident_documents_generated": False,
            "accident_chunks_generated": False,
            "note": "다른 데이터 전처리 완료 후 통합 단계에서 한 번만 생성",
        },
        "files": {key: path.name for key, path in paths.items()},
    }

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    paths["manifest"] = manifest_path
    return paths, manifest
