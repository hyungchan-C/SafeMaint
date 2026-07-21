import asyncio
import json

import httpx

from app.schemas.chat import ChatRequest
from app.services.accident_classifier import AccidentClassifierClient


def test_classifier_client_builds_team_model_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/classify"
        body = json.loads(request.content)
        assert body["title"] == "컨베이어 베어링 교체"
        assert "롤러에 손이 말려 들어감" in body["text"]
        assert "어떤 사고가 예상돼?" in body["text"]
        return httpx.Response(
            200,
            json={
                "label": "끼임",
                "model": "Qwen/Qwen3.5-9B",
                "adapter": "best_adapter",
            },
        )

    client = AccidentClassifierClient(
        "http://classifier.test",
        transport=httpx.MockTransport(handler),
    )
    result = asyncio.run(
        client.classify(
            ChatRequest.model_validate(
                {
                    "question": "어떤 사고가 예상돼?",
                    "context": {
                        "equipment_name": "컨베이어",
                        "component_name": "베어링",
                        "task_type": "교체",
                        "task_description": "롤러에 손이 말려 들어감",
                    },
                }
            )
        )
    )

    assert result.label == "끼임"
    assert result.model == "Qwen/Qwen3.5-9B"
