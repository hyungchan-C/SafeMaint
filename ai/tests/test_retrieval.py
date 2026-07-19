from uuid import uuid4

import pytest

from rag_service.config import Settings
from rag_service.retrieval import (
    PgvectorRetriever,
    normalize_document_ids,
    normalize_source_types,
    normalize_text,
    scope_sql,
)
from rag_service.schemas import InternalChatRequest


def test_scope_filters_are_parameterized() -> None:
    document_id = uuid4()
    source_types = normalize_source_types(["public_incident", "equipment_manual"])
    document_ids = normalize_document_ids([document_id, document_id])

    clause, parameters = scope_sql(source_types, document_ids)

    assert "ANY(%s)" in clause
    assert "public_incident" not in clause
    assert parameters == [["public_incident", "equipment_manual"], [document_id]]


def test_empty_explicit_scope_is_rejected() -> None:
    with pytest.raises(ValueError):
        normalize_source_types([])
    with pytest.raises(ValueError):
        normalize_document_ids([])


def test_search_text_combines_context_without_access_scope_content() -> None:
    request = InternalChatRequest.model_validate(
        {
            "question": "베어링 교체 절차",
            "context": {"equipment_name": "컨베이어 CV-203"},
            "access_scope": {"site_ids": [str(uuid4())], "all_sites": False},
        }
    )
    retriever = PgvectorRetriever(Settings(), embedder=object())  # type: ignore[arg-type]

    query = retriever.build_search_query(request)

    assert normalize_text(query) == "컨베이어 CV-203 베어링 교체 절차"
    assert request.access_scope.site_ids[0] not in query


def test_default_access_scope_is_strict_public_only() -> None:
    request = InternalChatRequest(question="conveyor bearing replacement")
    retriever = PgvectorRetriever(Settings(), embedder=object())  # type: ignore[arg-type]

    sql = retriever._candidate_query("")

    assert request.access_scope.allow_company is False
    assert "dt.scope = 'public'" in sql
    assert "d.access_level = 'public'" in sql
    assert "AND dt.scope = 'company'" in sql


def test_old_uuid_manual_values_are_backward_compatible() -> None:
    document_id = uuid4()
    request = InternalChatRequest.model_validate(
        {
            "question": "manual search",
            "context": {
                "registered_manuals": [str(document_id), "old-file-name.pdf"],
            },
        }
    )

    assert request.context.effective_document_ids() == (document_id,)


def test_topic_mismatch_is_removed_before_answering() -> None:
    request = InternalChatRequest.model_validate(
        {
            "question": "light curtain replacement",
            "context": {"component_name": "light curtain"},
        }
    )
    retriever = PgvectorRetriever(
        Settings(min_similarity=0.1), embedder=object()  # type: ignore[arg-type]
    )
    unrelated = {
        "document_id": "doc-1",
        "chunk_id": "chunk-1",
        "title": "Conveyor belt entanglement",
        "source_type": "public_incident",
        "document_scope": "public",
        "original_filename": "conveyor.pdf",
        "document_version": 1,
        "section": "belt cleaning",
        "content": "Lock out the conveyor before belt cleaning.",
        "content_hash": "a" * 64,
        "page": 1,
        "page_start": 1,
        "page_end": 1,
        "publisher": "public source",
        "url": None,
        "similarity": 0.95,
        "postgres_keyword_score": 0.0,
    }

    assert retriever._rerank(request, [unrelated]) == []


def test_multiple_relevant_chunks_per_document_are_allowed_and_bounded() -> None:
    request = InternalChatRequest(question="conveyor bearing replacement")
    retriever = PgvectorRetriever(
        Settings(top_k=5, max_chunks_per_document=2, min_similarity=0.1),
        embedder=object(),  # type: ignore[arg-type]
    )

    def row(index: int) -> dict:
        return {
            "document_id": "doc-1",
            "chunk_id": f"chunk-{index}",
            "title": "Conveyor bearing manual",
            "source_type": "public_guide",
            "document_scope": "public",
            "original_filename": "manual.pdf",
            "document_version": 1,
            "section": f"bearing {index}",
            "content": f"Conveyor bearing replacement step {index}",
            "content_hash": str(index) * 64,
            "page": index,
            "page_start": index,
            "page_end": index,
            "publisher": "public source",
            "url": None,
            "similarity": 0.8 - index / 100,
            "postgres_keyword_score": 0.1,
        }

    results = retriever._rerank(request, [row(1), row(2), row(3)])

    assert len(results) == 2
    assert all(result.reranker_score > 0 for result in results)
