import pytest

from app.schemas.chat import QueryAnalysis
from app.services.ai import AIService


class FakeProvider:
    def __init__(self, *, answer: str = "전원을 차단합니다 [1].") -> None:
        self.answer_text = answer
        self.models: list[str] = []

    def generate_json(
        self, *, model, instructions, user_input, schema, max_output_tokens
    ):
        assert "do not decide risk level" in instructions
        assert "bearing" in user_input
        assert max_output_tokens > 0
        self.models.append(model)
        return schema.model_validate(
            {
                "work_type": "replacement",
                "component": ["bearing"],
                "search_keywords": ["conveyor", "bearing", "replacement"],
            }
        )

    def generate_text(
        self, *, model, instructions, user_input, max_output_tokens
    ) -> str:
        assert "only the numbered evidence" in instructions
        assert "[1]" in user_input
        assert max_output_tokens > 0
        self.models.append(model)
        return self.answer_text


def test_fake_provider_supports_structured_analysis_and_grounded_answer() -> None:
    provider = FakeProvider()
    service = AIService(provider=provider)

    analysis = service.analyze("bearing replacement", '{"equipment":"conveyor"}')
    answer = service.answer(
        "bearing replacement",
        "[1] title: Manual\n[1] evidence: Disconnect power.",
    )

    assert isinstance(analysis, QueryAnalysis)
    assert analysis.occurrence_type is None
    assert analysis.component == ["bearing"]
    assert answer.endswith("[1].")
    assert len(provider.models) == 2


def test_answer_with_invalid_citation_is_rejected() -> None:
    service = AIService(provider=FakeProvider(answer="확인합니다 [2]."))

    with pytest.raises(RuntimeError, match="citations"):
        service.answer(
            "bearing replacement",
            "[1] title: Manual\n[1] evidence: Disconnect power.",
        )
