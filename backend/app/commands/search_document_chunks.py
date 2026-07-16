from __future__ import annotations

import argparse
import json
from os import getenv
from pathlib import Path

from app.services.document_ingestion import assert_isolated_test_database


DEFAULT_QUERIES = (
    "컨베이어 벨트 끼임 사고",
    "크레인으로 코일 상차 중 낙하 사고",
    "작동유 드럼 파열 사고",
    "설비 청소 중 감전 사고",
    "회전체 점검 중 말림 사고",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run pgvector cosine search against embedded incident chunks."
    )
    parser.add_argument(
        "--model",
        default=getenv("EMBEDDING_MODEL", "BAAI/bge-m3"),
    )
    parser.add_argument("--query", action="append")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--min-similarity", type=float, default=0.25)
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
        search_similar_chunks,
    )

    database_name = assert_isolated_test_database(settings.database_url)
    embedder = SentenceTransformerEmbedder(
        model_name=args.model,
        device=args.device,
        cache_dir=args.cache_dir,
    )
    query_reports = []
    for query in args.query or DEFAULT_QUERIES:
        dimension, hits = search_similar_chunks(
            SessionLocal,
            embedder,
            query=query,
            top_k=args.top_k,
            min_similarity=args.min_similarity,
        )
        query_reports.append(
            {
                "query": query,
                "dimension": dimension,
                "hits": [hit.to_report() for hit in hits],
            }
        )
    print(
        json.dumps(
            {
                "database": database_name,
                "model": embedder.model_name,
                "device": embedder.device,
                "top_k": args.top_k,
                "min_similarity": args.min_similarity,
                "queries": query_reports,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
