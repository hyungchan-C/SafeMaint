import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pytest

BACKEND_SERVICES_DIR = Path(__file__).parents[2] / "backend" / "app" / "services"
if str(BACKEND_SERVICES_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_SERVICES_DIR))

from rag_service import main as rag_main
from rag_service.config import Settings
from rag_service.retrieval import (
    PgvectorRetriever,
    normalize_source_types,
    normalize_text,
    psycopg_database_url,
    scope_sql,
)
from rag_service.main import _grounded_excerpt_answer
from rag_service.schemas import ChatRequest, ChatSource, InternalChatRequest


class FakeEmbedder:
    def encode(self, text: str) -> np.ndarray:
        assert text
        return np.asarray([1.0, 0.0], dtype=np.float32)


def test_normalize_text_and_database_url() -> None:
    assert normalize_text("  안전\r\n\n 점검  ") == "안전\n점검"
    assert (
        psycopg_database_url("postgresql+psycopg://user:pass@db:5432/test")
        == "postgresql://user:pass@db:5432/test"
    )


def test_search_query_contains_equipment_context() -> None:
    retriever = PgvectorRetriever(Settings(), embedder=FakeEmbedder())
    request = ChatRequest.model_validate(
        {
            "question": "청소법",
            "context": {
                "equipment_name": "컨베이어 CV-203",
                "component_name": "벨트",
                "task_type": "청소",
            },
        }
    )

    assert retriever.build_search_query(request) == "컨베이어 CV-203 벨트 청소 청소법"


def test_rag_scope_uses_parameters_instead_of_source_type_sql_literals() -> None:
    document_id = uuid4()
    source_types = normalize_source_types(["incident", "manual", "incident"])

    sql, parameters = scope_sql(source_types, (document_id,))

    assert source_types == ("incident", "manual")
    assert "d.source_type = ANY(%s)" in sql
    assert "d.id = ANY(%s)" in sql
    assert "incident" not in sql
    assert parameters == [["incident", "manual"], [document_id]]
    with pytest.raises(ValueError, match="must not be empty"):
        normalize_source_types([])


def test_grounded_excerpt_answer_contains_manual_text_and_page() -> None:
    answer = _grounded_excerpt_answer(
        [
            ChatSource(
                document_id="doc-1",
                chunk_id="chunk-1",
                title="라이트커튼 매뉴얼",
                source_type="equipment_manual",
                document_scope="company",
                original_filename="manual.pdf",
                document_version=2,
                excerpt="설치 시 반사면과 상호 간섭을 확인하십시오.",
                page=12,
                page_start=12,
                page_end=12,
                similarity=0.8,
            )
        ]
    )

    assert "설치 시 반사면" in answer
    assert "12쪽" in answer


def test_chat_returns_tbm_guidance_when_retrieval_has_no_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rag_main,
        "retriever",
        SimpleNamespace(search=lambda _request: []),
    )

    response = asyncio.run(
        rag_main.chat(
            InternalChatRequest.model_validate(
                {
                    "question": "컨베이어 베어링 교체 작업 TBM 체크리스트",
                    "context": {
                        "equipment_name": "컨베이어 CV-203",
                        "component_name": "베어링",
                        "task_type": "부품 교체",
                    },
                }
            )
        )
    )

    assert response.sources == []
    assert response.retrieval_mode == "hybrid"
    assert "TBM 체크리스트" in response.answer
    assert "[ ]" in response.answer
    assert "작업 중지 기준" in response.answer
    assert response.warning == "검색 범위에서 질문 주제와 일치하는 근거를 찾지 못했습니다."
