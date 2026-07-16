from __future__ import annotations

import argparse
import json
from os import getenv
from pathlib import Path

from sqlalchemy.engine import make_url

from app.commands.document_scope import (
    add_document_scope_arguments,
    parse_document_scope,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Embed pending document chunks in an explicitly selected scope."
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
    add_document_scope_arguments(
        parser,
        all_option="--all-pending",
        all_help="source_type 또는 문서 ID 제한 없이 모든 pending 청크를 처리합니다.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    scope = parse_document_scope(parser, args, all_attribute="all_pending")

    from app.core.config import settings
    from app.db.session import SessionLocal
    from app.services.document_embeddings import (
        SentenceTransformerEmbedder,
        embed_pending_chunks,
    )

    database_name = make_url(settings.database_url).database or ""
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
                "source_types": scope.source_types,
                "document_ids": (
                    [str(document_id) for document_id in scope.document_ids]
                    if scope.document_ids is not None
                    else None
                ),
                "all_pending": scope.unrestricted,
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
        source_types=scope.source_types,
        document_ids=scope.document_ids,
    )
    print(json.dumps(result.to_report(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
