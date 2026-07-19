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
