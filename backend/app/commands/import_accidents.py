from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.services.document_ingestion import (
    SUPPORTED_SOURCES,
    assert_isolated_test_database,
    import_accident_dataset,
    scan_accident_dataset,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Stream preprocessed accident JSONL into SafeMaint documents."
    )
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument(
        "--sources",
        nargs="+",
        choices=SUPPORTED_SOURCES,
        default=list(SUPPORTED_SOURCES),
    )
    parser.add_argument("--limit-per-source", type=int, default=100)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.limit_per_source > 100:
        raise SystemExit(
            "This experiment is limited to 100 documents per source. "
            "Full ingestion requires a separate approval."
        )

    scan = scan_accident_dataset(
        args.data_dir,
        sources=args.sources,
        limit_per_source=args.limit_per_source,
    )
    report: dict[str, object] = {"mode": "dry-run", "scan": scan.to_report()}
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    if scan.errors:
        return 2
    if args.dry_run:
        return 0

    from app.core.config import settings
    from app.db.session import SessionLocal

    database_name = assert_isolated_test_database(settings.database_url)
    result = import_accident_dataset(scan, SessionLocal, batch_size=args.batch_size)
    print(
        json.dumps(
            {
                "mode": "import",
                "database": database_name,
                "result": result.to_report(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
