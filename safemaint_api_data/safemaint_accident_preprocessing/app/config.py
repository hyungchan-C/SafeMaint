from __future__ import annotations

from dataclasses import dataclass, replace
import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"환경변수 {name}는 정수여야 합니다: {raw!r}") from exc


def _key_type(name: str) -> str:
    value = os.getenv(name, "decoded").strip().lower()
    if value not in {"decoded", "encoded"}:
        raise ValueError(f"{name}은 decoded 또는 encoded여야 합니다.")
    return value


def _optional_key(name: str, placeholder: str) -> str:
    value = os.getenv(name, "").strip()
    if value == placeholder:
        return ""
    return value


@dataclass(frozen=True)
class Settings:
    domestic_service_key: str
    domestic_service_key_type: str
    fatal_service_key: str
    fatal_service_key_type: str
    domestic_api_url: str
    domestic_call_api_id: str
    fatal_api_url: str
    fatal_call_api_id: str
    domestic_business: str
    domestic_keyword: str
    api_page_size: int
    request_timeout_seconds: int
    max_pages: int
    output_dir: Path

    @classmethod
    def from_env(cls) -> "Settings":
        page_size = _int_env("API_PAGE_SIZE", 100)
        timeout = _int_env("REQUEST_TIMEOUT_SECONDS", 60)
        max_pages = _int_env("MAX_PAGES", 0)

        if page_size <= 0:
            raise ValueError("API_PAGE_SIZE는 1 이상이어야 합니다.")
        if timeout <= 0:
            raise ValueError("REQUEST_TIMEOUT_SECONDS는 1 이상이어야 합니다.")
        if max_pages < 0:
            raise ValueError("MAX_PAGES는 0 이상이어야 합니다.")

        output_dir = Path(os.getenv("OUTPUT_DIR", "output").strip() or "output")
        if not output_dir.is_absolute():
            output_dir = PROJECT_ROOT / output_dir

        return cls(
            domestic_service_key=_optional_key(
                "DOMESTIC_SERVICE_KEY", "여기에_국내재해사례_인증키_입력"
            ),
            domestic_service_key_type=_key_type("DOMESTIC_SERVICE_KEY_TYPE"),
            fatal_service_key=_optional_key(
                "FATAL_SERVICE_KEY", "여기에_사고사망_인증키_입력"
            ),
            fatal_service_key_type=_key_type("FATAL_SERVICE_KEY_TYPE"),
            domestic_api_url=os.getenv(
                "DOMESTIC_API_URL",
                "http://apis.data.go.kr/B552468/disaster_api02/getdisaster_api02",
            ).strip(),
            domestic_call_api_id=os.getenv("DOMESTIC_CALL_API_ID", "1060").strip(),
            fatal_api_url=os.getenv(
                "FATAL_API_URL",
                "http://apis.data.go.kr/B552468/news_api02/getNews_api02",
            ).strip(),
            fatal_call_api_id=os.getenv("FATAL_CALL_API_ID", "1040").strip(),
            domestic_business=os.getenv("DOMESTIC_BUSINESS", "").strip(),
            domestic_keyword=os.getenv("DOMESTIC_KEYWORD", "").strip(),
            api_page_size=page_size,
            request_timeout_seconds=timeout,
            max_pages=max_pages,
            output_dir=output_dir,
        )

    def with_overrides(self, *, max_pages: int | None = None) -> "Settings":
        if max_pages is None:
            return self
        if max_pages < 0:
            raise ValueError("--max-pages는 0 이상이어야 합니다.")
        return replace(self, max_pages=max_pages)

    def validate_for_sources(self, sources: set[str]) -> None:
        if "domestic" in sources and not self.domestic_service_key:
            raise ValueError(".env의 DOMESTIC_SERVICE_KEY에 실제 인증키를 입력하세요.")
        if "fatal" in sources and not self.fatal_service_key:
            raise ValueError(".env의 FATAL_SERVICE_KEY에 실제 인증키를 입력하세요.")
