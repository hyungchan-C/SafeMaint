"""
db_loader.load_document()의 실제 DB 적재 검증. backend/tests/test_database_integration.py와
같은 규칙: RUN_DB_INTEGRATION=1, ALLOW_TEST_DB_MUTATION=1이고 DB 이름이 _test로
끝날 때만 실행한다. 기본 상태(Docker 미기동 등)에서는 스킵된다.
"""

from os import getenv

import pytest
from sqlalchemy.engine import make_url

from ai.embeddings import db_loader
from app.core.config import settings
from app.db.models.document import Document
from app.db.session import SessionLocal


INTEGRATION_ENABLED = (
    getenv("RUN_DB_INTEGRATION") == "1" and getenv("ALLOW_TEST_DB_MUTATION") == "1"
)

pytestmark = pytest.mark.skipif(
    not INTEGRATION_ENABLED,
    reason="Set RUN_DB_INTEGRATION=1 and ALLOW_TEST_DB_MUTATION=1 to run DB tests",
)


def _assert_isolated_test_database() -> None:
    database_name = make_url(settings.database_url).database or ""
    if not database_name.endswith("_test"):
        raise RuntimeError(
            "DB integration tests only run against a database ending in '_test'."
        )


def test_load_document_is_idempotent_on_external_id() -> None:
    _assert_isolated_test_database()

    document = {
        "external_id": "manual:테스트제조사:M1:ingest-test-doc",
        "title": "ingest-test-doc",
        "source_type": "manual",
        "publisher": "테스트제조사",
        "access_level": "restricted",
        "file_sha256": "0" * 64,
        "metadata": {"manufacturer": "테스트제조사"},
    }
    first_chunks = [
        {
            "chunk_index": 0,
            "section_path": ["1장"],
            "content": "첫 버전 내용",
            "content_hash": "a" * 64,
            "metadata": {},
            "embedding": [0.1, 0.2],
            "embedding_model": "BAAI/bge-m3",
            "embedding_dimension": 2,
            "embedding_status": "ready",
        }
    ]

    session = SessionLocal()
    try:
        document_id = db_loader.load_document(document, first_chunks)

        # 재처리: 같은 external_id, 청크 내용이 바뀐 경우 -> 갱신되고 중복 생성되면 안 됨
        second_chunks = [
            {**first_chunks[0], "content": "수정된 내용", "content_hash": "b" * 64},
            {
                "chunk_index": 1,
                "section_path": ["2장"],
                "content": "새로 추가된 내용",
                "content_hash": "c" * 64,
                "metadata": {},
                "embedding": [0.3, 0.4],
                "embedding_model": "BAAI/bge-m3",
                "embedding_dimension": 2,
                "embedding_status": "ready",
            },
        ]
        document_id_again = db_loader.load_document(document, second_chunks)

        assert document_id == document_id_again

        row = session.get(Document, document_id)
        assert row is not None
        assert len(row.chunks) == 2
        assert {c.content for c in row.chunks} == {"수정된 내용", "새로 추가된 내용"}
    finally:
        session.rollback()
        session.close()
