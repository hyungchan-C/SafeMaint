from __future__ import annotations

import asyncio
import json
import re

import httpx
from openai import OpenAIError

from app.core.config import settings
from app.schemas.chat import (
    AccidentClassification,
    ChatRequest,
    ChatResponse,
    QueryAnalysis,
    RetrievalAccessScope,
)
from app.services.accident_classifier import (
    AccidentClassifierClient,
    AccidentClassifierError,
)
from app.services.ai import AIConfigurationError, AIService
from app.services.qwen import QwenClient
from app.services.safety_guidance import format_safety_answer


RAG_UNAVAILABLE_WARNING = (
    "문서 검색 서비스에 연결하지 못해 공통 안전수칙만 표시합니다. "
    "근거 문서가 없으므로 작업 승인 판단에 사용하지 마세요."
)
ANALYZER_FALLBACK_WARNING = (
    "상황 분석 모델을 사용할 수 없어 입력값 기반 검색어로 안전하게 대체했습니다."
)
LLM_FALLBACK_WARNING = (
    "외부 LLM 답변 생성에 실패해 검색 서비스의 근거 기반 기본 안내를 표시합니다."
)
COMPANY_LLM_WARNING = (
    "회사 문서 내용은 외부 LLM으로 전송하지 않았습니다."
)
LOCAL_VISION_LLM_WARNING = (
    "로컬 이미지 분석 내용은 외부 LLM으로 전송하지 않았습니다."
)
QWEN_UNAVAILABLE_WARNING = (
    "Qwen answer generation is unavailable; using the retrieved evidence template."
)
QWEN_COMPANY_CONTEXT_WARNING = (
    "Qwen company-context sharing is disabled; using the retrieved evidence template."
)
CLASSIFIER_FALLBACK_WARNING = (
    "팀 Qwen 사고유형 분류기를 사용할 수 없어 기존 검색 분석으로 대체했습니다."
)


class ChatService:
    def __init__(
        self,
        service_url: str | None = None,
        timeout_seconds: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        ai_service: AIService | None = None,
        openai_enabled: bool | None = None,
        qwen_client: QwenClient | None = None,
        qwen_enabled: bool | None = None,
        qwen_allow_company_context: bool | None = None,
        classifier_client: AccidentClassifierClient | None = None,
        classifier_enabled: bool | None = None,
    ) -> None:
        self.service_url = (service_url or "").rstrip("/")
        self.timeout_seconds = timeout_seconds or settings.rag_request_timeout_seconds
        self.transport = transport
        self.ai_service = ai_service or AIService()
        self.openai_enabled = (
            (settings.allow_external_llm or settings.llm_is_local)
            and bool(settings.openai_api_key or settings.llm_base_url)
            if openai_enabled is None
            else openai_enabled
        )
        self.qwen_enabled = (
            settings.qwen_enabled and bool(settings.qwen_service_url)
            if qwen_enabled is None
            else qwen_enabled
        )
        self.qwen_client = (
            qwen_client
            if qwen_client is not None
            else (QwenClient() if self.qwen_enabled else None)
        )
        self.qwen_allow_company_context = (
            settings.qwen_allow_company_context
            if qwen_allow_company_context is None
            else qwen_allow_company_context
        )
        self.classifier_client = classifier_client or AccidentClassifierClient(
            settings.qwen_classifier_url
        )
        self.classifier_enabled = (
            settings.qwen_classifier_enabled
            and bool(settings.qwen_classifier_url)
            if classifier_enabled is None
            else classifier_enabled
        )

    async def answer(
        self,
        request: ChatRequest,
        access_scope: RetrievalAccessScope | None = None,
    ) -> ChatResponse:
        use_qwen = self.qwen_enabled and self.qwen_client is not None
        classification, classifier_fell_back = await self._classify(request)
        analyzed_request, analyzer_fell_back = await self._analyze(
            request,
            allow_external=(
                not use_qwen
                and (
                    settings.llm_is_local
                    or not bool(access_scope and access_scope.allow_company)
                )
            ),
        )
        if classification is not None:
            analyzed_request = self._apply_classification(
                analyzed_request, classification
            )
        elif use_qwen:
            qwen_analysis = await self.qwen_client.classify(analyzed_request)
            if qwen_analysis is not None:
                analyzed_request = analyzed_request.model_copy(
                    update={
                        "analysis": self._merge_qwen_analysis(
                            analyzed_request.analysis, qwen_analysis
                        )
                    }
                )

        # Image-identification questions must use the latest local Vision result.
        # Do this before RAG so an unrelated selected manual cannot replace the
        # current photo with stale document evidence (for example, a bearing manual).
        visual_answer = self._visual_answer(analyzed_request)
        if visual_answer:
            return ChatResponse(
                answer=visual_answer,
                sources=[],
                retrieval_mode="safety-fallback",
                generation_mode="template",
                warning=(
                    "사진에서 직접 관찰한 일반 형상과 로컬 분석 결과이며, "
                    "정확한 제품·모델·규격을 확정한 결과가 아닙니다."
                ),
                accident_classification=classification,
            )

        retrieval_response = await self._retrieve(analyzed_request, access_scope)
        if classification is not None:
            retrieval_response = retrieval_response.model_copy(
                update={"accident_classification": classification}
            )
        if classifier_fell_back:
            retrieval_response = retrieval_response.model_copy(
                update={
                    "warning": self._append_warning(
                        retrieval_response.warning, CLASSIFIER_FALLBACK_WARNING
                    )
                }
            )
        if analyzer_fell_back and self.openai_enabled and not use_qwen:
            retrieval_response = retrieval_response.model_copy(
                update={
                    "warning": self._append_warning(
                        retrieval_response.warning, ANALYZER_FALLBACK_WARNING
                    )
                }
            )

        if use_qwen:
            return await self._answer_with_qwen(analyzed_request, retrieval_response)

        if not retrieval_response.sources or not self.openai_enabled:
            return retrieval_response

        # visual_summary is produced locally but is still supplied by the client.
        # Never forward OCR, labels, or image-derived text to an external provider.
        if analyzed_request.context.visual_summary and not settings.llm_is_local:
            return retrieval_response.model_copy(
                update={
                    "warning": self._append_warning(
                        retrieval_response.warning, LOCAL_VISION_LLM_WARNING
                    )
                }
            )

        # This is intentionally unconditional: no company evidence is sent to
        # an external provider, even if a legacy environment flag says otherwise.
        if not settings.llm_is_local and any(
            source.document_scope == "company"
            for source in retrieval_response.sources
        ):
            return retrieval_response.model_copy(
                update={
                    "warning": self._append_warning(
                        retrieval_response.warning, COMPANY_LLM_WARNING
                    )
                }
            )

        try:
            answer = await asyncio.to_thread(
                self.ai_service.answer,
                request.question,
                self._build_grounded_context(analyzed_request, retrieval_response),
            )
        except (AIConfigurationError, OpenAIError, RuntimeError, ValueError):
            return retrieval_response.model_copy(
                update={
                    "warning": self._append_warning(
                        retrieval_response.warning, LLM_FALLBACK_WARNING
                    )
                }
            )

        return retrieval_response.model_copy(
            update={
                "answer": answer,
                "generation_mode": "openai",
                "model": settings.llm_answer_model,
            }
        )

    async def _answer_with_qwen(
        self,
        request: ChatRequest,
        retrieval_response: ChatResponse,
    ) -> ChatResponse:
        if not retrieval_response.sources or self.qwen_client is None:
            return retrieval_response
        if (
            not self.qwen_allow_company_context
            and any(
                source.document_scope == "company"
                for source in retrieval_response.sources
            )
        ):
            return retrieval_response.model_copy(
                update={
                    "warning": self._append_warning(
                        retrieval_response.warning, QWEN_COMPANY_CONTEXT_WARNING
                    )
                }
            )
        qwen_answer = await self.qwen_client.answer(request, retrieval_response)
        if qwen_answer is None:
            return retrieval_response.model_copy(
                update={
                    "warning": self._append_warning(
                        retrieval_response.warning, QWEN_UNAVAILABLE_WARNING
                    )
                }
            )
        return retrieval_response.model_copy(
            update={
                "answer": qwen_answer.answer,
                "generation_mode": "qwen",
                "model": qwen_answer.model or "qwen",
            }
        )

    async def _classify(
        self,
        request: ChatRequest,
    ) -> tuple[AccidentClassification | None, bool]:
        if not self.classifier_enabled:
            return None, False
        try:
            return await self.classifier_client.classify(request), False
        except AccidentClassifierError:
            return None, True

    @staticmethod
    def _apply_classification(
        request: ChatRequest,
        classification: AccidentClassification,
    ) -> ChatRequest:
        analysis = request.analysis or QueryAnalysis()
        keywords = list(
            dict.fromkeys([classification.label, *analysis.search_keywords])
        )[:30]
        return request.model_copy(
            update={
                "analysis": analysis.model_copy(
                    update={
                        "occurrence_type": classification.label,
                        "search_keywords": keywords,
                    }
                )
            }
        )

    async def _analyze(
        self,
        request: ChatRequest,
        *,
        allow_external: bool = True,
    ) -> tuple[ChatRequest, bool]:
        if request.analysis is not None:
            return request, False
        fallback = self._fallback_analysis(request)
        if (
            not allow_external
            or not self.openai_enabled
            or not hasattr(self.ai_service, "analyze")
        ):
            return request.model_copy(update={"analysis": fallback}), False
        try:
            analysis = await asyncio.to_thread(
                self.ai_service.analyze,
                request.question,
                self._analysis_context(request),
            )
            if not isinstance(analysis, QueryAnalysis):
                analysis = QueryAnalysis.model_validate(analysis)
            return request.model_copy(update={"analysis": analysis}), False
        except (AIConfigurationError, OpenAIError, RuntimeError, ValueError):
            return request.model_copy(update={"analysis": fallback}), True

    async def _retrieve(
        self,
        request: ChatRequest,
        access_scope: RetrievalAccessScope | None = None,
    ) -> ChatResponse:
        if not self.service_url:
            return self._fallback(request)
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                request_body = request.model_dump(mode="json")
                request_body["access_scope"] = (
                    access_scope or RetrievalAccessScope()
                ).model_dump(mode="json")
                response = await client.post(
                    f"{self.service_url}/v1/chat", json=request_body
                )
                response.raise_for_status()
                return ChatResponse.model_validate(response.json())
        except (httpx.HTTPError, ValueError):
            return self._fallback(request)

    @staticmethod
    def _analysis_context(request: ChatRequest) -> str:
        return json.dumps(
            request.context.model_dump(
                mode="json",
                exclude={
                    "registered_manuals",
                    "selected_document_ids",
                    "selected_document_version_ids",
                    "visual_summary",
                    "visual_categories",
                    "visual_features",
                },
            ),
            ensure_ascii=False,
        )

    @staticmethod
    def _fallback_analysis(request: ChatRequest) -> QueryAnalysis:
        context = request.context
        raw_keywords = " ".join(
            value
            for value in (
                context.equipment_name,
                context.manufacturer,
                context.model_number,
                context.component_name,
                context.task_type,
                context.energy_source,
                context.task_description,
                request.question,
            )
            if value
        )
        keywords = list(
            dict.fromkeys(
                token.casefold()
                for token in re.findall(r"[0-9A-Za-z가-힣_-]+", raw_keywords)
                if len(token) >= 2
            )
        )[:30]
        return QueryAnalysis(
            work_type=context.task_type,
            equipment=[context.equipment_name] if context.equipment_name else [],
            component=[context.component_name] if context.component_name else [],
            energy_sources=[context.energy_source] if context.energy_source else [],
            search_keywords=keywords,
        )

    @staticmethod
    def _merge_qwen_analysis(
        fallback: QueryAnalysis | None,
        qwen_analysis: QueryAnalysis,
    ) -> QueryAnalysis:
        base = fallback or QueryAnalysis()
        occurrence_type = (qwen_analysis.occurrence_type or "").strip()
        if not occurrence_type:
            return base
        return base.model_copy(update={"occurrence_type": occurrence_type})

    @staticmethod
    def _build_grounded_context(
        request: ChatRequest,
        response: ChatResponse,
    ) -> str:
        context = request.context
        fields = (
            ("사업장", context.site_name),
            ("설비", context.equipment_name),
            ("제조사", context.manufacturer),
            ("모델·부품번호", context.model_number),
            ("부품", context.component_name),
            ("작업 종류", context.task_type),
            ("에너지원", context.energy_source),
            ("작업 설명", context.task_description),
            (
                "로컬 이미지 분석(외형 후보이며 모델·규격 확정 근거가 아님)",
                context.visual_summary,
            ),
            (
                "사진 지시어 해석",
                "'이건 뭐야', '어디에 쓰여' 같은 짧은 질문은 최근 첨부 사진을 가리킴"
                if context.visual_summary
                else None,
            ),
        )
        lines = [f"{label}: {value}" for label, value in fields if value]
        if request.analysis and request.analysis.occurrence_type:
            lines.append(
                "팀 Qwen LoRA 사고유형 예측(실험용): "
                f"{request.analysis.occurrence_type}"
            )
        lines.append("\n검증된 검색 근거:")
        for index, source in enumerate(response.sources, start=1):
            location = source.section or "section unknown"
            if source.page_start:
                location = f"{location}, page {source.page_start}"
                if source.page_end and source.page_end != source.page_start:
                    location += f"-{source.page_end}"
            lines.extend(
                (
                    f"[{index}] title: {source.title}",
                    f"[{index}] type/version: {source.source_type} / {source.document_version or 'legacy'}",
                    f"[{index}] location: {location}",
                    f"[{index}] evidence: {source.excerpt}",
                )
            )
        return "\n".join(lines)

    @staticmethod
    def _append_warning(current: str | None, additional: str) -> str:
        return f"{current} {additional}" if current else additional

    @staticmethod
    def _visual_answer(request: ChatRequest) -> str | None:
        question = request.question.casefold()
        if not request.context.visual_categories or not any(
            token in question
            for token in ("이건", "이것", "뭐", "무엇", "사진", "어디에 쓰", "용도", "부품")
        ):
            return None
        categories = list(
            dict.fromkeys(
                value.strip()
                for value in request.context.visual_categories
                if value.strip()
            )
        )[:3]
        if not categories:
            return None
        primary = categories[0]
        features = list(
            dict.fromkeys(
                value.strip()
                for value in request.context.visual_features
                if value.strip()
            )
        )[:3]
        lines = [f"사진에서 보이는 일반 형상은 **{primary}**로 추정됩니다."]
        if features:
            lines.append("관찰 근거: " + ", ".join(features) + ".")
        if "어디에 쓰" in question or "용도" in question:
            usage = "부품을 서로 체결하는 용도"
            if "너트" in primary:
                usage = "볼트와 함께 부품을 조여 고정하는 용도"
            elif "와셔" in primary:
                usage = "체결 하중을 분산하거나 표면 손상을 줄이는 용도"
            elif "베어링" in primary:
                usage = "회전축을 지지하고 마찰을 줄이는 용도"
            elif "센서" in primary:
                usage = "상태나 물리량을 감지하는 용도"
            elif "카메라" in primary:
                usage = "대상을 촬영하거나 검사하는 용도"
            elif "usb" in primary.casefold() or "메모리" in primary:
                usage = "파일과 데이터를 저장하고 USB 포트가 있는 장치 사이에서 옮기는 용도"
            lines.append(f"일반적으로는 {usage}에 사용됩니다.")
        lines.append(
            "다만 현재 결과는 사진 형상에 대한 추정이므로, 정확한 제품명·규격·적용 위치는 각인과 카탈로그 표의 일치 여부를 추가로 확인해야 합니다."
        )
        return "\n\n".join(lines)

    @staticmethod
    def _fallback(request: ChatRequest) -> ChatResponse:
        context_text = " ".join(
            value
            for value in (
                request.context.equipment_name,
                request.context.component_name,
                request.context.task_type,
                request.context.energy_source,
                request.context.task_description,
                request.question,
            )
            if value
        )
        return ChatResponse(
            answer=format_safety_answer(context_text),
            sources=[],
            retrieval_mode="safety-fallback",
            warning=RAG_UNAVAILABLE_WARNING,
        )


chat_service = ChatService(service_url=settings.rag_service_url)


def get_chat_service() -> ChatService:
    return chat_service
