from __future__ import annotations

import argparse
import json
from os import getenv
from pathlib import Path

from app.services.document_ingestion import assert_isolated_test_database


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Embed pending incident chunks in an isolated test database."
    )
    parser.add_argument(
        "--model",
        default=getenv("EMBEDDING_MODEL", "BAAI/bge-m3"),
    )
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("ai/.model-cache"),
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    from app.core.config import settings
    from app.db.session import SessionLocal
    from app.services.document_embeddings import (
        SentenceTransformerEmbedder,
        embed_pending_chunks,
    )

    database_name = assert_isolated_test_database(settings.database_url)
    embedder = SentenceTransformerEmbedder(
        model_name=args.model,
        device=args.device,
        cache_dir=args.cache_dir,
    )
    print(
        json.dumps(
            {
                "database": database_name,
                "model": embedder.model_name,
                "device": embedder.device,
                "cache_dir": str(args.cache_dir.resolve()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    result = embed_pending_chunks(
        SessionLocal,
        embedder,
        limit=args.limit,
        batch_size=args.batch_size,
    )
    print(json.dumps(result.to_report(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
