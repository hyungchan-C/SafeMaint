import numpy as np

from rag_service.config import Settings
from rag_service.retrieval import PgvectorRetriever, normalize_text, psycopg_database_url
from rag_service.schemas import ChatRequest


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
