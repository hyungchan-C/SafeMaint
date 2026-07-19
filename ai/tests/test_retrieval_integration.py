from os import getenv
from uuid import uuid4

import numpy as np
import psycopg
import pytest
from pgvector.psycopg import register_vector

from rag_service.config import Settings
from rag_service.retrieval import PgvectorRetriever, psycopg_database_url
from rag_service.schemas import InternalChatRequest


pytestmark = pytest.mark.skipif(
    getenv("RUN_DB_INTEGRATION") != "1",
    reason="Run only against an isolated test database.",
)


class FakeEmbedder:
    def encode(self, text: str) -> np.ndarray:
        assert text
        return np.asarray([1.0, 0.0, 0.0], dtype=np.float32)


def test_public_only_and_selected_company_scope_are_enforced() -> None:
    settings = Settings(top_k=10, candidate_k=20, min_similarity=0.1)
    assert settings.database_url.rsplit("/", 1)[-1].endswith("_test")
    public_document_id = uuid4()
    company_document_id = uuid4()
    database_url = psycopg_database_url(settings.database_url)
    with psycopg.connect(database_url) as connection:
        register_vector(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO documents
                    (id, external_id, title, source_type, document_type_code,
                     lifecycle_status, access_level, metadata)
                VALUES
                    (%s, %s, 'Public conveyor bearing guide', 'guide',
                     'public_guide', 'active', 'public', '{}'::jsonb),
                    (%s, %s, 'Company conveyor bearing manual', 'manual',
                     'equipment_manual', 'active', 'restricted', '{}'::jsonb)
                """,
                (
                    public_document_id,
                    f"retrieval-public:{public_document_id}",
                    company_document_id,
                    f"retrieval-company:{company_document_id}",
                ),
            )
            for document_id, content_hash in (
                (public_document_id, "a" * 64),
                (company_document_id, "b" * 64),
            ):
                cursor.execute(
                    """
                    INSERT INTO document_chunks
                        (document_id, chunk_index, content, content_hash,
                         embedding, embedding_model, embedding_dimension,
                         embedding_status, section_path, metadata)
                    VALUES
                        (%s, 0, 'Conveyor bearing replacement requires energy isolation.',
                         %s, %s, %s, 3, 'ready', '[]'::jsonb, '{}'::jsonb)
                    """,
                    (
                        document_id,
                        content_hash,
                        [1.0, 0.0, 0.0],
                        settings.model_name,
                    ),
                )
        connection.commit()

    retriever = PgvectorRetriever(settings, embedder=FakeEmbedder())
    selected_but_unauthenticated = InternalChatRequest.model_validate(
        {
            "question": "conveyor bearing replacement",
            "context": {"selected_document_ids": [str(company_document_id)]},
        }
    )
    public_only = retriever.search(selected_but_unauthenticated)

    assert str(public_document_id) in {source.document_id for source in public_only}
    assert str(company_document_id) not in {
        source.document_id for source in public_only
    }

    authorized = InternalChatRequest.model_validate(
        {
            "question": "conveyor bearing replacement",
            "context": {"selected_document_ids": [str(company_document_id)]},
            "access_scope": {
                "allow_company": True,
                "all_sites": True,
                "allow_private": False,
            },
        }
    )
    combined = retriever.search(authorized)
    combined_ids = {source.document_id for source in combined}

    assert str(public_document_id) in combined_ids
    assert str(company_document_id) in combined_ids
    assert {source.document_scope for source in combined} == {"public", "company"}

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM documents WHERE id = ANY(%s::uuid[])",
                ([public_document_id, company_document_id],),
            )
        connection.commit()
