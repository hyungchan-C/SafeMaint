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


def test_selected_owner_draft_scope_is_parameterized() -> None:
    retriever = PgvectorRetriever(Settings(), embedder=object())  # type: ignore[arg-type]

    sql = retriever._candidate_query("")

    assert "dv.status = 'review_required'" in sql
    assert "dv.uploaded_by_user_id = %s::uuid" in sql
    assert "d.id = ANY(%s::uuid[])" in sql


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


def test_selected_manual_follow_up_uses_question_instead_of_stale_form_topic() -> None:
    document_id = uuid4()
    request = InternalChatRequest.model_validate(
        {
            "question": "라이트커튼 설치시 주의사항",
            "context": {
                "equipment_name": "컨베이어 CV-203",
                "component_name": "벨트",
                "selected_document_ids": [str(document_id)],
            },
        }
    )
    retriever = PgvectorRetriever(
        Settings(min_similarity=0.1), embedder=object()  # type: ignore[arg-type]
    )
    relevant = {
        "document_id": str(document_id),
        "chunk_id": "chunk-light-curtain",
        "title": "라이트커튼 사용 설명서",
        "source_type": "equipment_manual",
        "document_scope": "company",
        "original_filename": "light-curtain.pdf",
        "document_version": 1,
        "section": "설치 주의사항",
        "content": "라이트커튼 설치 시 반사면과 상호 간섭을 확인하십시오.",
        "content_hash": "c" * 64,
        "page": 12,
        "page_start": 12,
        "page_end": 12,
        "publisher": None,
        "url": None,
        "similarity": 0.8,
        "postgres_keyword_score": 0.1,
    }

    results = retriever._rerank(request, [relevant])

    assert len(results) == 1
    assert "라이트커튼" in results[0].excerpt


def test_selected_manual_allows_referential_question_without_literal_topic_match() -> None:
    document_id = uuid4()
    request = InternalChatRequest.model_validate(
        {
            "question": "어디에 쓰는 거야?",
            "context": {"selected_document_ids": [str(document_id)]},
        }
    )
    retriever = PgvectorRetriever(
        Settings(min_similarity=0.1), embedder=object()  # type: ignore[arg-type]
    )
    overview = {
        "document_id": str(document_id),
        "chunk_id": "chunk-overview",
        "title": "SFL 라이트커튼 사용 설명서",
        "source_type": "equipment_manual",
        "document_scope": "company",
        "original_filename": "manual.pdf",
        "document_version": 1,
        "section": "제품 개요",
        "content": "SFL은 기계의 위험 영역에 사람이 접근하는 것을 검출하는 안전용 라이트 커튼입니다.",
        "content_hash": "d" * 64,
        "page": 4,
        "page_start": 4,
        "page_end": 4,
        "publisher": None,
        "url": None,
        "similarity": 0.75,
        "postgres_keyword_score": 0.0,
    }

    results = retriever._rerank(request, [overview])

    assert len(results) == 1
    assert "위험 영역" in results[0].excerpt


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
