from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


class APISchemaChangedError(RuntimeError):
    """공공데이터 API 컬럼 구조가 예상과 달라졌을 때 발생한다."""


def validate_item_schema(
    rows: Iterable[Mapping[str, Any]],
    *,
    source_name: str,
    required_columns: set[str],
) -> None:
    """모든 수집 행이 예상 컬럼을 갖는지 검사한다.

    데이터가 추가되는 것은 허용하지만 컬럼명이 바뀌거나 빠진 상태에서 조용히
    잘못된 파일을 만드는 것은 막는다.
    """
    for index, row in enumerate(rows, start=1):
        missing = sorted(required_columns - set(row))
        if missing:
            raise APISchemaChangedError(
                f"{source_name} API 응답 {index}번째 항목에서 필수 컬럼이 없습니다: "
                + ", ".join(missing)
            )
