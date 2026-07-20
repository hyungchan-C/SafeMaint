from __future__ import annotations

import argparse
import logging
import sys

from app.config import Settings
from app.pipeline import run


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "SafeMaint 국내재해사례·사고사망 API 전체 수집 및 "
            "domestic_cases/fatal_cases 전처리 파일 생성"
        )
    )
    parser.add_argument(
        "--only",
        choices=("all", "domestic", "fatal"),
        default="all",
        help="처리할 데이터. 기본값은 all",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="시험 실행용 최대 페이지 수. 0 또는 생략하면 전체 페이지 수집",
    )
    return parser.parse_args()


def main() -> int:
    configure_logging()
    args = parse_args()
    logger = logging.getLogger(__name__)

    try:
        settings = Settings.from_env().with_overrides(max_pages=args.max_pages)
        result = run(settings, only=args.only)
        manifest = result["manifest"]
        logger.info(
            "기본 테이블 전처리 완료 판정: %s",
            "완료" if manifest.get("preprocessing_ready") else "미완료(manifest.json 확인)",
        )
    except Exception as exc:
        logger.error("실행 실패: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
