from __future__ import annotations

import json
import re
from typing import Protocol, TypeVar

from openai import OpenAI
from pydantic import BaseModel, ValidationError

from app.core.config import settings
from app.schemas.chat import QueryAnalysis


ANALYZER_INSTRUCTIONS = """You are a query analyzer for industrial safety retrieval.
Return only JSON matching the requested schema. Extract search terms from the user's
situation; do not decide risk level, approve work, or invent equipment facts. Use
empty lists or null when information is absent."""

ANSWER_INSTRUCTIONS = """You are SafeMaint AI, an industrial safety assistant.
Answer in Korean using only the numbered evidence supplied by the application.
Every factual manual, incident, legal, procedural, or numeric claim must include a
matching citation such as [1]. Never invent a law, manual step, threshold, torque,
or measurement. If the evidence is insufficient, say exactly what is missing.
This is not work approval; require site conditions, manufacturer instructions, and
the safety manager's final confirmation."""

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class AIConfigurationError(RuntimeError):
    pass


class ModelProvider(Protocol):
    def generate_json(
        self,
        *,
        model: str,
        instructions: str,
        user_input: str,
        schema: type[SchemaT],
        max_output_tokens: int,
    ) -> SchemaT: ...

    def generate_text(
        self,
        *,
        model: str,
        instructions: str,
        user_input: str,
        max_output_tokens: int,
    ) -> str: ...


class OpenAICompatibleProvider:
    """Provider used by GPT-4o-mini now and an OpenAI-compatible Qwen server later."""

    def __init__(self) -> None:
        kwargs: dict[str, object] = {
            "api_key": settings.openai_api_key or "local-model",
            "timeout": settings.openai_timeout_seconds,
        }
        if settings.llm_base_url:
            kwargs["base_url"] = settings.llm_base_url
        self.client = OpenAI(**kwargs)

    def generate_json(
        self,
        *,
        model: str,
        instructions: str,
        user_input: str,
        schema: type[SchemaT],
        max_output_tokens: int,
    ) -> SchemaT:
        json_schema = schema.model_json_schema()
        json_schema["additionalProperties"] = False
        json_schema["required"] = list(json_schema.get("properties", {}))
        completion = self.client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": instructions},
                {"role": "user", "content": user_input},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "strict": True,
                    "schema": json_schema,
                },
            },
            max_tokens=max_output_tokens,
        )
        content = completion.choices[0].message.content or ""
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
        try:
            return schema.model_validate(json.loads(content))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise RuntimeError("Analyzer returned invalid structured JSON.") from exc

    def generate_text(
        self,
        *,
        model: str,
        instructions: str,
        user_input: str,
        max_output_tokens: int,
    ) -> str:
        completion = self.client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": instructions},
                {"role": "user", "content": user_input},
            ],
            max_tokens=max_output_tokens,
            temperature=0,
        )
        return (completion.choices[0].message.content or "").strip()


class AIService:
    def __init__(self, provider: ModelProvider | None = None) -> None:
        self.provider = provider

    def _provider(self) -> ModelProvider:
        if self.provider is not None:
            return self.provider
        if not settings.allow_external_llm:
            raise AIConfigurationError("External LLM use is disabled by policy.")
        if not settings.openai_api_key and not settings.llm_base_url:
            raise AIConfigurationError(
                "OPENAI_API_KEY or an OpenAI-compatible LLM_BASE_URL is required."
            )
        self.provider = OpenAICompatibleProvider()
        return self.provider

    def analyze(self, question: str, context: str) -> QueryAnalysis:
        return self._provider().generate_json(
            model=settings.llm_analyzer_model,
            instructions=ANALYZER_INSTRUCTIONS,
            user_input=f"Context:\n{context}\n\nQuestion:\n{question}",
            schema=QueryAnalysis,
            max_output_tokens=settings.llm_analyzer_max_output_tokens,
        )

    def answer(self, question: str, context: str | None = None) -> str:
        if not context:
            raise RuntimeError("Grounded evidence context is required.")
        answer = self._provider().generate_text(
            model=settings.llm_answer_model,
            instructions=ANSWER_INSTRUCTIONS,
            user_input=f"Evidence and work context:\n{context}\n\nQuestion:\n{question}",
            max_output_tokens=settings.openai_max_output_tokens,
        )
        if not answer:
            raise RuntimeError("The model returned an empty answer.")
        source_numbers = {
            int(number)
            for number in re.findall(r"^\[(\d+)\]", context, flags=re.MULTILINE)
        }
        cited_numbers = {
            int(number) for number in re.findall(r"\[(\d+)\]", answer)
        }
        if source_numbers and (
            not cited_numbers or not cited_numbers.issubset(source_numbers)
        ):
            raise RuntimeError("The grounded answer contains missing or invalid citations.")
        return answer
