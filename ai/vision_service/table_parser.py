from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any


@dataclass(slots=True)
class Cell:
    text: str
    rowspan: int = 1
    colspan: int = 1


class _TableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[Cell]]] = []
        self._table: list[list[Cell]] | None = None
        self._row: list[Cell] | None = None
        self._cell_parts: list[str] | None = None
        self._rowspan = 1
        self._colspan = 1

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell_parts = []
            self._rowspan = max(1, int((attributes.get("rowspan") or "1").strip('\\"')))
            self._colspan = max(1, int((attributes.get("colspan") or "1").strip('\\"')))

    def handle_data(self, data: str) -> None:
        if self._cell_parts is not None:
            self._cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell_parts is not None and self._row is not None:
            text = re.sub(r"\s+", " ", "".join(self._cell_parts)).strip()
            self._row.append(Cell(text, self._rowspan, self._colspan))
            self._cell_parts = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None


def _expand_table(rows: list[list[Cell]]) -> list[list[str]]:
    expanded: list[list[str]] = []
    active: dict[int, tuple[str, int]] = {}
    for raw_row in rows:
        values: dict[int, str] = {}
        for column, (value, remaining) in list(active.items()):
            values[column] = value
            if remaining <= 1:
                del active[column]
            else:
                active[column] = (value, remaining - 1)

        column = 0
        for cell in raw_row:
            while column in values:
                column += 1
            for offset in range(cell.colspan):
                target = column + offset
                values[target] = cell.text
                if cell.rowspan > 1:
                    active[target] = (cell.text, cell.rowspan - 1)
            column += cell.colspan

        width = max(values, default=-1) + 1
        expanded.append([values.get(index, "") for index in range(width)])
    return expanded


def _html_tables(payload: Any) -> list[str]:
    found: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key == "block_content" and isinstance(value, str) and "<table" in value:
                found.append(value.replace('\\"', '"'))
            else:
                found.extend(_html_tables(value))
    elif isinstance(payload, list):
        for value in payload:
            found.extend(_html_tables(value))
    return found


def extract_table_rows(extracted: str | None) -> list[dict[str, str]]:
    if not extracted:
        return []
    try:
        payload = json.loads(extracted)
        html_tables = _html_tables(payload)
    except json.JSONDecodeError:
        html_tables = re.findall(r"<table.*?</table>", extracted, flags=re.I | re.S)

    structured: list[dict[str, str]] = []
    for html in html_tables:
        parser = _TableHTMLParser()
        parser.feed(html)
        for table in parser.tables:
            grid = _expand_table(table)
            if len(grid) < 2:
                continue
            headers = [header or f"열_{index + 1}" for index, header in enumerate(grid[0])]
            for row in grid[1:]:
                record = {
                    headers[index]: value
                    for index, value in enumerate(row[: len(headers)])
                    if value
                }
                if record:
                    structured.append(record)
    return structured[:200]
