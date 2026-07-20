from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import logging
from typing import Any
from urllib.parse import quote, urlencode

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

LOGGER = logging.getLogger(__name__)


class PublicDataAPIError(RuntimeError):
    pass


@dataclass(frozen=True)
class FetchStats:
    api_total_count: int
    pages_fetched: int
    raw_items_fetched: int
    complete: bool
    stopped_reason: str


def create_session() -> requests.Session:
    retry = Retry(
        total=5,
        connect=5,
        read=5,
        status=5,
        backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        raise_on_status=False,
    )
    session = requests.Session()
    session.mount("http://", HTTPAdapter(max_retries=retry))
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update({"Accept": "application/json", "User-Agent": "SafeMaintAI/1.0"})
    return session


def _build_url(
    base_url: str,
    service_key: str,
    service_key_type: str,
    params: Mapping[str, Any],
) -> str:
    encoded_key = service_key if service_key_type == "encoded" else quote(service_key, safe="")
    query = urlencode(
        {key: value for key, value in params.items() if value is not None and value != ""},
        doseq=True,
    )
    separator = "&" if "?" in base_url else "?"
    url = f"{base_url}{separator}serviceKey={encoded_key}"
    return f"{url}&{query}" if query else url


def _unwrap_response(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    root: Mapping[str, Any] = payload
    wrapped = payload.get("response")
    if isinstance(wrapped, Mapping):
        root = wrapped

    header = root.get("header")
    body = root.get("body")
    if not isinstance(header, Mapping) or not isinstance(body, Mapping):
        raise PublicDataAPIError("응답에 header 또는 body 객체가 없습니다.")
    return header, body


def _normalize_items(body: Mapping[str, Any]) -> list[dict[str, Any]]:
    items = body.get("items")
    if items in (None, ""):
        return []

    value: Any = items.get("item", []) if isinstance(items, Mapping) else items
    if isinstance(value, Mapping):
        return [dict(value)]
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, Mapping)]
    return []


def fetch_all_items(
    *,
    base_url: str,
    service_key: str,
    service_key_type: str,
    call_api_id: str,
    num_of_rows: int,
    timeout_seconds: int,
    max_pages: int = 0,
    extra_params: Mapping[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], FetchStats]:
    session = create_session()
    rows: list[dict[str, Any]] = []
    page_no = 1
    pages_fetched = 0
    total_count = 0
    stopped_reason = "unknown"

    try:
        while True:
            if max_pages and page_no > max_pages:
                stopped_reason = "max_pages_reached"
                break

            params: dict[str, Any] = {
                "pageNo": page_no,
                "numOfRows": num_of_rows,
                "callApiId": call_api_id,
            }
            if extra_params:
                params.update(extra_params)

            url = _build_url(base_url, service_key, service_key_type, params)
            response = session.get(url, timeout=timeout_seconds)
            response.raise_for_status()

            try:
                payload = response.json()
            except ValueError as exc:
                raise PublicDataAPIError(
                    f"JSON이 아닌 응답을 받았습니다: {response.text[:300]!r}"
                ) from exc

            if not isinstance(payload, Mapping):
                raise PublicDataAPIError("최상위 API 응답이 JSON 객체가 아닙니다.")

            header, body = _unwrap_response(payload)
            result_code = str(header.get("resultCode", "")).strip()
            if result_code and result_code != "00":
                raise PublicDataAPIError(
                    f"API 오류 {result_code}: {header.get('resultMsg', '')}"
                )

            items = _normalize_items(body)
            try:
                total_count = int(body.get("totalCount") or len(items))
            except (TypeError, ValueError):
                total_count = len(items)

            pages_fetched += 1
            rows.extend(items)
            LOGGER.info("API page=%s items=%s total=%s", page_no, len(items), total_count)

            if not items:
                stopped_reason = "empty_page"
                break
            if len(rows) >= total_count:
                stopped_reason = "total_count_reached"
                break
            if len(items) < num_of_rows:
                stopped_reason = "short_page"
                break
            page_no += 1
    finally:
        session.close()

    complete = total_count > 0 and len(rows) >= total_count
    return rows, FetchStats(
        api_total_count=total_count,
        pages_fetched=pages_fetched,
        raw_items_fetched=len(rows),
        complete=complete,
        stopped_reason=stopped_reason,
    )
