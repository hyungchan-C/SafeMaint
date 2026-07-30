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


DEFAULT_QUERIES = (
    "컨베이어 벨트 끼임 사고",
    "크레인으로 코일 상차 중 낙하 사고",
    "작동유 드럼 파열 사고",
    "설비 청소 중 감전 사고",
    "회전체 점검 중 말림 사고",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run pgvector cosine search in an explicitly selected document scope."
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
    add_document_scope_arguments(
        parser,
        all_option="--all-documents",
        all_help="source_type 또는 문서 ID 제한 없이 모든 ready 청크를 검색합니다.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    scope = parse_document_scope(parser, args, all_attribute="all_documents")

    from app.core.config import settings
    from app.db.session import SessionLocal
    from app.services.document_embeddings import (
        SentenceTransformerEmbedder,
        search_similar_chunks,
    )

    database_name = make_url(settings.database_url).database or ""
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
            source_types=scope.source_types,
            document_ids=scope.document_ids,
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
                "source_types": scope.source_types,
                "document_ids": (
                    [str(document_id) for document_id in scope.document_ids]
                    if scope.document_ids is not None
                    else None
                ),
                "all_documents": scope.unrestricted,
                "queries": query_reports,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
