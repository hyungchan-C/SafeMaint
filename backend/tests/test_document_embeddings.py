from __future__ import annotations

from os import getenv
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.engine import make_url

from app.services.document_embeddings import (
    _validate_vectors,
    normalize_document_ids,
    normalize_source_types,
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


def test_document_scope_normalization_is_explicit() -> None:
    document_id = uuid4()

    assert normalize_source_types(None) is None
    assert normalize_source_types(["incident", "manual", "incident"]) == (
        "incident",
        "manual",
    )
    assert normalize_document_ids([document_id, document_id]) == (document_id,)
    with pytest.raises(ValueError, match="must not be empty"):
        normalize_source_types([])
    with pytest.raises(ValueError, match="blank"):
        normalize_source_types(["incident", " "])
    with pytest.raises(ValueError, match="must not be empty"):
        normalize_document_ids([])


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
    manual_external_id = f"manual:test-{uuid4().hex}"
    incident_document_id = None
    manual_document_id = None

    try:
        with SessionLocal() as session, session.begin():
            document = Document(
                external_id=external_id,
                title="임베딩 통합 테스트",
                source_type="incident",
                document_type_code="public_incident",
                access_level="restricted",
                metadata_json={
                    "dataset_type": "domestic",
                    "content_quality": "full",
                },
            )
            session.add(document)
            session.flush()
            incident_document_id = document.id
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
            manual = Document(
                external_id=manual_external_id,
                title="컨베이어 제조사 매뉴얼",
                source_type="manual",
                document_type_code="equipment_manual",
                access_level="restricted",
                metadata_json={"content_quality": "full"},
            )
            session.add(manual)
            session.flush()
            manual_document_id = manual.id
            session.add(
                DocumentChunk(
                    document_id=manual.id,
                    chunk_index=0,
                    content="베어링 교체 전 전원을 차단하고 잠금장치를 적용한다.",
                    content_hash="b" * 64,
                    metadata_json={},
                    embedding_status="pending",
                )
            )

        assert incident_document_id is not None
        assert manual_document_id is not None

        embedder = FakeEmbedder()
        selected_document_ids = [incident_document_id, manual_document_id]
        first = embed_pending_chunks(
            SessionLocal,
            embedder,
            limit=2,
            batch_size=1,
            source_types=["incident"],
            document_ids=selected_document_ids,
        )
        assert first.processed == 1
        assert first.newly_ready == 1
        assert first.vector_dimension == 3
        assert first.status_counts == {"ready": 1}

        second = embed_pending_chunks(
            SessionLocal,
            embedder,
            limit=2,
            batch_size=1,
            source_types=["incident"],
            document_ids=selected_document_ids,
        )
        assert second.processed == 0
        assert second.newly_ready == 0

        manual_run = embed_pending_chunks(
            SessionLocal,
            embedder,
            limit=1,
            batch_size=1,
            document_ids=[manual_document_id],
        )
        assert manual_run.processed == 1
        assert manual_run.newly_ready == 1

        dimension, hits = search_similar_chunks(
            SessionLocal,
            embedder,
            query="컨베이어 사고",
            top_k=5,
            min_similarity=0.9,
            source_types=["incident"],
            document_ids=selected_document_ids,
        )
        assert dimension == 3
        assert any(hit.external_id == external_id for hit in hits)
        assert all(hit.similarity >= 0.9 for hit in hits)
        assert all(hit.source_type == "incident" for hit in hits)

        _, manual_hits = search_similar_chunks(
            SessionLocal,
            embedder,
            query="베어링 교체",
            top_k=5,
            min_similarity=0.9,
            document_ids=[manual_document_id],
        )
        assert [hit.external_id for hit in manual_hits] == [manual_external_id]
        assert manual_hits[0].source_type == "manual"

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
            session.execute(
                delete(Document).where(
                    Document.external_id.in_((external_id, manual_external_id))
                )
            )
