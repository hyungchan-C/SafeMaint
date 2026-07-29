import asyncio
from uuid import uuid4

from app.db.models import Document, DocumentChunk, DocumentVersion
from app.repositories.document_chunks import DocumentChunkRepository
from app.schemas.chat import ChatRequest, ChatResponse, ChatSource, DocumentAnswerDetails, DocumentOverview, MaintenanceAnswerDetails, MaintenanceSummary
from app.services.chat import (
    ChatService,
    _chat_source_from_chunk,
    _with_page_reference_sources,
)


def _build_chunk() -> DocumentChunk:
    document_id = uuid4()
    version_id = uuid4()
    document = Document(
        id=document_id,
        external_id="ext-1",
        title="라이트 커튼 SFL 시리즈 사용자 매뉴얼",
        source_type="equipment_manual",
        document_type_code="equipment_manual",
    )
    version = DocumentVersion(
        id=version_id,
        document_id=document_id,
        version_number=3,
        original_filename="MSO-SFL_A_U1-V3.1-KO_20250912_W.pdf",
        stored_filename="stored.pdf",
        storage_path="/tmp/stored.pdf",
        sha256="0" * 64,
        file_size=100,
        mime_type="application/pdf",
    )
    chunk = DocumentChunk(
        id=uuid4(),
        document_id=document_id,
        document_version_id=version_id,
        chunk_index=7,
        page_number=19,
        page_start=19,
        page_end=19,
        section_path=["2. 정격 및 성능", "2.6 상세 모델"],
        content="응답시간, 소비전류, 중량 등 정격값을 명시합니다.",
        content_hash="hash",
    )
    chunk.document = document
    chunk.document_version = version
    return chunk


def test_chat_source_from_chunk_maps_document_and_version_fields() -> None:
    chunk = _build_chunk()

    source = _chat_source_from_chunk(chunk)

    assert isinstance(source, ChatSource)
    assert source.document_id == str(chunk.document_id)
    assert source.document_version_id == str(chunk.document_version_id)
    assert source.chunk_id == str(chunk.id)
    assert source.title == "라이트 커튼 SFL 시리즈 사용자 매뉴얼"
    assert source.source_type == "equipment_manual"
    assert source.original_filename == "MSO-SFL_A_U1-V3.1-KO_20250912_W.pdf"
    assert source.section == "2. 정격 및 성능 > 2.6 상세 모델"
    assert source.excerpt == "응답시간, 소비전류, 중량 등 정격값을 명시합니다."
    assert source.page == 19
    assert source.page_start == 19
    assert source.page_end == 19


def test_chat_source_from_chunk_prepends_outline_chapter_title_to_leaf_section() -> None:
    # Real documents often keep only the leaf subsection heading in section_path
    # ("2.6.1 손가락 검출"), dropping the parent chapter title ("2. 정격 및 성능") the
    # card-meaning search actually needs — outline_chapter_title (read from the
    # PDF's own bookmarks, see app.services.pdf_outline) fills that gap.
    chunk = _build_chunk()
    chunk.section_path = ["2.6.1 손가락 검출"]

    source = _chat_source_from_chunk(chunk, outline_chapter_title="2. 정격 및 성능")

    assert source.section == "2. 정격 및 성능 > 2.6.1 손가락 검출"


def test_chat_source_from_chunk_uses_outline_title_alone_without_a_leaf_section() -> None:
    chunk = _build_chunk()
    chunk.section_path = []

    source = _chat_source_from_chunk(chunk, outline_chapter_title="2. 정격 및 성능")

    assert source.section == "2. 정격 및 성능"


def test_chat_source_from_chunk_keeps_leaf_section_without_an_outline_title() -> None:
    chunk = _build_chunk()
    chunk.section_path = ["2.6.1 손가락 검출"]

    source = _chat_source_from_chunk(chunk)

    assert source.section == "2.6.1 손가락 검출"


def test_full_document_sources_skips_db_lookup_without_a_session() -> None:
    service = ChatService()

    result = asyncio.run(service._full_document_sources([], "maintenance_guide", None))

    assert result is None


def test_full_document_sources_ignores_answer_types_without_pdf_page_cards() -> None:
    service = ChatService()

    # document_qa에는 아직 이 페이지 카드가 없으므로, db가 있어도(더미 객체) DB를
    # 건드리지 않고 바로 None을 반환해야 한다.
    result = asyncio.run(
        service._full_document_sources([], "document_qa", object())  # type: ignore[arg-type]
    )

    assert result is None


def test_document_chunk_repository_skips_query_for_empty_input() -> None:
    # session=None이어도(실제로는 Session이어야 하지만) 빈 집합이면 쿼리 자체를
    # 실행하지 않고 바로 빈 리스트를 반환해야 한다 — DB 연결이 필요 없는 경로.
    repository = DocumentChunkRepository(session=None)  # type: ignore[arg-type]

    assert repository.list_for_document_versions(set()) == []


def _maintenance_answer(page_source_ids: list[str]) -> MaintenanceAnswerDetails:
    return MaintenanceAnswerDetails(
        summary=MaintenanceSummary(
            status="안전관리자 확인 필요",
            risk_level="판단 불가",
            core_warning="작업 전 확인 필요",
        ),
        rating_performance_page_source_ids=page_source_ids,
    )


def test_with_page_reference_sources_appends_pages_missing_from_retrieval() -> None:
    # rating_performance_page_source_ids는 전체 문서 재검색(full_document_sources)으로
    # 찾은 청크를 가리킬 수 있는데, 그 청크는 이번 질문의 RAG 검색 결과(sources)엔 원래
    # 없었을 수 있다 — 그러면 프론트가 sources에서 이 id를 못 찾아 카드가 빈 채로 뜬다.
    narrow_source = ChatSource(
        document_id="doc-1", chunk_id="narrow-1", title="t", source_type="equipment_manual",
        excerpt="", similarity=0.9,
    )
    rating_page = ChatSource(
        document_id="doc-1", chunk_id="rating-1", title="t", source_type="equipment_manual",
        section="정격/성능", page=2, excerpt="", similarity=0.0,
    )
    structured_answer = _maintenance_answer(["rating-1"])

    merged = _with_page_reference_sources(
        [narrow_source], structured_answer, [narrow_source, rating_page],
    )

    assert [s.chunk_id for s in merged] == ["narrow-1", "rating-1"]


def test_with_page_reference_sources_does_not_duplicate_known_sources() -> None:
    narrow_source = ChatSource(
        document_id="doc-1", chunk_id="narrow-1", title="t", source_type="equipment_manual",
        excerpt="", similarity=0.9,
    )
    structured_answer = _maintenance_answer(["narrow-1"])

    merged = _with_page_reference_sources(
        [narrow_source], structured_answer, [narrow_source],
    )

    assert [s.chunk_id for s in merged] == ["narrow-1"]


def test_with_page_reference_sources_is_a_noop_without_full_document_sources() -> None:
    narrow_source = ChatSource(
        document_id="doc-1", chunk_id="narrow-1", title="t", source_type="equipment_manual",
        excerpt="", similarity=0.9,
    )
    sources = [narrow_source]
    structured_answer = _maintenance_answer(["rating-1"])

    merged = _with_page_reference_sources(sources, structured_answer, None)

    assert merged is sources


def test_with_page_reference_sources_ignores_non_maintenance_answers() -> None:
    narrow_source = ChatSource(
        document_id="doc-1", chunk_id="narrow-1", title="t", source_type="equipment_manual",
        excerpt="", similarity=0.9,
    )
    rating_page = ChatSource(
        document_id="doc-1", chunk_id="rating-1", title="t", source_type="equipment_manual",
        section="정격/성능", excerpt="", similarity=0.0,
    )
    document_answer = DocumentAnswerDetails(overview=DocumentOverview(filename="f.pdf"))

    merged = _with_page_reference_sources(
        [narrow_source], document_answer, [narrow_source, rating_page],
    )

    assert [s.chunk_id for s in merged] == ["narrow-1"]


def test_qwen_context_response_excludes_page_reference_only_sources() -> None:
    # rating_performance_page_source_ids는 질문 내용과 무관하게 매 유지보수 질문마다
    # 전체 문서에서 "정격/성능" 챕터를 찾아 카드용으로 붙는다 (_with_page_reference_sources).
    # 이 청크가 실제 RAG 검색 결과(narrow retrieval)에는 없었는데도 Qwen 근거로 같이
    # 넘어가면, 질문과 무관한 스펙표가 섞여 Qwen 생성 품질을 떨어뜨린다 — 카드 표시용으로만
    # 쓰고 Qwen 입력에서는 제외해야 한다.
    relevant_source = ChatSource(
        document_id="doc-1", chunk_id="relevant-1", title="t", source_type="equipment_manual",
        section="안전을 위한 주의사항", excerpt="설치 시 주의사항 본문", similarity=0.9,
    )
    rating_page = ChatSource(
        document_id="doc-1", chunk_id="rating-1", title="t", source_type="equipment_manual",
        section="2. 정격 및 성능", page=16, excerpt="스펙표", similarity=0.0,
    )
    structured_answer = _maintenance_answer(["rating-1"])
    response = ChatResponse(
        answer="x",
        answer_type="maintenance_guide",
        sources=[relevant_source, rating_page],
        structured_answer=structured_answer,
        retrieval_mode="hybrid",
    )

    qwen_input = ChatService._qwen_context_response(
        ChatRequest(question="설치 시 주의사항 알려줘"),
        response,
        excluded_source_ids=frozenset({"rating-1"}),
    )

    assert [s.chunk_id for s in qwen_input.sources] == ["relevant-1"]


def test_qwen_context_response_keeps_genuinely_retrieved_sources() -> None:
    # rating-1이 excluded_source_ids에 없다면(narrow 검색이 실제로 찾은 근거라면)
    # 평소처럼 그대로 후보에 남아야 한다 — 제외 대상은 "카드용으로만 덧붙은" 것만이다.
    rating_page = ChatSource(
        document_id="doc-1", chunk_id="rating-1", title="t", source_type="equipment_manual",
        section="2. 정격 및 성능", page=16, excerpt="정격 및 성능 스펙표", similarity=0.9,
    )
    structured_answer = _maintenance_answer(["rating-1"])
    response = ChatResponse(
        answer="x",
        answer_type="maintenance_guide",
        sources=[rating_page],
        structured_answer=structured_answer,
        retrieval_mode="hybrid",
    )

    qwen_input = ChatService._qwen_context_response(
        ChatRequest(question="정격 및 성능 알려줘"),
        response,
        excluded_source_ids=frozenset(),
    )

    assert [s.chunk_id for s in qwen_input.sources] == ["rating-1"]
