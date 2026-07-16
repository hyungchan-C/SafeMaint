from __future__ import annotations

import json
from os import getenv
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.engine import make_url

from app.services.document_ingestion import (
    content_sha256,
    document_values,
    import_accident_dataset,
    normalize_chunk_content,
    scan_accident_dataset,
    stable_external_id,
)


INTEGRATION_ENABLED = (
    getenv("RUN_DB_INTEGRATION") == "1"
    and getenv("ALLOW_TEST_DB_MUTATION") == "1"
)


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _fixture_data(
    root: Path,
    domestic_document_id: int = 1,
    domestic_source_row_id: int = 11,
    include_fatal: bool = True,
) -> None:
    documents: list[dict[str, object]] = [
        {
            "document_id": domestic_document_id,
            "source_type": "domestic",
            "source_row_id": domestic_source_row_id,
            "title": "작동유 드럼 파열",
            "body_text": None,
            "search_text": "제목: 작동유 드럼 파열",
        }
    ]
    chunks: list[dict[str, object]] = [
        {
            "chunk_id": 101,
            "document_id": domestic_document_id,
            "chunk_order": 0,
            "chunk_text": "작동유 드럼 파열 사고",
            "embedding": None,
        }
    ]
    fatal_rows: list[dict[str, object]] = []
    if include_fatal:
        documents.append(
            {
                "document_id": domestic_document_id + 1,
                "source_type": "fatal",
                "source_row_id": domestic_source_row_id + 1,
                "title": "코일 낙하 사고",
                "body_text": "크레인으로 상차 중 코일이 낙하함",
                "search_text": "코일 낙하 사고",
            }
        )
        chunks.append(
            {
                "chunk_id": 102,
                "document_id": domestic_document_id + 1,
                "chunk_order": 0,
                "chunk_text": "크레인으로 코일 상차 중 낙하 사고",
                "embedding": None,
            }
        )
        fatal_rows.append(
            {
                "id": domestic_source_row_id + 1,
                "title_raw": "코일 낙하 사고",
                "accident_date_text": "2026.07.01",
                "location": "테스트 사업장",
                "title": "코일 낙하 사고",
                "content_raw": "원문",
                "content_text": "정제 본문",
            }
        )

    _write_jsonl(root / "accident_documents.jsonl", documents)
    _write_jsonl(root / "accident_chunks.jsonl", chunks)
    _write_jsonl(
        root / "domestic_cases.jsonl",
        [
            {
                "id": domestic_source_row_id,
                "boardno": "TEST-001",
                "business": "제조업",
                "title": "작동유 드럼 파열",
                "content_raw": "null",
                "content_text": None,
                "detailed_business": None,
                "causal_object": "드럼",
            }
        ],
    )
    _write_jsonl(root / "fatal_cases.jsonl", fatal_rows)


def test_scan_and_mapping_preserve_source_semantics(tmp_path: Path) -> None:
    _fixture_data(tmp_path)

    scan = scan_accident_dataset(tmp_path, limit_per_source=100)

    assert scan.read_documents == 2
    assert scan.read_chunks == 2
    assert scan.selected_source_counts == {"domestic": 1, "fatal": 1}
    assert scan.title_only_count == 1
    assert scan.empty_title_count == 0
    assert scan.empty_chunk_count == 0
    assert scan.errors == []

    domestic = scan.selected_documents[0]
    values = document_values(domestic, scan.raw_metadata[("domestic", 11)])
    assert values["external_id"] == "incident:domestic:11"
    assert values["source_type"] == "incident"
    assert values["access_level"] == "restricted"
    assert values["publisher"] is None
    assert values["metadata"]["content_quality"] == "title_only"
    assert values["metadata"]["causal_object"] == "드럼"
    assert "content_raw" not in values["metadata"]

    fatal = scan.selected_documents[1]
    fatal_values = document_values(fatal, scan.raw_metadata[("fatal", 12)])
    assert str(fatal_values["published_at"]) == "2026-07-01"
    assert fatal_values["metadata"]["accident_date_text"] == "2026.07.01"


def test_hash_and_external_id_are_stable() -> None:
    assert normalize_chunk_content("  A\r\nB\r  ") == "A\nB"
    assert content_sha256("A\r\nB") == content_sha256("  A\nB  ")
    assert stable_external_id("fatal", 7) == "incident:fatal:7"


def test_scan_reports_empty_duplicate_and_orphan_chunks(tmp_path: Path) -> None:
    _fixture_data(tmp_path, include_fatal=False)
    _write_jsonl(
        tmp_path / "accident_chunks.jsonl",
        [
            {
                "chunk_id": 1,
                "document_id": 1,
                "chunk_order": 0,
                "chunk_text": "   ",
                "embedding": None,
            },
            {
                "chunk_id": 2,
                "document_id": 1,
                "chunk_order": 0,
                "chunk_text": "duplicate",
                "embedding": None,
            },
            {
                "chunk_id": 3,
                "document_id": 999,
                "chunk_order": 0,
                "chunk_text": "orphan",
                "embedding": None,
            },
        ],
    )

    scan = scan_accident_dataset(
        tmp_path,
        sources=("domestic",),
        limit_per_source=100,
    )

    assert scan.empty_chunk_count == 1
    assert scan.duplicate_chunk_index_count == 1
    assert scan.orphan_chunk_count == 1
    assert len(scan.errors) == 3


@pytest.mark.skipif(
    not INTEGRATION_ENABLED,
    reason="Set RUN_DB_INTEGRATION=1 and ALLOW_TEST_DB_MUTATION=1",
)
def test_postgres_upsert_is_idempotent_and_invalidates_changed_embedding(
    tmp_path: Path,
) -> None:
    from app.core.config import settings
    from app.db.models import Document, DocumentChunk
    from app.db.session import SessionLocal

    database_name = make_url(settings.database_url).database or ""
    assert database_name.endswith("_test")

    suffix = int(uuid4().hex[:7], 16)
    document_id = 90_000_000 + suffix
    source_row_id = 80_000_000 + suffix
    external_id = f"incident:domestic:{source_row_id}"
    _fixture_data(
        tmp_path,
        domestic_document_id=document_id,
        domestic_source_row_id=source_row_id,
        include_fatal=False,
    )

    try:
        first_scan = scan_accident_dataset(
            tmp_path,
            sources=("domestic",),
            limit_per_source=100,
        )
        first = import_accident_dataset(first_scan, SessionLocal, batch_size=10)
        assert first.documents_inserted == 1
        assert first.chunks_inserted == 1

        with SessionLocal() as session:
            document = session.scalar(
                select(Document).where(Document.external_id == external_id)
            )
            assert document is not None
            first_updated_at = document.updated_at
            chunk = session.scalar(
                select(DocumentChunk).where(DocumentChunk.document_id == document.id)
            )
            assert chunk is not None
            chunk.embedding = [1.0, 0.0]
            chunk.embedding_model = "test/model"
            chunk.embedding_dimension = 2
            chunk.embedding_status = "ready"
            session.commit()

        second = import_accident_dataset(first_scan, SessionLocal, batch_size=10)
        assert second.documents_unchanged == 1
        assert second.chunks_unchanged == 1
        with SessionLocal() as session:
            document = session.scalar(
                select(Document).where(Document.external_id == external_id)
            )
            assert document is not None
            assert document.updated_at == first_updated_at

        _write_jsonl(
            tmp_path / "accident_chunks.jsonl",
            [
                {
                    "chunk_id": 101,
                    "document_id": document_id,
                    "chunk_order": 0,
                    "chunk_text": "내용이 변경된 작동유 드럼 파열 사고",
                    "embedding": None,
                }
            ],
        )
        changed_scan = scan_accident_dataset(
            tmp_path,
            sources=("domestic",),
            limit_per_source=100,
        )
        changed = import_accident_dataset(changed_scan, SessionLocal, batch_size=10)
        assert changed.chunks_updated == 1
        with SessionLocal() as session:
            document = session.scalar(
                select(Document).where(Document.external_id == external_id)
            )
            assert document is not None
            chunk = session.scalar(
                select(DocumentChunk).where(DocumentChunk.document_id == document.id)
            )
            assert chunk is not None
            assert chunk.embedding is None
            assert chunk.embedding_model is None
            assert chunk.embedding_dimension is None
            assert chunk.embedding_status == "pending"
    finally:
        with SessionLocal() as session, session.begin():
            session.execute(delete(Document).where(Document.external_id == external_id))
