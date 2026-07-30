from __future__ import annotations

import httpx

from app.core.config import settings
from app.schemas.chat import AccidentClassification, ChatRequest


class AccidentClassifierError(RuntimeError):
    pass


class AccidentClassifierClient:
    def __init__(
        self,
        service_url: str | None = None,
        timeout_seconds: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.service_url = (service_url or "").rstrip("/")
        self.timeout_seconds = (
            timeout_seconds or settings.qwen_classifier_timeout_seconds
        )
        self.transport = transport

    async def classify(self, request: ChatRequest) -> AccidentClassification:
        if not self.service_url:
            raise AccidentClassifierError("Qwen classifier URL is not configured.")
        title = " ".join(
            value
            for value in (
                request.context.equipment_name,
                request.context.component_name,
                request.context.task_type,
            )
            if value
        ) or request.question
        accident_text = "\n".join(
            value
            for value in (
                request.context.task_description,
                request.question,
            )
            if value
        )
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                response = await client.post(
                    f"{self.service_url}/v1/classify",
                    json={"title": title, "text": accident_text},
                )
                response.raise_for_status()
                return AccidentClassification.model_validate(response.json())
        except (httpx.HTTPError, ValueError) as exc:
            raise AccidentClassifierError(
                "Team Qwen accident classifier request failed."
            ) from exc
