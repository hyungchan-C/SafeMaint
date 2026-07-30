from __future__ import annotations

import json
import re
from typing import Protocol, TypeVar

from openai import OpenAI
from pydantic import BaseModel, ValidationError

from app.core.config import settings
from app.schemas.chat import QueryAnalysis


# 기존 영문 프롬프트:
# You are a query analyzer for industrial safety retrieval. Return only JSON matching
# the requested schema. Extract search terms without deciding risk, approving work,
# or inventing equipment facts. Classify the user's purpose into the defined intents.
ANALYZER_INSTRUCTIONS = """당신은 산업 안전 검색을 위한 질문 분석기입니다.
요청된 스키마와 일치하는 JSON만 반환하세요. 사용자의 상황에서 검색어를 추출하되,
위험 수준을 결정하거나 작업을 승인하거나 설비 정보를 지어내지 마세요. 정보가 없으면
빈 목록 또는 null을 사용하세요. 사용자의 목적을 document_qa(PDF·문서 내용 질문),
maintenance_guide(설치·점검·청소·수리·교체 방법 질문), component_info(정의·목적·역할·
용도 질문), clarification_required(목적이 모호함) 중 하나로 분류하세요. 부품 명사만
있다고 component_info로 분류하지 마세요. no_evidence는 질문 의도가 아닙니다."""

# 기존 영문 프롬프트:
# You are SafeMaint AI, an industrial safety assistant. Answer in Korean using only
# numbered evidence. Cite every factual claim, do not invent facts, distinguish local
# visual observations from verified specifications, and never approve work.
ANSWER_INSTRUCTIONS = """당신은 산업 안전 지원 도우미 SafeMaint AI입니다.
애플리케이션이 제공한 번호가 붙은 근거만 사용하여 한국어로 답하세요. 매뉴얼, 사고사례,
법령, 절차 또는 수치에 관한 모든 사실 주장에는 [1]과 같은 일치하는 인용을 붙이세요.
법령, 매뉴얼 단계, 기준값, 토크 또는 측정값을 지어내지 마세요. 근거가 부족하면 무엇이
부족한지 정확히 밝히세요.
제공된 답변 유형을 따르세요. document_qa는 선택한 문서를 요약하며 위험 판단이나 TBM을
추가하지 않습니다. component_info는 정의·역할·용도와 근거가 있는 주의사항을 설명하며
유지보수 절차나 TBM을 추가하지 않습니다. maintenance_guide는 승인된 매뉴얼 근거가
있을 때만 절차를 포함할 수 있으며 작업이 승인되었거나 안전하다고 표현하면 안 됩니다.
로컬 이미지 분석이 제공되면 관찰된 외형 및 카탈로그 유사 후보와 검증된 모델·규격 사실을
구분하세요. 외형만으로 각인 문자, 모델 번호, 치수, 재질 또는 등급을 추론하지 마세요.
검증된 OCR이나 인용된 승인 문서에 없는 값은 확인할 수 없다고 말하세요.
사용자가 "이건 뭐야?" 또는 "어디에 쓰여?"처럼 짧은 지시형 질문을 하면 최근 제공된
로컬 이미지 분석을 가리키는 것으로 해석하세요. 사용자가 후보 이미지를 명시적으로
요청하지 않아도 관찰 가능한 범주, 추정 가능한 일반 용도와 카탈로그 후보를 설명하세요.
이 답변은 작업 승인이 아닙니다. 현장 조건과 제조사 지침을 확인하고 안전관리자의 최종
확인을 받도록 안내하세요."""

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
        local_options = (
            {"reasoning_effort": settings.llm_reasoning_effort}
            if settings.llm_is_local
            else {}
        )
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
            **local_options,
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
        local_options = (
            {"reasoning_effort": settings.llm_reasoning_effort}
            if settings.llm_is_local
            else {}
        )
        completion = self.client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": instructions},
                {"role": "user", "content": user_input},
            ],
            max_tokens=max_output_tokens,
            temperature=0,
            **local_options,
        )
        return (completion.choices[0].message.content or "").strip()


class AIService:
    def __init__(self, provider: ModelProvider | None = None) -> None:
        self.provider = provider

    def _provider(self) -> ModelProvider:
        if self.provider is not None:
            return self.provider
        if not settings.allow_external_llm and not settings.llm_is_local:
            raise AIConfigurationError("External LLM use is disabled by policy.")
        if settings.llm_is_local and not settings.local_llm_url_is_trusted():
            raise AIConfigurationError(
                "Local LLM scope requires LLM_BASE_URL to use a trusted local host."
            )
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
            # 기존 영문 입력 구분명: Context, Question.
            user_input=f"맥락:\n{context}\n\n질문:\n{question}",
            schema=QueryAnalysis,
            max_output_tokens=settings.llm_analyzer_max_output_tokens,
        )

    def answer(self, question: str, context: str | None = None) -> str:
        if not context:
            raise RuntimeError("Grounded evidence context is required.")
        answer = self._provider().generate_text(
            model=settings.llm_answer_model,
            instructions=ANSWER_INSTRUCTIONS,
            # 기존 영문 입력 구분명: Evidence and work context, Question.
            user_input=f"근거 및 작업 맥락:\n{context}\n\n질문:\n{question}",
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
