from uuid import uuid4

import pytest

from rag_service.config import Settings
from rag_service.retrieval import (
    PgvectorRetriever,
    domain_phrase_query_terms,
    maintenance_query_expansions,
    _term_matches,
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


def test_legacy_source_type_aliases_map_to_canonical_document_types() -> None:
    assert normalize_source_types(["incident", "manual", "regulation"]) == (
        "public_incident",
        "equipment_manual",
        "public_law",
    )


def test_korean_compound_topic_matches_spaced_manual_text() -> None:
    assert _term_matches(
        "라이트커튼",
        "설치 시 라이트 커튼의 기능을 다시 설정하십시오.",
    )


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

    assert normalize_text(query) == "베어링 교체 절차"
    assert request.access_scope.site_ids[0] not in query


def test_specific_question_excludes_unrelated_form_context() -> None:
    request = InternalChatRequest.model_validate(
        {
            "question": "라이트커튼 설치시 주의사항",
            "context": {
                "equipment_name": "컨베이어 CV-203",
                "component_name": "벨트",
                "task_type": "이물질 제거",
                "task_description": "컨베이어 벨트에 낀 이물질을 제거합니다.",
            },
        }
    )
    retriever = PgvectorRetriever(Settings(), embedder=object())  # type: ignore[arg-type]

    query = retriever.build_search_query(request)

    assert query.startswith("라이트커튼 설치시 주의사항")
    assert "광전자식 방호장치" in query
    assert "컨베이어" not in query
    assert "이물질" not in query


def test_classifier_label_does_not_override_specific_question_topic() -> None:
    request = InternalChatRequest.model_validate(
        {
            "question": "베어링 교체작업",
            "analysis": {
                "occurrence_type": "끼임",
                "search_keywords": ["베어링", "교체작업"],
            },
        }
    )
    retriever = PgvectorRetriever(Settings(), embedder=object())  # type: ignore[arg-type]

    assert retriever.build_search_query(request) == "베어링 교체작업"


def test_maintenance_query_expands_public_safety_terms() -> None:
    request = InternalChatRequest.model_validate(
        {
            "question": "컨베이어 청소할 때 작업 전 확인사항 알려줘",
            "analysis": {"question_intent": "maintenance_guide"},
        }
    )
    retriever = PgvectorRetriever(Settings(), embedder=object())  # type: ignore[arg-type]

    query = retriever.build_search_query(request)

    assert "컨베이어 청소할 때 작업 전 확인사항 알려줘" in query
    assert "전원" in query
    assert "차단" in query
    assert "끼임" in query
    assert "협착" in query


def test_maintenance_query_expansion_is_action_specific() -> None:
    assert maintenance_query_expansions("컨베이어 청소 작업") == (
        "청소",
        "정지",
        "전원",
        "차단",
        "잠금",
        "재가동",
        "끼임",
        "협착",
        "사고",
        "예방",
    )


def test_maintenance_public_safety_match_survives_equipment_topic_gap() -> None:
    request = InternalChatRequest.model_validate(
        {
            "question": "컨베이어 청소할 때 작업 전 확인사항 알려줘",
            "analysis": {"question_intent": "maintenance_guide"},
        }
    )
    retriever = PgvectorRetriever(
        Settings(min_similarity=0.1, maintenance_top_k=4),
        embedder=object(),  # type: ignore[arg-type]
    )
    safety_row = {
        "document_id": "public-cleaning-safety",
        "chunk_id": "chunk-cleaning-safety",
        "title": "청소 작업 사고 예방 지침",
        "source_type": "public_guide",
        "document_scope": "public",
        "original_filename": "cleaning-safety.pdf",
        "document_version": 1,
        "section": "청소 작업 전 확인",
        "content": "기계 청소 작업 전에는 운전을 정지하고 전원을 차단하며 잠금 조치로 끼임 사고를 예방한다.",
        "content_hash": "p" * 64,
        "page": 2,
        "page_start": 2,
        "page_end": 2,
        "publisher": "public source",
        "url": None,
        "similarity": 0.75,
        "postgres_keyword_score": 0.1,
    }

    results = retriever._rerank(request, [safety_row])

    assert [result.chunk_id for result in results] == ["chunk-cleaning-safety"]


def test_domain_phrase_query_expands_light_curtain_aliases() -> None:
    terms = domain_phrase_query_terms("라이트 커튼 설치 작업")

    assert "라이트커튼" in terms
    assert "광전자식 방호장치" in terms
    assert "ESPE" in terms


def test_light_curtain_query_keeps_photoelectric_sources_before_curtain_wall() -> None:
    request = InternalChatRequest.model_validate(
        {
            "question": "라이트 커튼 설치 할 거야",
            "analysis": {"question_intent": "maintenance_guide"},
        }
    )
    retriever = PgvectorRetriever(
        Settings(min_similarity=0.1, maintenance_top_k=4),
        embedder=object(),  # type: ignore[arg-type]
    )
    curtain_wall = {
        "document_id": "public-curtain-wall",
        "chunk_id": "chunk-curtain-wall",
        "title": "금속 커튼월(Curtain wall) 안전작업 지침",
        "source_type": "public_guide",
        "document_scope": "public",
        "original_filename": "curtain-wall.pdf",
        "document_version": 1,
        "section": "설치 시 안전조치",
        "content": "커튼월 설치작업 전 작업계획서를 작성하고 양중장비와 작업발판을 확인한다.",
        "content_hash": "q" * 64,
        "page": 2,
        "page_start": 2,
        "page_end": 2,
        "publisher": "public source",
        "url": None,
        "similarity": 0.95,
        "postgres_keyword_score": 0.3,
    }
    photoelectric = {
        "document_id": "public-photoelectric",
        "chunk_id": "chunk-photoelectric",
        "title": "광전자식 방호장치 설치 지침",
        "source_type": "public_guide",
        "document_scope": "public",
        "original_filename": "photoelectric.pdf",
        "document_version": 1,
        "section": "광전자식 방호장치",
        "content": "광전자식 방호장치는 위험한 움직임을 멈출 수 있는 안전거리에 설치하고 광축을 확인한다.",
        "content_hash": "r" * 64,
        "page": 5,
        "page_start": 5,
        "page_end": 5,
        "publisher": "public source",
        "url": None,
        "similarity": 0.75,
        "postgres_keyword_score": 0.1,
    }

    results = retriever._rerank(request, [curtain_wall, photoelectric])

    assert [result.chunk_id for result in results] == ["chunk-photoelectric"]


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


def test_selected_manual_topic_mismatch_is_filtered() -> None:
    document_id = uuid4()
    request = InternalChatRequest.model_validate(
        {
            "question": "프레스 설비 내부를 청소할 예정이야.",
            "context": {"selected_document_ids": [str(document_id)]},
        }
    )
    retriever = PgvectorRetriever(
        Settings(min_similarity=0.1), embedder=object()  # type: ignore[arg-type]
    )
    unrelated_selected = {
        "document_id": str(document_id),
        "chunk_id": "chunk-light-curtain",
        "title": "라이트커튼 사용 설명서",
        "source_type": "equipment_manual",
        "document_scope": "company",
        "original_filename": "light-curtain.pdf",
        "document_version": 1,
        "section": "제품 개요",
        "content": "라이트커튼은 광축 차단을 감지하는 안전장치입니다.",
        "content_hash": "e" * 64,
        "page": 4,
        "page_start": 4,
        "page_end": 4,
        "publisher": None,
        "url": None,
        "similarity": 0.9,
        "postgres_keyword_score": 0.0,
    }

    assert retriever._rerank(request, [unrelated_selected]) == []


def test_public_supplement_requires_selected_maintenance_intent() -> None:
    document_id = uuid4()
    retriever = PgvectorRetriever(Settings(), embedder=object())  # type: ignore[arg-type]

    maintenance_request = InternalChatRequest.model_validate(
        {
            "question": "프레스 설비 내부를 청소할 예정이야.",
            "context": {"selected_document_ids": [str(document_id)]},
            "analysis": {"question_intent": "maintenance_guide"},
            "access_scope": {"allow_company": True},
        }
    )
    document_request = InternalChatRequest.model_validate(
        {
            "question": "라이트 커튼 문서 요약해줘.",
            "context": {"selected_document_ids": [str(document_id)]},
            "analysis": {"question_intent": "document_qa"},
            "access_scope": {"allow_company": True},
        }
    )
    component_request = InternalChatRequest.model_validate(
        {
            "question": "라이트 커튼이 뭐야?",
            "context": {"selected_document_ids": [str(document_id)]},
            "analysis": {"question_intent": "component_info"},
            "access_scope": {"allow_company": True},
        }
    )

    assert retriever._include_public_supplement(maintenance_request, None)
    assert not retriever._include_public_supplement(document_request, None)
    assert retriever._include_public_supplement(component_request, None)
    assert not retriever._include_public_supplement(
        maintenance_request,
        (document_id,),
    )


def test_public_source_can_supplement_selected_manual_when_topic_matches() -> None:
    document_id = uuid4()
    request = InternalChatRequest.model_validate(
        {
            "question": "프레스 설비 내부를 청소할 예정이야.",
            "context": {"selected_document_ids": [str(document_id)]},
        }
    )
    retriever = PgvectorRetriever(
        Settings(min_similarity=0.1, top_k=5), embedder=object()  # type: ignore[arg-type]
    )
    selected_unrelated = {
        "document_id": str(document_id),
        "chunk_id": "chunk-light-curtain",
        "title": "라이트커튼 사용 설명서",
        "source_type": "equipment_manual",
        "document_scope": "company",
        "original_filename": "light-curtain.pdf",
        "document_version": 1,
        "section": "제품 개요",
        "content": "라이트커튼은 광축 차단을 감지하는 안전장치입니다.",
        "content_hash": "f" * 64,
        "page": 4,
        "page_start": 4,
        "page_end": 4,
        "publisher": None,
        "url": None,
        "similarity": 0.9,
        "postgres_keyword_score": 0.0,
    }
    public_relevant = {
        "document_id": "public-doc",
        "chunk_id": "chunk-press-cleaning",
        "title": "프레스 설비 청소 안전 지침",
        "source_type": "public_guide",
        "document_scope": "public",
        "original_filename": "press-cleaning.pdf",
        "document_version": 1,
        "section": "프레스 청소",
        "content": "프레스 설비 내부 청소 전에는 전원을 차단하고 재가동 방지 조치를 확인한다.",
        "content_hash": "g" * 64,
        "page": 2,
        "page_start": 2,
        "page_end": 2,
        "publisher": "public source",
        "url": None,
        "similarity": 0.8,
        "postgres_keyword_score": 0.1,
    }

    results = retriever._rerank(request, [selected_unrelated, public_relevant])

    assert [result.chunk_id for result in results] == ["chunk-press-cleaning"]


def test_public_curtain_wall_does_not_supplement_selected_light_curtain_manual() -> None:
    document_id = uuid4()
    request = InternalChatRequest.model_validate(
        {
            "question": "라이트 커튼 설치할 거야.",
            "context": {"selected_document_ids": [str(document_id)]},
            "analysis": {"question_intent": "maintenance_guide"},
        }
    )
    retriever = PgvectorRetriever(
        Settings(min_similarity=0.1, top_k=5), embedder=object()  # type: ignore[arg-type]
    )
    selected_manual = {
        "document_id": str(document_id),
        "chunk_id": "chunk-light-curtain-install",
        "title": "라이트 커튼 사용자 매뉴얼",
        "source_type": "equipment_manual",
        "document_scope": "company",
        "original_filename": "light-curtain.pdf",
        "document_version": 1,
        "section": "안전을 위한 주의사항",
        "content": "라이트 커튼 설치 시 기능 설정 후 의도한 대로 동작하는지 확인한다.",
        "content_hash": "h" * 64,
        "page": 7,
        "page_start": 7,
        "page_end": 9,
        "publisher": None,
        "url": None,
        "similarity": 0.8,
        "postgres_keyword_score": 0.1,
    }
    public_curtain_wall = {
        "document_id": "public-curtain-wall",
        "chunk_id": "chunk-curtain-wall",
        "title": "금속 커튼월(Curtain wall) 안전작업 지침 - 설치 시 안전조치 사항",
        "source_type": "public_guide",
        "document_scope": "public",
        "original_filename": "curtain-wall.pdf",
        "document_version": 1,
        "section": "설치 시 안전조치 사항",
        "content": "커튼월 설치 작업 전 양중용 로프의 이상 유무를 점검하여야 한다.",
        "content_hash": "i" * 64,
        "page": 3,
        "page_start": 3,
        "page_end": 3,
        "publisher": "public source",
        "url": None,
        "similarity": 0.9,
        "postgres_keyword_score": 0.1,
    }

    results = retriever._rerank(request, [selected_manual, public_curtain_wall])

    assert [result.chunk_id for result in results] == ["chunk-light-curtain-install"]


def test_document_summary_allows_diverse_chunks_from_same_document() -> None:
    document_id = uuid4()
    request = InternalChatRequest.model_validate(
        {
            "question": "이 문서 요약해줘.",
            "context": {"selected_document_ids": [str(document_id)]},
            "analysis": {"question_intent": "document_qa"},
        }
    )
    retriever = PgvectorRetriever(
        Settings(min_similarity=0.1, top_k=4, max_chunks_per_document=2),
        embedder=object(),  # type: ignore[arg-type]
    )

    def row(chunk_id: str, section: str, content: str, page: int) -> dict:
        return {
            "document_id": str(document_id),
            "chunk_id": chunk_id,
            "title": "장비 사용자 매뉴얼",
            "source_type": "equipment_manual",
            "document_scope": "company",
            "original_filename": "manual.pdf",
            "document_version": 1,
            "section": section,
            "content": content,
            "content_hash": chunk_id[-1] * 64,
            "page": page,
            "page_start": page,
            "page_end": page,
            "publisher": None,
            "url": None,
            "similarity": 0.8,
            "postgres_keyword_score": 0.0,
        }

    results = retriever._rerank(
        request,
        [
            row("chunk-overview", "개요", "제품 개요와 적용 범위를 설명한다.", 1),
            row("chunk-safety", "안전 주의사항", "작업 전 안전 주의사항과 위험요인을 확인한다.", 5),
            row("chunk-procedure", "설치 절차", "설치 및 점검 절차를 설명한다.", 12),
            row("chunk-spec", "모델 구성", "모델 구성과 사양 정보를 설명한다.", 20),
        ],
    )

    assert [result.chunk_id for result in results] == [
        "chunk-overview",
        "chunk-safety",
        "chunk-procedure",
        "chunk-spec",
    ]


def test_document_qa_without_selected_document_stops_before_embedding() -> None:
    class MustNotEmbed:
        def encode(self, text: str):
            raise AssertionError("Document QA without a selected document must not search")

    request = InternalChatRequest.model_validate(
        {
            "question": "이 PDF를 요약해줘.",
            "analysis": {"question_intent": "document_qa"},
        }
    )
    retriever = PgvectorRetriever(Settings(), embedder=MustNotEmbed())  # type: ignore[arg-type]

    assert retriever.search(request) == []


def test_maintenance_action_match_is_ranked_above_generic_topic_match() -> None:
    request = InternalChatRequest.model_validate(
        {
            "question": "프레스 설비 내부를 청소할 예정이야.",
            "analysis": {"question_intent": "maintenance_guide"},
        }
    )
    retriever = PgvectorRetriever(
        Settings(min_similarity=0.1, top_k=2), embedder=object()  # type: ignore[arg-type]
    )
    noise = {
        "document_id": "public-noise",
        "chunk_id": "chunk-noise",
        "title": "설비 소음 제어 지침",
        "source_type": "public_guide",
        "document_scope": "public",
        "original_filename": "noise.pdf",
        "document_version": 1,
        "section": "소음 제어",
        "content": "프레스 설비 주변의 소음 노출을 줄이기 위한 흡음 대책을 설명한다.",
        "content_hash": "j" * 64,
        "page": 2,
        "page_start": 2,
        "page_end": 2,
        "publisher": "public source",
        "url": None,
        "similarity": 0.95,
        "postgres_keyword_score": 0.0,
    }
    action_match = {
        "document_id": "public-action",
        "chunk_id": "chunk-cleaning",
        "title": "설비 정비 안전 지침",
        "source_type": "public_guide",
        "document_scope": "public",
        "original_filename": "maintenance.pdf",
        "document_version": 1,
        "section": "청소 작업 전 확인",
        "content": "프레스 설비 청소 전 전원 차단과 재가동 방지 상태를 확인한다.",
        "content_hash": "k" * 64,
        "page": 6,
        "page_start": 6,
        "page_end": 6,
        "publisher": "public source",
        "url": None,
        "similarity": 0.75,
        "postgres_keyword_score": 0.0,
    }

    results = retriever._rerank(request, [noise, action_match])

    assert results[0].chunk_id == "chunk-cleaning"


def test_maintenance_action_mismatch_is_excluded() -> None:
    request = InternalChatRequest.model_validate(
        {
            "question": "프레스 설비 베어링을 교체할 예정이야.",
            "analysis": {"question_intent": "maintenance_guide"},
        }
    )
    retriever = PgvectorRetriever(
        Settings(min_similarity=0.1, maintenance_top_k=4),
        embedder=object(),  # type: ignore[arg-type]
    )
    mismatch = {
        "document_id": "public-cleaning",
        "chunk_id": "chunk-cleaning",
        "title": "프레스 설비 청소 지침",
        "source_type": "public_guide",
        "document_scope": "public",
        "original_filename": "cleaning.pdf",
        "document_version": 1,
        "section": "청소",
        "content": "프레스 설비 내부 청소 전 전원을 차단한다.",
        "content_hash": "m" * 64,
        "page": 2,
        "page_start": 2,
        "page_end": 2,
        "publisher": "public source",
        "url": None,
        "similarity": 0.95,
        "postgres_keyword_score": 0.2,
    }
    matching = {
        **mismatch,
        "document_id": "manual-replacement",
        "chunk_id": "chunk-replacement",
        "title": "프레스 베어링 교체 매뉴얼",
        "source_type": "component_manual",
        "document_scope": "company",
        "original_filename": "bearing.pdf",
        "section": "베어링 교체",
        "content_hash": "n" * 64,
        "content": "프레스 설비 베어링 교체 시 축을 지지한다.",
        "similarity": 0.75,
    }

    results = retriever._rerank(request, [mismatch, matching])

    assert [result.chunk_id for result in results] == ["chunk-replacement"]


def test_conveyor_replacement_keeps_related_maintenance_incident() -> None:
    request = InternalChatRequest.model_validate(
        {
            "question": "컨베이어 벨트를 교체해야 해.",
            "analysis": {"question_intent": "maintenance_guide"},
        }
    )
    retriever = PgvectorRetriever(
        Settings(min_similarity=0.1, maintenance_top_k=4),
        embedder=object(),  # type: ignore[arg-type]
    )
    incident = {
        "document_id": "incident-conveyor",
        "chunk_id": "incident-belt-conveyor",
        "title": "벨트컨베이어에 협착",
        "source_type": "public_incident",
        "document_scope": "public",
        "original_filename": "belt-conveyor-incident.pdf",
        "document_version": 1,
        "section": "재해 원인",
        "content": "벨트컨베이어 정비 작업 중 운전을 정지하지 않아 협착 사고가 발생했다.",
        "content_hash": "z" * 64,
        "page": 1,
        "page_start": 1,
        "page_end": 1,
        "publisher": "public source",
        "url": None,
        "similarity": 0.8,
        "postgres_keyword_score": 0.2,
    }

    results = retriever._rerank(request, [incident])

    assert [result.chunk_id for result in results] == ["incident-belt-conveyor"]


def test_conveyor_belt_replacement_keeps_plain_konveyor_incident_title() -> None:
    request = InternalChatRequest.model_validate(
        {
            "question": "컨베이어 벨트를 교체해야 해.",
            "analysis": {"question_intent": "maintenance_guide"},
        }
    )
    retriever = PgvectorRetriever(
        Settings(min_similarity=0.1, maintenance_top_k=4),
        embedder=object(),  # type: ignore[arg-type]
    )
    incident = {
        "document_id": "incident-konveyor",
        "chunk_id": "incident-plain-konveyor",
        "title": "콘베이어로 이송물질 운반 중 협착사고",
        "source_type": "public_incident",
        "document_scope": "public",
        "original_filename": "konveyor-incident.pdf",
        "document_version": 1,
        "section": "재해 원인",
        "content": "콘베이어 정비 작업 중 운전이 정지되지 않아 협착사고가 발생했다.",
        "content_hash": "y" * 64,
        "page": 1,
        "page_start": 1,
        "page_end": 1,
        "publisher": "public source",
        "url": None,
        "similarity": 0.8,
        "postgres_keyword_score": 0.2,
    }

    results = retriever._rerank(request, [incident])

    assert [result.chunk_id for result in results] == ["incident-plain-konveyor"]


def test_maintenance_bucket_quotas_prevent_one_source_type_from_dominating() -> None:
    request = InternalChatRequest.model_validate(
        {
            "question": "프레스 설비를 점검할 예정이야.",
            "analysis": {"question_intent": "maintenance_guide"},
        }
    )
    retriever = PgvectorRetriever(
        Settings(
            min_similarity=0.1,
            maintenance_top_k=4,
            maintenance_manual_quota=1,
            maintenance_company_policy_quota=1,
            maintenance_law_quota=1,
            maintenance_guide_quota=1,
            maintenance_incident_quota=0,
        ),
        embedder=object(),  # type: ignore[arg-type]
    )

    def row(index: int, source_type: str) -> dict:
        return {
            "document_id": f"doc-{index}",
            "chunk_id": f"chunk-{index}",
            "title": "프레스 설비 점검",
            "source_type": source_type,
            "document_scope": (
                "company"
                if source_type in {"equipment_manual", "company_policy"}
                else "public"
            ),
            "original_filename": f"source-{index}.pdf",
            "document_version": 1,
            "section": "점검",
            "content": "프레스 설비 점검 전 안전 상태를 확인한다.",
            "content_hash": str(index) * 64,
            "page": index,
            "page_start": index,
            "page_end": index,
            "publisher": "source",
            "url": None,
            "similarity": 0.95 - index / 100,
            "postgres_keyword_score": 0.1,
        }

    results = retriever._rerank(
        request,
        [
            row(1, "equipment_manual"),
            row(2, "component_manual"),
            row(3, "company_policy"),
            row(4, "public_law"),
            row(5, "public_guide"),
        ],
    )

    assert [result.source_type for result in results] == [
        "equipment_manual",
        "company_policy",
        "public_law",
        "public_guide",
    ]


def test_document_neighbor_loader_uses_same_document_version_and_window() -> None:
    class FakeCursor:
        def __init__(self) -> None:
            self.query = ""
            self.parameters: tuple[object, ...] = ()

        def execute(self, query: str, parameters: tuple[object, ...]) -> None:
            self.query = query
            self.parameters = parameters

        def fetchall(self) -> list[dict]:
            return [
                {
                    "document_id": "doc-1",
                    "document_version_id": "version-1",
                    "chunk_id": "neighbor-1",
                    "chunk_index": 4,
                    "similarity": 0.0,
                }
            ]

    retriever = PgvectorRetriever(
        Settings(
            min_similarity=0.25,
            document_neighbor_window=1,
        ),
        embedder=object(),  # type: ignore[arg-type]
    )
    cursor = FakeCursor()
    rows = retriever._load_adjacent_rows(
        cursor,
        [
            {
                "document_id": "doc-1",
                "document_version_id": "version-1",
                "chunk_id": "seed-1",
                "chunk_index": 5,
                "similarity": 0.8,
            }
        ],
    )

    assert "COALESCE(dc.document_version_id::text, '') = %s" in cursor.query
    assert cursor.parameters == (
        retriever.settings.model_name,
        "doc-1",
        "version-1",
        4,
        6,
    )
    assert rows[0]["similarity"] == pytest.approx(0.79)


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
