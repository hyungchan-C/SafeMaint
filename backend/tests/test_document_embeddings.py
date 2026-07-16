from __future__ import annotations

from os import getenv
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.engine import make_url

from app.services.document_embeddings import (
    _validate_vectors,
    normalize_text_for_embedding,
)


INTEGRATION_ENABLED = (
    getenv("RUN_DB_INTEGRATION") == "1"
    and getenv("ALLOW_TEST_DB_MUTATION") == "1"
)


class FakeEmbedder:
    model_name = "test/fake-normalized"
    device = "cpu"

    def encode(self, texts, batch_size):
        del batch_size
        return [[1.0, 0.0, 0.0] for _ in texts]


def test_embedding_normalization_and_dimension_validation() -> None:
    assert normalize_text_for_embedding("  안전\r\n점검  ") == "안전\n점검"
    assert _validate_vectors([[1.0, 0.0]], 1, None) == 2
    with pytest.raises(Exception, match="dimension"):
        _validate_vectors([[1.0, 0.0], [1.0, 0.0, 0.0]], 2, None)


@pytest.mark.skipif(
    not INTEGRATION_ENABLED,
    reason="Set RUN_DB_INTEGRATION=1 and ALLOW_TEST_DB_MUTATION=1",
)
def test_fake_embedding_is_idempotent_and_pgvector_searches() -> None:
    from app.core.config import settings
    from app.db.models import Document, DocumentChunk
    from app.db.session import SessionLocal
    from app.services.document_embeddings import (
        embed_pending_chunks,
        search_similar_chunks,
    )

    database_name = make_url(settings.database_url).database or ""
    assert database_name.endswith("_test")
    external_id = f"incident:domestic:test-{uuid4().hex}"

    try:
        with SessionLocal() as session, session.begin():
            document = Document(
                external_id=external_id,
                title="임베딩 통합 테스트",
                source_type="incident",
                access_level="restricted",
                metadata_json={
                    "dataset_type": "domestic",
                    "content_quality": "full",
                },
            )
            session.add(document)
            session.flush()
            session.add(
                DocumentChunk(
                    document_id=document.id,
                    chunk_index=0,
                    content="컨베이어 벨트 끼임 사고",
                    content_hash="a" * 64,
                    metadata_json={},
                    embedding_status="pending",
                )
            )

        embedder = FakeEmbedder()
        first = embed_pending_chunks(SessionLocal, embedder, limit=1, batch_size=1)
        assert first.processed == 1
        assert first.newly_ready == 1
        assert first.vector_dimension == 3

        second = embed_pending_chunks(SessionLocal, embedder, limit=1, batch_size=1)
        assert second.processed == 0
        assert second.newly_ready == 0

        dimension, hits = search_similar_chunks(
            SessionLocal,
            embedder,
            query="컨베이어 사고",
            top_k=5,
            min_similarity=0.9,
        )
        assert dimension == 3
        assert any(hit.external_id == external_id for hit in hits)
        assert all(hit.similarity >= 0.9 for hit in hits)

        with SessionLocal() as session:
            document = session.scalar(
                select(Document).where(Document.external_id == external_id)
            )
            assert document is not None
            chunk = session.scalar(
                select(DocumentChunk).where(DocumentChunk.document_id == document.id)
            )
            assert chunk is not None
            assert chunk.embedding_status == "ready"
            assert chunk.embedding_model == embedder.model_name
            assert chunk.embedding_dimension == 3
            assert chunk.metadata_json.get("embedding_error") is None
    finally:
        with SessionLocal() as session, session.begin():
            session.execute(delete(Document).where(Document.external_id == external_id))
