from __future__ import annotations

import asyncio
import json
import re
import shutil
import tempfile
import time
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from qwen_service.config import Settings
from qwen_service.schemas import (
    AnswerRequest,
    AnswerResponse,
    ChatSource,
    ClassifyRequest,
    ClassifyResponse,
    DocumentProfile,
    DocumentProfileRequest,
    DocumentProfileResponse,
    IntentClassifyResponse,
    QueryAnalysis,
)


MANUAL_SOURCE_TYPES = frozenset(
    {"equipment_manual", "component_manual"}
)
PUBLIC_REFERENCE_SOURCE_TYPES = frozenset(
    {"public_law", "public_guide", "public_incident", "public_media"}
)
COMPANY_REFERENCE_SOURCE_TYPES = frozenset({"company_policy"})
MAINTENANCE_REFERENCE_SOURCE_TYPES = (
    PUBLIC_REFERENCE_SOURCE_TYPES | COMPANY_REFERENCE_SOURCE_TYPES
)
PRECAUTION_REFERENCE_SOURCE_TYPES = (
    (PUBLIC_REFERENCE_SOURCE_TYPES - {"public_incident"}) | COMPANY_REFERENCE_SOURCE_TYPES
)
CHECKLIST_SOURCE_TYPES = MANUAL_SOURCE_TYPES | frozenset(
    {"public_law", "public_guide", "company_policy"}
)
GENERIC_FALLBACK_ANSWERS = frozenset(
    {
        "검색된 근거를 기준으로 작업 전 확인할 핵심 사항을 요약했습니다.",
        "검색된 문서 근거를 기준으로 유지보수 시 확인할 사항을 정리했습니다. 현장 안전관리자의 최종 확인 전에는 작업을 시작하지 마세요.",
        "검색된 문서 근거를 기준으로 확인 가능한 내용을 요약했습니다.",
        "검색된 문서 근거를 기준으로 질문과 관련된 내용을 요약했습니다.",
        "검색된 문서 근거에서 확인되는 부품 정보를 정리했습니다.",
        "검색된 문서 근거를 기준으로 부품 정보를 요약했습니다.",
    }
)
BAD_CARD_PREFIXES = ("은 ", "는 ", "이 ", "가 ", "을 ", "를 ", "에 ", "에서 ", "후에")
BAD_CARD_SUFFIXES = ("...", "예.", "후에", "직", "및", "또는")
MAX_COMPACT_CARD_CHARS = 40


class QwenAnswerGenerationError(RuntimeError):
    """Raised when Qwen did not produce a usable compact answer."""


class QwenEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = asyncio.Lock()
        self._tokenizer: Any = None
        self._model: Any = None
        self._answer_tokenizer: Any = None
        self._answer_model: Any = None
        self._adapter_loaded = False
        self._prepared_adapter_path: Path | None = None

    async def classify(self, request: ClassifyRequest) -> ClassifyResponse:
        async with self._lock:
            return await asyncio.to_thread(self._classify_sync, request)

    async def intent(self, request: ClassifyRequest) -> IntentClassifyResponse:
        async with self._lock:
            return await asyncio.to_thread(self._intent_sync, request)

    async def answer(self, request: AnswerRequest) -> AnswerResponse:
        async with self._lock:
            return await asyncio.to_thread(self._answer_sync, request)

    async def document_profile(
        self,
        request: DocumentProfileRequest,
    ) -> DocumentProfileResponse:
        async with self._lock:
            return await asyncio.to_thread(self._document_profile_sync, request)

    def _classify_sync(self, request: ClassifyRequest) -> ClassifyResponse:
        labels = ", ".join(self.settings.occurrence_labels)
        # 기존 영문 프롬프트:
        # You are the SafeMaint accident-type classifier. Return JSON only.
        # Classify only accident/risk occurrence labels. Do not classify the user's question intent.
        system_prompt = (
            "당신은 SafeMaint의 사고유형 분류기입니다. JSON만 반환하세요. "
            "사고·위험 발생 유형 라벨만 분류하고 사용자의 질문 의도는 분류하지 마세요."
        )
        # 기존 영문 프롬프트:
        # Choose one occurrence_type and up to three explicit_risk_factors from this label list.
        # Return JSON exactly like the example below.
        user_prompt = (
            "다음 라벨 목록에서 occurrence_type 하나와 explicit_risk_factors를 "
            "최대 세 개까지 선택하세요:\n"
            f"{labels}\n\n"
            "필드명은 변경하지 말고 다음 형식의 JSON만 반환하세요: "
            '{"occurrence_type":"label","confidence":0.0,'
            '"explicit_risk_factors":["label"]}.\n\n'
            f"작업 맥락:\n{self._context_text(request.context)}\n\n"
            f"질문:\n{request.question}"
        )
        text = self._generate(
            system_prompt,
            user_prompt,
            max_new_tokens=self.settings.classify_max_new_tokens,
            disable_adapter=False,
        )
        occurrence_type, confidence, risk_factors = (
            self._parse_occurrence_classification(text)
        )
        analysis = QueryAnalysis(
            occurrence_type=occurrence_type,
            explicit_risk_factors=risk_factors,
            search_keywords=risk_factors,
        )
        return ClassifyResponse(
            occurrence_type=occurrence_type,
            confidence=confidence,
            question_intent=None,
            intent_confidence=None,
            clarification_question=None,
            analysis=analysis,
            model=self.settings.base_model,
        )

    def _intent_sync(self, request: ClassifyRequest) -> IntentClassifyResponse:
        # 기존 영문 프롬프트:
        # You are the SafeMaint question-intent classifier. Return JSON only.
        # Classify the user's purpose. Do not classify accident occurrence type.
        system_prompt = (
            "당신은 SafeMaint의 질문 의도 분류기입니다. JSON만 반환하세요. "
            "사용자의 질문 목적만 분류하고 사고 발생 유형은 분류하지 마세요."
        )
        # 기존 영문 프롬프트:
        # Choose question_intent by the user's purpose and return JSON exactly like the example.
        user_prompt = (
            "사용자의 목적에 따라 question_intent를 선택하세요:\n"
            "- document_qa: 선택한 PDF·문서의 내용, 파일 정보 또는 문서 요약을 요청함\n"
            "- maintenance_guide: 설치·점검·청소·수리·교체·정지·격리 또는 안전한 작업 방법을 요청함\n"
            "- component_info: 부품의 정의·역할·용도·사용 위치 또는 부품 관련 주의점을 요청함\n"
            "- clarification_required: 질문 목적이 모호함\n\n"
            "필드명과 열거값은 변경하지 말고 다음 형식의 JSON만 반환하세요: "
            '{"question_intent":"component_info","intent_confidence":0.0,'
            '"clarification_question":null}.\n\n'
            f"작업 맥락:\n{self._context_text(request.context)}\n\n"
            f"질문:\n{request.question}"
        )
        text = self._generate(
            system_prompt,
            user_prompt,
            max_new_tokens=self.settings.classify_max_new_tokens,
            disable_adapter=True,
        )
        question_intent, intent_confidence, clarification_question = (
            self._parse_intent_classification(text)
        )
        analysis = QueryAnalysis(
            question_intent=question_intent,
            intent_confidence=intent_confidence,
            clarification_question=clarification_question,
        )
        return IntentClassifyResponse(
            question_intent=question_intent,
            intent_confidence=intent_confidence,
            clarification_question=clarification_question,
            analysis=analysis,
            model=self.settings.base_model,
        )

    def _answer_sync(self, request: AnswerRequest) -> AnswerResponse:
        evidence = self._evidence_text(request)
        # 기존 영문 프롬프트:
        # You are SafeMaint AI. Return exactly one valid JSON object. Do not output
        # markdown, tables, hidden reasoning, or a full structured_answer object.
        # Use only the provided evidence and candidate cards. Do not invent facts
        # or state that work is approved or safe to proceed.
        system_prompt = (
            "당신은 SafeMaint AI입니다. 유효한 JSON 객체 하나만 반환하세요. "
            "마크다운, 표, 숨겨진 추론 과정 또는 전체 structured_answer 객체를 출력하지 마세요. "
            "제공된 근거와 후보 카드만 사용하세요. "
            "최상위 answer는 사용자 질문에 직접 답하는 짧고 공손한 한국어 문장이어야 합니다. "
            "모든 카드·목록 값은 긴 설명이 아닌 짧은 한국어 체크리스트 문구로 작성하세요. "
            "유지보수 답변의 checklist_items는 최종 pre_checks에서만 가져오세요. "
            "파일명, 페이지, 법령, 사고사례, 절차 또는 출처 ID를 지어내지 마세요. "
            "작업이 승인되었거나 진행해도 안전하다고 말하지 마세요."
        )
        # 기존 영문 입력 구분명: Answer type, Work context, Analysis,
        # Candidate cards from backend, Evidence, Question.
        user_prompt = (
            f"답변 유형: {request.answer_type}\n"
            f"{self._compact_answer_format_for_type(request)}\n\n"
            f"작업 맥락:\n{self._context_text(request.context)}\n\n"
            f"분석 결과:\n{self._analysis_text(request.analysis)}\n\n"
            f"백엔드 후보 카드:\n{self._candidate_text(request)}\n\n"
            f"근거:\n{evidence}\n\n"
            f"질문:\n{request.question}"
        )
        generated = self._generate(
            system_prompt,
            user_prompt,
            max_new_tokens=self.settings.max_new_tokens,
            disable_adapter=True,
        )
        response, validation_error = self._response_from_compact_generation(
            request,
            generated,
        )
        if response is not None:
            return response

        if validation_error:
            print(
                json.dumps(
                    {
                        "event": "qwen_compact_answer_validation_failed",
                        "answer_type": request.answer_type,
                        "reason": validation_error[:300],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

        return self._fallback_answer(request, generated)

    def _document_profile_sync(
        self,
        request: DocumentProfileRequest,
    ) -> DocumentProfileResponse:
        sample_text = self._profile_sample_text(request.sample_text)
        # 기존 영문 프롬프트:
        # You extract structured metadata from industrial PDF manuals. Return exactly
        # one valid JSON object. Do not output markdown or reasoning. Use only the
        # provided filename, form metadata, and sample text.
        system_prompt = (
            "당신은 산업용 PDF 매뉴얼에서 구조화된 메타데이터를 추출합니다. "
            "유효한 JSON 객체 하나만 반환하고 마크다운이나 추론 과정은 출력하지 마세요. "
            "제공된 파일명, 입력 양식 메타데이터와 샘플 텍스트만 사용하세요."
        )
        # 기존 영문 프롬프트:
        # Extract a document profile for RAG routing and answer cards.
        # Return this JSON schema only and keep every list item short and specific.
        user_prompt = (
            "RAG 검색 경로와 답변 카드에 사용할 문서 프로필을 추출하세요.\n"
            "키 이름은 변경하지 말고 다음 JSON 스키마만 반환하세요:\n"
            "{"
            '"product_names":["제품명 또는 제품군 이름"],'
            '"model_names":["모델명 또는 시리즈명"],'
            '"aliases":["사용자가 질문할 수 있는 짧은 별칭"],'
            '"equipment":["언급된 설비 또는 기계"],'
            '"components":["부품·센서·스위치·모듈·케이블·커버·제어기"],'
            '"supported_tasks":["확인된 설치·설정·배선·점검·유지보수 작업"],'
            '"safety_topics":["확인된 경고·위험·정지·안전 주제"],'
            '"summary_points":["짧은 한국어 요약"],'
            '"document_keywords":["검색 키워드"],'
            '"confidence":0.0,'
            '"extraction_notes":["불확실하거나 누락된 필드"]'
            "}\n"
            "규칙:\n"
            "- 모든 목록 항목은 짧고 구체적으로 작성하세요.\n"
            "- 제품, 문서, 매뉴얼, 장비, 기계 같은 일반 단어만 단독으로 넣지 마세요.\n"
            "- 제조사, 모델, 설비, 작업, 법령 또는 경고를 지어내지 마세요.\n"
            "- 작업·안전·요약 필드는 한국어로 작성하세요.\n\n"
            f"제목: {request.title}\n"
            f"원본 파일명: {request.original_filename or ''}\n"
            f"제조사: {request.manufacturer or ''}\n"
            f"입력된 제품 유형: {request.product_type or ''}\n"
            f"입력된 모델명: {request.model_name or ''}\n"
            f"문서 유형: {request.document_type or ''}\n\n"
            f"샘플 텍스트:\n{sample_text}"
        )
        generated = self._generate(
            system_prompt,
            user_prompt,
            max_new_tokens=min(self.settings.max_new_tokens, 768),
            disable_adapter=True,
        )
        parsed = self._extract_json(generated) or {}
        payload = self._normalize_document_profile_payload(parsed, request)
        if not self._document_profile_has_signal(payload):
            payload = self._fallback_document_profile(request)
        return DocumentProfileResponse(
            document_profile=DocumentProfile.model_validate(payload),
            model=self.settings.base_model,
        )

    @staticmethod
    def _profile_sample_text(value: str, *, limit: int = 18000) -> str:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if len(text) <= limit:
            return text
        head = text[: limit // 2].rstrip()
        tail = text[-limit // 2 :].lstrip()
        return f"{head}\n...\n{tail}"

    def _normalize_document_profile_payload(
        self,
        payload: dict[str, Any],
        request: DocumentProfileRequest,
    ) -> dict[str, Any]:
        fallback = self._fallback_document_profile(request)
        normalized: dict[str, Any] = {}
        for key, limit in (
            ("product_names", 12),
            ("model_names", 12),
            ("aliases", 20),
            ("equipment", 20),
            ("components", 30),
            ("supported_tasks", 20),
            ("safety_topics", 20),
            ("summary_points", 8),
            ("document_keywords", 30),
            ("extraction_notes", 8),
        ):
            values = self._profile_string_list(payload.get(key), limit=limit)
            if key in {
                "product_names",
                "model_names",
                "aliases",
                "document_keywords",
            }:
                values = self._merge_profile_values(
                    values,
                    fallback.get(key, []),
                    limit=limit,
                )
            normalized[key] = values
        try:
            confidence = float(payload.get("confidence"))
        except (TypeError, ValueError):
            confidence = None
        if confidence is not None:
            confidence = min(max(confidence, 0.0), 1.0)
        normalized["confidence"] = confidence
        return normalized

    @staticmethod
    def _profile_string_list(value: Any, *, limit: int) -> list[str]:
        raw_values = value if isinstance(value, list) else [value]
        normalized: list[str] = []
        for raw in raw_values:
            if raw is None or isinstance(raw, (dict, list, tuple, set)):
                continue
            text = " ".join(str(raw).split()).strip(" -•*[]()")
            if (
                not text
                or len(text) > 80
                or text in {"제품", "문서", "매뉴얼", "장비", "기계", "설비"}
            ):
                continue
            if text not in normalized:
                normalized.append(text)
            if len(normalized) >= limit:
                break
        return normalized

    @staticmethod
    def _merge_profile_values(
        primary: list[str],
        fallback: Any,
        *,
        limit: int,
    ) -> list[str]:
        values = list(primary)
        fallback_values = fallback if isinstance(fallback, list) else []
        for item in fallback_values:
            text = " ".join(str(item).split()).strip()
            if text and text not in values:
                values.append(text)
            if len(values) >= limit:
                break
        return values

    @staticmethod
    def _document_profile_has_signal(payload: dict[str, Any]) -> bool:
        return any(
            payload.get(key)
            for key in (
                "product_names",
                "model_names",
                "aliases",
                "components",
                "equipment",
                "supported_tasks",
                "safety_topics",
            )
        )

    def _fallback_document_profile(
        self,
        request: DocumentProfileRequest,
    ) -> dict[str, Any]:
        text = " ".join(
            value
            for value in (
                request.title,
                request.original_filename or "",
                request.manufacturer or "",
                request.product_type or "",
                request.model_name or "",
                request.sample_text[:8000],
            )
            if value
        )
        product_names = self._profile_string_list(
            [request.product_type, *self._profile_entity_candidates(text)],
            limit=12,
        )
        model_names = self._profile_string_list(
            [request.model_name, *self._profile_model_candidates(text)],
            limit=12,
        )
        aliases = self._profile_string_list(
            [*model_names, *product_names, *self._profile_filename_aliases(request)],
            limit=20,
        )
        components = [
            value
            for value in product_names
            if not value.endswith(("설비", "장비", "기계", "라인", "로봇", "프레스"))
        ][:30]
        equipment = [
            value
            for value in product_names
            if value.endswith(("설비", "장비", "기계", "라인", "로봇", "프레스"))
        ][:20]
        supported_tasks = self._profile_task_labels(text)
        safety_topics = self._profile_safety_labels(text)
        summary_points = self._profile_summary_points(text)
        keywords = self._profile_string_list(
            [*aliases, *components, *equipment, *supported_tasks, *safety_topics],
            limit=30,
        )
        notes = [] if aliases or components or equipment else ["문서 식별명을 확정하지 못했습니다."]
        return {
            "product_names": product_names,
            "model_names": model_names,
            "aliases": aliases,
            "equipment": equipment,
            "components": components,
            "supported_tasks": supported_tasks,
            "safety_topics": safety_topics,
            "summary_points": summary_points,
            "document_keywords": keywords,
            "confidence": 0.45 if aliases else 0.2,
            "extraction_notes": notes,
        }

    @staticmethod
    def _profile_entity_candidates(text: str) -> list[str]:
        suffixes = (
            "센서",
            "스위치",
            "장치",
            "모듈",
            "컨트롤러",
            "케이블",
            "커튼",
            "베어링",
            "모터",
            "펌프",
            "밸브",
            "실린더",
            "로봇",
            "컨베이어",
            "프레스",
            "브라켓",
            "커버",
            "기구",
            "부품",
            "컴포넌트",
            "릴레이",
            "차단기",
            "인버터",
            "드라이버",
            "설비",
            "장비",
            "기계",
        )
        suffix_pattern = "|".join(re.escape(suffix) for suffix in suffixes)
        candidates: list[str] = []
        for match in re.finditer(
            rf"([0-9A-Za-z가-힣□·/()+_-]+(?:\s+[0-9A-Za-z가-힣□·/()+_-]+){{0,4}}\s*(?:{suffix_pattern}))",
            text,
            flags=re.IGNORECASE,
        ):
            candidate = " ".join(match.group(1).split()).strip(" .,:;·-/[]()")
            if 2 <= len(candidate) <= 48 and candidate not in candidates:
                candidates.append(candidate)
            if len(candidates) >= 30:
                break
        return candidates

    @staticmethod
    def _profile_model_candidates(text: str) -> list[str]:
        candidates: list[str] = []
        normalized = text.replace("_", " ").replace("-", " ")
        for token in re.findall(r"(?<![A-Za-z0-9])([A-Za-z]{1,10}[A-Za-z0-9]{0,20})(?![A-Za-z0-9])", normalized):
            lowered = token.casefold()
            if lowered in {
                "pdf",
                "manual",
                "user",
                "guide",
                "catalog",
                "model",
                "series",
                "type",
            }:
                continue
            if token.isupper() or any(char.isdigit() for char in token):
                if token not in candidates:
                    candidates.append(token)
                if 2 <= len(token) <= 8:
                    series = f"{token} Series"
                    if series not in candidates:
                        candidates.append(series)
            if len(candidates) >= 20:
                break
        return candidates

    @staticmethod
    def _profile_filename_aliases(request: DocumentProfileRequest) -> list[str]:
        values: list[str] = []
        for raw in (request.original_filename, request.title):
            if not raw:
                continue
            stem = re.sub(r"\.pdf$", "", raw, flags=re.IGNORECASE)
            stem = stem.replace("_", " ").replace("-", " ")
            for token in stem.split():
                if 2 <= len(token) <= 24 and token.casefold() not in {"ko", "kr", "pdf", "manual"}:
                    values.append(token)
        return list(dict.fromkeys(values))[:12]

    @staticmethod
    def _profile_task_labels(text: str) -> list[str]:
        lowered = text.casefold()
        labels: list[str] = []
        for terms, label in (
            (("설치", "장착", "고정", "체결"), "설치·장착 조건 확인"),
            (("배선", "결선", "전원", "전압", "전류"), "정격·전원·배선 확인"),
            (("설정", "파라미터", "모드"), "설정값 확인"),
            (("점검", "검사", "시험", "확인"), "점검·시험 방법 확인"),
            (("청소", "오염", "이물"), "청소·오염 관리"),
            (("교체", "분리", "조립"), "교체·분리 작업 확인"),
        ):
            if any(term in lowered for term in terms):
                labels.append(label)
        return labels[:20]

    @staticmethod
    def _profile_safety_labels(text: str) -> list[str]:
        lowered = text.casefold()
        labels: list[str] = []
        for terms, label in (
            (("주의", "경고", "금지"), "주의·경고사항"),
            (("오동작", "고장", "손상", "파손"), "오동작·손상 방지"),
            (("정지", "차단", "비상정지"), "정지·차단 조건"),
            (("감전", "전원", "전압", "접지"), "전기 안전"),
            (("끼임", "협착", "회전", "구동"), "구동부 끼임 위험"),
            (("안전거리", "이격", "간섭"), "안전거리·간섭 방지"),
        ):
            if any(term in lowered for term in terms):
                labels.append(label)
        return labels[:20]

    @staticmethod
    def _profile_summary_points(text: str) -> list[str]:
        lowered = text.casefold()
        points: list[str] = []
        if any(term in lowered for term in ("모델", "형식", "사양", "정격", "치수")):
            points.append("모델 구성과 주요 사양을 확인할 수 있습니다.")
        if any(term in lowered for term in ("설치", "장착", "고정", "배선", "결선")):
            points.append("설치·장착·배선 조건을 확인할 수 있습니다.")
        if any(term in lowered for term in ("기능", "설정", "파라미터", "동작")):
            points.append("기능 설정과 동작 확인 방법을 확인할 수 있습니다.")
        if any(term in lowered for term in ("주의", "경고", "오동작", "손상", "위험")):
            points.append("주의사항과 오동작·손상 방지 조건을 확인할 수 있습니다.")
        return points[:8]

    def _response_from_compact_generation(
        self,
        request: AnswerRequest,
        generated: str,
    ) -> tuple[AnswerResponse | None, str | None]:
        parsed = self._extract_answer_payload(generated)
        if parsed is None:
            return None, "No JSON object was found in the model output."
        normalized = self._compact_payload_to_answer_payload(parsed, request)
        try:
            response = AnswerResponse.model_validate(normalized)
        except ValidationError as exc:
            return None, exc.errors(include_url=False).__repr__()
        if response.answer_type != request.answer_type:
            return (
                None,
                f"answer_type must be {request.answer_type}, got {response.answer_type}.",
            )
        if request.sources and response.structured_answer is None:
            return (
                None,
                "structured_answer is required when evidence sources are supplied.",
            )
        if request.sources and self._is_generic_fallback_answer(response.answer):
            return None, "answer is a generic fallback sentence."
        compact_error = self._compact_answer_quality_error(response)
        if compact_error:
            return None, compact_error
        return response, None

    def _compact_answer_quality_error(self, response: AnswerResponse) -> str | None:
        structured = response.structured_answer
        if structured is None:
            return None
        if response.answer_type == "maintenance_guide":
            fields = (
                structured.pre_checks,
                structured.hazards,
                structured.manual_steps,
                structured.stop_conditions,
                structured.related_regulations_and_incidents,
            )
            if not any(fields):
                return "maintenance compact answer has no card items."
        elif response.answer_type == "component_info":
            if not (
                structured.main_roles
                or structured.usage_locations
                or structured.precautions
                or structured.one_line_description
            ):
                return "component compact answer has no usable content."
        elif response.answer_type == "document_qa":
            if not structured.main_contents:
                return "document compact answer has no main contents."
        return None

    @staticmethod
    def _is_generic_fallback_answer(value: str) -> bool:
        return " ".join(str(value or "").split()) in GENERIC_FALLBACK_ANSWERS

    def _compact_payload_to_answer_payload(
        self,
        payload: dict[str, Any],
        request: AnswerRequest,
    ) -> dict[str, Any]:
        structured_answer = self._structured_answer_from_compact_payload(
            payload,
            request,
        )
        checklist_items = (
            self._checklist_items_from_pre_checks(structured_answer)
            if request.answer_type == "maintenance_guide"
            else []
        )
        used_source_ids = self._normalize_source_id_list(
            payload.get("used_source_ids"),
            request.sources,
        )
        if not used_source_ids:
            used_source_ids = sorted(self._collect_evidence_ids(structured_answer))
        return {
            "answer": self._compact_ai_answer(payload.get("answer"), request),
            "answer_type": request.answer_type,
            "structured_answer": structured_answer,
            "checklist_items": checklist_items,
            "used_source_ids": used_source_ids,
            "model": self.settings.base_model,
        }

    def _structured_answer_from_compact_payload(
        self,
        payload: dict[str, Any],
        request: AnswerRequest,
    ) -> dict[str, Any] | None:
        if not request.sources:
            return None
        candidate = self._normalized_candidate_structured_answer(request)
        if request.answer_type == "document_qa":
            return self._document_structured_from_compact(payload, request, candidate)
        if request.answer_type == "component_info":
            return self._component_structured_from_compact(payload, request, candidate)
        return self._maintenance_structured_from_compact(payload, request, candidate)

    def _document_structured_from_compact(
        self,
        payload: dict[str, Any],
        request: AnswerRequest,
        candidate: dict[str, Any],
    ) -> dict[str, Any]:
        overview = dict(candidate.get("overview") or {})
        if not overview:
            first = request.sources[0]
            overview = {
                "filename": first.original_filename or first.title,
                "document_type": first.source_type,
                "manufacturer": None,
                "model_name": None,
                "version": (
                    str(first.document_version)
                    if first.document_version is not None
                    else None
                ),
                "authored_at": None,
            }
        main_contents = self._evidence_items_from_payload_or_candidate(
            payload.get("main_contents"),
            candidate.get("main_contents"),
            request,
            limit=4,
        )
        supported_tasks = self._string_values_from_payload_or_candidate(
            payload.get("supported_tasks"),
            candidate.get("supported_tasks"),
            limit=5,
        )
        unverified = self._string_values_from_payload_or_candidate(
            payload.get("unverified_information"),
            candidate.get("unverified_information"),
            limit=4,
        )
        if not supported_tasks and "문서 근거에서 확인 가능한 작업 없음" not in unverified:
            unverified.append("문서 근거에서 확인 가능한 작업 없음")
        result = {
            "answer_type": "document_qa",
            "overview": overview,
            "main_contents": main_contents,
            "related_equipment": self._string_values_from_payload_or_candidate(
                payload.get("related_equipment"),
                candidate.get("related_equipment"),
                limit=5,
            ),
            "related_components": self._string_values_from_payload_or_candidate(
                payload.get("related_components"),
                candidate.get("related_components"),
                limit=5,
            ),
            "supported_tasks": supported_tasks,
            "evidence_chunk_ids": self._normalize_source_id_list(
                payload.get("used_source_ids") or payload.get("evidence_chunk_ids"),
                request.sources,
            ),
            "conflicts": self._conflicts_from_payload_or_candidate(
                payload.get("conflicts"),
                candidate.get("conflicts"),
                request,
            ),
            "unverified_information": unverified,
        }
        if not result["evidence_chunk_ids"]:
            result["evidence_chunk_ids"] = sorted(self._collect_evidence_ids(result))
        return self._normalize_structured_answer(result, request)

    def _component_structured_from_compact(
        self,
        payload: dict[str, Any],
        request: AnswerRequest,
        candidate: dict[str, Any],
    ) -> dict[str, Any]:
        one_line_description = self._short_text(
            payload.get("one_line_description")
            or candidate.get("one_line_description")
            or "근거에서 확인된 부품 정보를 정리했습니다.",
            limit=90,
        )
        result = {
            "answer_type": "component_info",
            "one_line_description": one_line_description,
            "main_roles": self._evidence_items_from_payload_or_candidate(
                payload.get("main_roles"),
                candidate.get("main_roles"),
                request,
                limit=4,
            ),
            "usage_locations": self._evidence_items_from_payload_or_candidate(
                payload.get("usage_locations"),
                candidate.get("usage_locations"),
                request,
                limit=4,
            ),
            "precautions": self._evidence_items_from_payload_or_candidate(
                payload.get("precautions"),
                candidate.get("precautions"),
                request,
                limit=4,
            ),
            "evidence_chunk_ids": self._normalize_source_id_list(
                payload.get("used_source_ids") or payload.get("evidence_chunk_ids"),
                request.sources,
            ),
            "conflicts": self._conflicts_from_payload_or_candidate(
                payload.get("conflicts"),
                candidate.get("conflicts"),
                request,
            ),
            "additional_information_needed": self._string_values_from_payload_or_candidate(
                payload.get("additional_information_needed"),
                candidate.get("additional_information_needed"),
                limit=4,
            ),
        }
        if not result["evidence_chunk_ids"]:
            result["evidence_chunk_ids"] = sorted(self._collect_evidence_ids(result))
        return self._normalize_structured_answer(result, request)

    def _maintenance_structured_from_compact(
        self,
        payload: dict[str, Any],
        request: AnswerRequest,
        candidate: dict[str, Any],
    ) -> dict[str, Any]:
        candidate_summary = candidate.get("summary") if isinstance(candidate.get("summary"), dict) else {}
        result = {
            "answer_type": "maintenance_guide",
            "summary": {
                "status": self._short_text(
                    payload.get("status")
                    or candidate_summary.get("status")
                    or "안전관리자 확인 필요",
                    limit=40,
                ),
                "risk_level": "판단 불가",
                "risk_basis": self._evidence_items_from_payload_or_candidate(
                    payload.get("risk_basis"),
                    candidate_summary.get("risk_basis"),
                    request,
                    limit=3,
                    allowed_types=MAINTENANCE_REFERENCE_SOURCE_TYPES
                    | MANUAL_SOURCE_TYPES,
                ),
                "core_warning": self._short_text(
                    payload.get("core_warning")
                    or "작업 전 안전조건을 먼저 확인해야 합니다.",
                    limit=90,
                ),
            },
            "pre_checks": self._evidence_items_from_payload_or_candidate(
                payload.get("pre_checks"),
                candidate.get("pre_checks"),
                request,
                limit=5,
            ),
            "hazards": self._hazards_from_payload_or_candidate(
                payload.get("hazards"),
                candidate.get("hazards"),
                request,
            ),
            "manual_steps": self._evidence_items_from_payload_or_candidate(
                payload.get("manual_steps"),
                candidate.get("manual_steps"),
                request,
                limit=6,
                allowed_types=MANUAL_SOURCE_TYPES,
                require_action=True,
            ),
            "precautions": self._evidence_items_from_payload_or_candidate(
                payload.get("precautions"),
                candidate.get("precautions"),
                request,
                limit=4,
                allowed_types=MANUAL_SOURCE_TYPES | PRECAUTION_REFERENCE_SOURCE_TYPES,
            ),
            "stop_conditions": self._evidence_items_from_payload_or_candidate(
                payload.get("stop_conditions"),
                candidate.get("stop_conditions"),
                request,
                limit=5,
            ),
            "related_regulations_and_incidents": self._evidence_items_from_payload_or_candidate(
                payload.get("related_regulations_and_incidents"),
                candidate.get("related_regulations_and_incidents"),
                request,
                limit=5,
                allowed_types=MAINTENANCE_REFERENCE_SOURCE_TYPES,
            ),
            "evidence_chunk_ids": self._normalize_source_id_list(
                payload.get("used_source_ids") or payload.get("evidence_chunk_ids"),
                request.sources,
            ),
            "conflicts": self._conflicts_from_payload_or_candidate(
                payload.get("conflicts"),
                candidate.get("conflicts"),
                request,
            ),
            "additional_information_needed": self._string_values_from_payload_or_candidate(
                payload.get("additional_information_needed"),
                candidate.get("additional_information_needed"),
                limit=4,
            ),
        }
        if not result["evidence_chunk_ids"]:
            result["evidence_chunk_ids"] = sorted(self._collect_evidence_ids(result))
        return self._normalize_structured_answer(result, request)

    def _normalized_candidate_structured_answer(
        self,
        request: AnswerRequest,
    ) -> dict[str, Any]:
        candidate = (
            dict(request.candidate_structured_answer)
            if isinstance(request.candidate_structured_answer, dict)
            else None
        )
        if candidate is None:
            candidate = self._fallback_structured_answer(request)
        if not isinstance(candidate, dict):
            return {}
        try:
            return self._normalize_structured_answer(candidate, request)
        except Exception:
            return {}

    def _evidence_items_from_payload_or_candidate(
        self,
        primary: Any,
        fallback: Any,
        request: AnswerRequest,
        *,
        limit: int,
        allowed_types: frozenset[str] | None = None,
        require_action: bool = False,
    ) -> list[dict[str, Any]]:
        items = self._evidence_items_from_payload(
            primary,
            request,
            limit=limit,
            allowed_types=allowed_types,
            require_action=require_action,
        )
        if items:
            return items
        return self._evidence_items_from_payload(
            fallback,
            request,
            limit=limit,
            allowed_types=allowed_types,
            require_action=require_action,
        )

    def _evidence_items_from_payload(
        self,
        value: Any,
        request: AnswerRequest,
        *,
        limit: int,
        allowed_types: frozenset[str] | None = None,
        require_action: bool = False,
    ) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        source_by_id = {source.chunk_id: source for source in request.sources}
        items: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            content = self._compact_card_text(
                item.get("content") or item.get("text") or item.get("name")
            )
            if not content:
                continue
            evidence_ids = self._normalize_source_id_list(
                item.get("evidence_chunk_ids") or item.get("source_ids"),
                request.sources,
            )
            if not evidence_ids:
                continue
            if allowed_types is not None and any(
                source_by_id[source_id].source_type.casefold() not in allowed_types
                for source_id in evidence_ids
            ):
                continue
            if require_action and not self._looks_like_actionable_manual_step(content):
                continue
            normalized = {
                "content": content,
                "evidence_chunk_ids": evidence_ids,
            }
            if normalized not in items:
                items.append(normalized)
            if len(items) >= limit:
                break
        return items

    def _hazards_from_payload_or_candidate(
        self,
        primary: Any,
        fallback: Any,
        request: AnswerRequest,
    ) -> list[dict[str, Any]]:
        hazards = self._hazards_from_payload(primary, request)
        if hazards:
            return hazards
        return self._hazards_from_payload(fallback, request)

    def _hazards_from_payload(
        self,
        value: Any,
        request: AnswerRequest,
    ) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        hazards: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            content = self._compact_card_text(
                item.get("content") or item.get("description") or item.get("text")
            )
            if not content:
                continue
            evidence_ids = self._normalize_source_id_list(
                item.get("evidence_chunk_ids") or item.get("source_ids"),
                request.sources,
            )
            if not evidence_ids:
                continue
            name = self._short_text(item.get("name") or content, limit=24)
            hazards.append(
                {
                    "name": name,
                    "content": content,
                    "evidence_chunk_ids": evidence_ids,
                }
            )
            if len(hazards) >= 3:
                break
        return hazards

    def _conflicts_from_payload_or_candidate(
        self,
        primary: Any,
        fallback: Any,
        request: AnswerRequest,
    ) -> list[dict[str, Any]]:
        conflicts = self._normalize_conflicts(primary, request.sources)
        if conflicts:
            return conflicts
        return self._normalize_conflicts(fallback, request.sources)

    def _string_values_from_payload_or_candidate(
        self,
        primary: Any,
        fallback: Any,
        *,
        limit: int,
    ) -> list[str]:
        values = self._short_string_values(primary, limit=limit)
        if values:
            return values
        return self._short_string_values(fallback, limit=limit)

    def _short_string_values(self, value: Any, *, limit: int) -> list[str]:
        values = value if isinstance(value, list) else []
        normalized: list[str] = []
        for item in values:
            text = self._compact_card_text(
                item.get("content") if isinstance(item, dict) else item
            )
            if text and text not in normalized:
                normalized.append(text)
            if len(normalized) >= limit:
                break
        return normalized

    def _compact_ai_answer(self, value: Any, request: AnswerRequest) -> str:
        answer = self._short_text(value, limit=240)
        if not answer:
            return self._fallback_polite_answer(request)
        if answer.endswith(("요", "니다", "습니다", "합니다", ".", "?", "!")):
            return answer
        return f"{answer}입니다."

    @staticmethod
    def _short_text(value: Any, *, limit: int) -> str:
        text = " ".join(str(value or "").split())
        text = text.strip(" -•*[]")
        if len(text) <= limit:
            return text
        return text[: limit - 3].rstrip() + "..."

    def _compact_card_text(self, value: Any) -> str:
        text = " ".join(str(value or "").split())
        text = text.strip(" -•*[]()")
        if not text or self._looks_like_bad_card_text(text):
            return ""
        if len(text) <= MAX_COMPACT_CARD_CHARS:
            return text.rstrip(". ")
        return ""

    @staticmethod
    def _looks_like_bad_card_text(text: str) -> bool:
        normalized = " ".join(str(text or "").split())
        if not normalized:
            return True
        if normalized in GENERIC_FALLBACK_ANSWERS:
            return True
        if normalized.startswith(BAD_CARD_PREFIXES):
            return True
        if normalized.endswith(BAD_CARD_SUFFIXES):
            return True
        if "..." in normalized:
            return True
        return False

    @staticmethod
    def _fallback_polite_answer(request: AnswerRequest) -> str:
        if not request.sources:
            return "확인 가능한 근거가 부족합니다."
        if request.answer_type == "document_qa":
            return "검색된 문서 근거를 기준으로 질문과 관련된 내용을 요약했습니다."
        if request.answer_type == "component_info":
            return "검색된 문서 근거를 기준으로 부품 정보를 요약했습니다."
        return "검색된 근거를 기준으로 작업 전 확인할 핵심 사항을 요약했습니다."

    def _normalize_structured_answer(
        self,
        value: dict[str, Any],
        request: AnswerRequest,
    ) -> dict[str, Any]:
        normalized = self._normalize_evidence_references(value, request.sources)
        normalized.setdefault("answer_type", request.answer_type)

        if request.answer_type == "maintenance_guide":
            normalized["summary"] = self._normalize_maintenance_summary(
                normalized.get("summary"),
                request,
            )
            normalized["pre_checks"] = self._filter_evidence_items(
                normalized.get("pre_checks"),
                request.sources,
                allowed_types=MANUAL_SOURCE_TYPES | PRECAUTION_REFERENCE_SOURCE_TYPES,
            )
            normalized["hazards"] = self._normalize_maintenance_hazards(
                normalized.get("hazards"),
                request.sources,
            )
            normalized["related_regulations_and_incidents"] = [
                normalized_item
                for item in self._as_dict_list(
                    normalized.get("related_regulations_and_incidents")
                )
                if (
                    normalized_item := self._normalize_evidence_item(
                        item,
                        request.sources,
                    )
                )
                if self._all_evidence_from_source_types(
                    normalized_item,
                    request.sources,
                    MAINTENANCE_REFERENCE_SOURCE_TYPES,
                )
            ]
            normalized["manual_steps"] = [
                normalized_item
                for item in self._as_dict_list(normalized.get("manual_steps"))
                if (
                    normalized_item := self._normalize_evidence_item(
                        item,
                        request.sources,
                    )
                )
                if self._all_evidence_from_source_types(
                    normalized_item,
                    request.sources,
                    MANUAL_SOURCE_TYPES,
                )
                and self._looks_like_actionable_manual_step(
                    str(normalized_item.get("content") or "")
                )
            ]
            normalized["precautions"] = [
                normalized_item
                for item in self._as_dict_list(normalized.get("precautions"))
                if (
                    normalized_item := self._normalize_evidence_item(
                        item,
                        request.sources,
                    )
                )
                if self._all_evidence_from_source_types(
                    normalized_item,
                    request.sources,
                    MANUAL_SOURCE_TYPES | PRECAUTION_REFERENCE_SOURCE_TYPES,
                )
            ]
            normalized["stop_conditions"] = self._filter_evidence_items(
                normalized.get("stop_conditions"),
                request.sources,
                allowed_types=MANUAL_SOURCE_TYPES | PUBLIC_REFERENCE_SOURCE_TYPES,
            )
            normalized["additional_information_needed"] = self._normalize_string_list(
                normalized.get("additional_information_needed")
            )
            normalized["conflicts"] = self._normalize_conflicts(
                normalized.get("conflicts"),
                request.sources,
            )
            evidence_ids = self._normalize_source_id_list(
                normalized.get("evidence_chunk_ids"),
                request.sources,
            )
            normalized["evidence_chunk_ids"] = evidence_ids or sorted(
                self._collect_evidence_ids(normalized)
            )
        elif request.answer_type == "document_qa":
            normalized["main_contents"] = self._filter_evidence_items(
                normalized.get("main_contents"),
                request.sources,
            )
        elif request.answer_type == "component_info":
            normalized["main_roles"] = self._filter_evidence_items(
                normalized.get("main_roles"),
                request.sources,
            )
            normalized["usage_locations"] = self._filter_evidence_items(
                normalized.get("usage_locations"),
                request.sources,
            )
            normalized["precautions"] = self._filter_evidence_items(
                normalized.get("precautions"),
                request.sources,
            )
        return normalized

    def _fallback_answer(
        self,
        request: AnswerRequest,
        generated: str,
    ) -> AnswerResponse:
        answer_text = self._clean_answer_text(generated)
        if self._extract_json(generated) is not None or answer_text.startswith("{"):
            answer_text = ""
        if not answer_text:
            answer_text = self._fallback_answer_text(request)
        structured_answer = (
            self._normalized_candidate_structured_answer(request)
            or self._fallback_structured_answer(request)
        )
        used_source_ids = (
            sorted(self._collect_evidence_ids(structured_answer))
            if structured_answer
            else []
        )
        if not used_source_ids and request.sources:
            used_source_ids = [source.chunk_id for source in request.sources[:5]]
        payload: dict[str, Any] = {
            "answer": answer_text,
            "answer_type": request.answer_type,
            "structured_answer": structured_answer,
            "checklist_items": (
                self._checklist_items_from_pre_checks(structured_answer)
                if request.answer_type == "maintenance_guide"
                else []
            ),
            "used_source_ids": used_source_ids,
            "model": self.settings.base_model,
        }
        return AnswerResponse.model_validate(payload)

    def _fallback_answer_text(self, request: AnswerRequest) -> str:
        if not request.sources:
            return "검증 가능한 검색 근거가 없어 구조화된 답변을 만들 수 없습니다."
        if request.answer_type == "document_qa":
            return "검색된 문서 근거를 기준으로 확인 가능한 내용을 요약했습니다."
        if request.answer_type == "component_info":
            return "검색된 문서 근거에서 확인되는 부품 정보를 정리했습니다."
        return (
            "검색된 문서 근거를 기준으로 유지보수 시 확인할 사항을 정리했습니다. "
            "현장 안전관리자의 최종 확인 전에는 작업을 시작하지 마세요."
        )

    def _fallback_structured_answer(
        self,
        request: AnswerRequest,
    ) -> dict[str, Any] | None:
        if not request.sources:
            return None
        source_items = [
            {
                "content": self._concise_source_content(source),
                "evidence_chunk_ids": [source.chunk_id],
            }
            for source in request.sources[:5]
        ]
        first = request.sources[0]
        if request.answer_type == "document_qa":
            return {
                "answer_type": "document_qa",
                "overview": {
                    "filename": first.original_filename or first.title,
                    "document_type": first.source_type,
                    "manufacturer": None,
                    "model_name": None,
                    "version": (
                        str(first.document_version)
                        if first.document_version is not None
                        else None
                    ),
                    "authored_at": None,
                },
                "main_contents": source_items,
                "related_equipment": [],
                "related_components": [],
                "supported_tasks": [],
                "evidence_chunk_ids": [
                    item["evidence_chunk_ids"][0] for item in source_items
                ],
                "conflicts": [],
                "unverified_information": [
                    "검색된 근거 밖의 문서 전체 내용은 확인하지 못했습니다."
                ],
            }
        if request.answer_type == "component_info":
            return {
                "answer_type": "component_info",
                "one_line_description": (
                    "모델 응답을 구조화하지 못했습니다. 아래 검색 근거 원문을 확인해 주세요."
                ),
                "main_roles": [],
                "usage_locations": [],
                "precautions": [],
                "evidence_chunk_ids": [],
                "conflicts": [],
                "additional_information_needed": [
                    "질문 대상의 정확한 모델명과 확인하려는 용도를 알려 주세요."
                ],
            }
        public_items = [
            item
            for item, source in zip(source_items, request.sources[:5], strict=False)
            if source.source_type.casefold() in MAINTENANCE_REFERENCE_SOURCE_TYPES
        ]
        return {
            "answer_type": "maintenance_guide",
            "summary": {
                "status": "근거 부족",
                "risk_level": "판단 불가",
                "risk_basis": [],
                "core_warning": "모델 응답을 검증하지 못해 작업 절차를 생성하지 않았습니다.",
            },
            "pre_checks": [],
            "hazards": [],
            "manual_steps": [],
            "stop_conditions": [],
            "related_regulations_and_incidents": public_items,
            "evidence_chunk_ids": [
                item["evidence_chunk_ids"][0] for item in public_items
            ],
            "conflicts": [],
            "additional_information_needed": [
                "검색 근거 원문과 현장 작업표준을 직접 확인해 주세요."
            ],
        }

    def _fallback_checklist_items(
        self,
        request: AnswerRequest,
    ) -> list[dict[str, Any]]:
        return []

    def _fallback_checklist_candidates(
        self,
        sources: list[ChatSource],
    ) -> list[tuple[str, list[str]]]:
        return []

    def _enrich_answer_response(
        self,
        response: AnswerResponse,
        request: AnswerRequest,
    ) -> AnswerResponse:
        return response

    def _merge_structured_answer(
        self,
        value: dict[str, Any],
        fallback: dict[str, Any],
        answer_type: str,
    ) -> dict[str, Any]:
        return dict(value)

    def _source_item(self, source: ChatSource) -> dict[str, Any]:
        return {
            "content": self._concise_source_content(source),
            "evidence_chunk_ids": [source.chunk_id],
        }

    def _document_related_equipment(self, sources: list[ChatSource]) -> list[str]:
        return []

    def _document_related_components(self, sources: list[ChatSource]) -> list[str]:
        return []

    def _document_supported_tasks(self, sources: list[ChatSource]) -> list[str]:
        return []

    def _component_description(self, sources: list[ChatSource]) -> str:
        return "검색 근거 원문을 확인해 주세요."

    def _component_roles(self, sources: list[ChatSource]) -> list[dict[str, Any]]:
        return []

    def _component_usage_locations(
        self,
        sources: list[ChatSource],
    ) -> list[dict[str, Any]]:
        return []

    def _component_precautions(self, sources: list[ChatSource]) -> list[dict[str, Any]]:
        return []

    @staticmethod
    def _source_text(source: ChatSource) -> str:
        return " ".join(
            str(value)
            for value in (
                source.title,
                source.source_type,
                source.section,
                source.original_filename,
                source.excerpt,
            )
            if value
        ).casefold()

    @staticmethod
    def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
        return any(keyword.casefold() in text for keyword in keywords)

    @classmethod
    def _contains_keyword_groups(
        cls,
        text: str,
        groups: tuple[tuple[str, ...], ...],
    ) -> bool:
        return all(cls._contains_any(text, group) for group in groups)

    def _add_label_if_present(
        self,
        labels: list[str],
        text: str,
        keywords: tuple[str, ...],
        label: str,
    ) -> None:
        if label not in labels and self._contains_any(text, keywords):
            labels.append(label)

    @staticmethod
    def _dedupe_evidence_item_dicts(
        items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in items:
            content = str(item.get("content") or "")
            if not content or content in seen:
                continue
            seen.add(content)
            deduped.append(item)
        return deduped

    def _extract_answer_payload(self, text: str) -> dict[str, Any] | None:
        parsed = self._extract_json(text)
        if not parsed:
            return None
        nested = parsed.get("answer")
        if isinstance(nested, str):
            nested_payload = self._extract_json(nested)
            if nested_payload and (
                isinstance(nested_payload.get("structured_answer"), dict)
                or "checklist_items" in nested_payload
                or "used_source_ids" in nested_payload
                or any(
                    key in nested_payload
                    for key in (
                        "main_contents",
                        "one_line_description",
                        "main_roles",
                        "pre_checks",
                        "hazards",
                        "manual_steps",
                        "core_warning",
                    )
                )
            ):
                merged = dict(nested_payload)
                if parsed.get("model") and not merged.get("model"):
                    merged["model"] = parsed["model"]
                return merged
        return parsed

    def _normalize_evidence_references(
        self,
        value: Any,
        sources: list[ChatSource],
    ) -> Any:
        if isinstance(value, dict):
            normalized: dict[str, Any] = {}
            for key, nested in value.items():
                if key in {"evidence_chunk_ids", "used_source_ids"}:
                    normalized[key] = self._normalize_source_id_list(nested, sources)
                else:
                    normalized[key] = self._normalize_evidence_references(
                        nested,
                        sources,
                    )
            return normalized
        if isinstance(value, list):
            return [
                self._normalize_evidence_references(item, sources)
                for item in value
            ]
        return value

    def _normalize_source_id_list(
        self,
        value: Any,
        sources: list[ChatSource],
    ) -> list[str]:
        values = value if isinstance(value, list) else []
        normalized: list[str] = []
        for item in values:
            chunk_id = self._normalize_source_id(item, sources)
            if chunk_id and chunk_id not in normalized:
                normalized.append(chunk_id)
        return normalized

    @staticmethod
    def _normalize_source_id(
        value: Any,
        sources: list[ChatSource],
    ) -> str | None:
        source_ids = [source.chunk_id for source in sources]
        if isinstance(value, int):
            index = value
        elif isinstance(value, str):
            stripped = value.strip()
            if stripped in source_ids:
                return stripped
            match = re.fullmatch(r"\[?\s*(\d+)\s*\]?", stripped)
            if not match:
                return None
            index = int(match.group(1))
        else:
            return None
        if 1 <= index <= len(source_ids):
            return source_ids[index - 1]
        return None

    def _checklist_items_from_pre_checks(
        self,
        structured_answer: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        if not isinstance(structured_answer, dict):
            return []
        pre_checks = structured_answer.get("pre_checks")
        if not isinstance(pre_checks, list):
            return []
        items: list[dict[str, Any]] = []
        for pre_check in pre_checks:
            if not isinstance(pre_check, dict):
                continue
            content = self._checklist_text(pre_check.get("content"))
            evidence_ids = [
                str(source_id)
                for source_id in pre_check.get("evidence_chunk_ids", [])
                if source_id
            ]
            if not content or not evidence_ids:
                continue
            items.append(
                {
                    "id": None,
                    "content": content,
                    "sequence": len(items) + 1,
                    "is_required": True,
                    "is_completed": False,
                    "completed_by_user_id": None,
                    "completed_at": None,
                    "evidence_chunk_ids": evidence_ids,
                }
            )
            if len(items) >= 6:
                break
        return items

    def _checklist_text(self, value: Any) -> str:
        text = self._compact_card_text(value)
        text = re.sub(
            r"(하십시오|합니다|하세요|한다|할 것|해야 함|하여야 함)\.?$",
            "",
            text,
        ).strip()
        return text.rstrip(" .")

    def _normalize_checklist_items(
        self,
        value: Any,
        sources: list[ChatSource],
    ) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        sources_by_id = {source.chunk_id: source for source in sources}
        allowed_types = CHECKLIST_SOURCE_TYPES
        items: list[dict[str, Any]] = []
        for index, item in enumerate(value, start=1):
            if not isinstance(item, dict):
                continue
            content = str(item.get("content") or "").strip()
            content = self._compact_card_text(content)
            if not content:
                continue
            evidence_ids = self._normalize_source_id_list(
                item.get("evidence_chunk_ids"),
                sources,
            )
            if not evidence_ids or any(
                sources_by_id[source_id].source_type.casefold() not in allowed_types
                for source_id in evidence_ids
            ):
                continue
            items.append(
                {
                    "id": None,
                    "content": content,
                    "sequence": index,
                    "is_required": bool(item.get("is_required", True)),
                    "is_completed": False,
                    "completed_by_user_id": None,
                    "completed_at": None,
                    "evidence_chunk_ids": evidence_ids,
                }
            )
        return items

    def _collect_evidence_ids(self, value: Any) -> set[str]:
        found: set[str] = set()
        if isinstance(value, dict):
            for key, nested in value.items():
                if key == "evidence_chunk_ids" and isinstance(nested, list):
                    found.update(str(item) for item in nested if item)
                else:
                    found.update(self._collect_evidence_ids(nested))
        elif isinstance(value, list):
            for nested in value:
                found.update(self._collect_evidence_ids(nested))
        return found

    @staticmethod
    def _as_dict_list(value: Any) -> list[dict[str, Any]]:
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

    @staticmethod
    def _strip_checklist_fields(value: dict[str, Any]) -> dict[str, Any]:
        return {
            key: nested
            for key, nested in value.items()
            if key
            not in {
                "id",
                "sequence",
                "is_required",
                "is_completed",
                "completed_by_user_id",
                "completed_at",
            }
        }

    def _normalize_maintenance_summary(
        self,
        value: Any,
        request: AnswerRequest,
    ) -> dict[str, Any]:
        summary = dict(value) if isinstance(value, dict) else {}
        risk_basis = self._filter_evidence_items(
            summary.get("risk_basis"),
            request.sources,
            allowed_types=MANUAL_SOURCE_TYPES | PUBLIC_REFERENCE_SOURCE_TYPES,
        )
        status = str(summary.get("status") or "").strip()
        if status not in {"안전관리자 확인 필요", "작업 중지 권고", "근거 부족"}:
            status = "안전관리자 확인 필요" if request.sources else "근거 부족"
        risk_level = str(summary.get("risk_level") or "").strip()
        if risk_level not in {"낮음", "보통", "높음", "매우 높음", "판단 불가"}:
            risk_level = "판단 불가"
        if risk_level != "판단 불가" and not risk_basis:
            risk_level = "판단 불가"
        core_warning = str(summary.get("core_warning") or "").strip()
        if not core_warning:
            core_warning = (
                "검색 근거만으로는 작업 승인 여부를 판단할 수 없습니다. "
                "제조사 매뉴얼 원문과 현장 안전관리자 확인 후 작업하세요."
            )
        return {
            "status": status,
            "risk_level": risk_level,
            "risk_basis": risk_basis,
            "core_warning": core_warning,
        }

    def _normalize_maintenance_hazards(
        self,
        value: Any,
        sources: list[ChatSource],
    ) -> list[dict[str, Any]]:
        hazards: list[dict[str, Any]] = []
        for item in self._as_dict_list(value):
            normalized = self._normalize_evidence_item(item, sources)
            if normalized is None:
                continue
            if not self._all_evidence_from_source_types(
                normalized,
                sources,
                MANUAL_SOURCE_TYPES | PUBLIC_REFERENCE_SOURCE_TYPES,
            ):
                continue
            content = str(normalized.get("content") or "").strip()
            name = str(item.get("name") or "").strip()
            if not name:
                name = self._hazard_name_from_content(content)
            hazards.append(
                {
                    "name": name,
                    "content": content,
                    "evidence_chunk_ids": normalized["evidence_chunk_ids"],
                }
            )
            if len(hazards) >= 3:
                break
        return hazards

    def _normalize_evidence_item(
        self,
        item: dict[str, Any],
        sources: list[ChatSource],
    ) -> dict[str, Any] | None:
        content = self._compact_card_text(item.get("content"))
        if not content:
            return None
        evidence_ids = self._normalize_source_id_list(
            item.get("evidence_chunk_ids"),
            sources,
        )
        if not evidence_ids:
            return None
        return {
            "content": content,
            "evidence_chunk_ids": evidence_ids,
        }

    def _normalize_conflicts(
        self,
        value: Any,
        sources: list[ChatSource],
    ) -> list[dict[str, Any]]:
        conflicts: list[dict[str, Any]] = []
        for item in self._as_dict_list(value):
            content = str(item.get("content") or "").strip()
            evidence_ids = self._normalize_source_id_list(
                item.get("evidence_chunk_ids"),
                sources,
            )
            if content and len(evidence_ids) >= 2:
                compact_content = self._compact_item_content(content)
                if not compact_content:
                    continue
                conflicts.append(
                    {
                        "content": compact_content,
                        "evidence_chunk_ids": evidence_ids,
                    }
                )
        return conflicts

    @staticmethod
    def _normalize_string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        items: list[str] = []
        for item in value:
            if isinstance(item, str):
                text = item.strip()
            elif isinstance(item, dict):
                text = str(item.get("content") or item.get("text") or "").strip()
            else:
                text = ""
            if text and text not in items:
                items.append(text)
        return items

    @staticmethod
    def _hazard_name_from_content(content: str) -> str:
        text = " ".join(content.split())
        if ":" in text:
            candidate = text.split(":", 1)[0].strip()
            if 2 <= len(candidate) <= 24:
                return candidate
        keyword_names = (
            ("감전", "감전"),
            ("끼임", "끼임"),
            ("협착", "협착"),
            ("추락", "추락"),
            ("낙하", "낙하"),
            ("화재", "화재"),
            ("폭발", "폭발"),
            ("질식", "질식"),
            ("중독", "중독"),
            ("베임", "베임"),
            ("찔림", "찔림"),
            ("인터락", "인터락 오류"),
            ("재기동", "불시 재기동"),
            ("설정", "설정 오류"),
            ("정렬", "정렬 불량"),
            ("미끄럼", "미끄럼"),
            ("소음", "소음 노출"),
        )
        for keyword, name in keyword_names:
            if keyword in text:
                return name
        candidate = re.split(r"[\s,.;:()]+", text)[0].strip()
        return candidate[:20] if len(candidate) >= 2 else "위험요인"

    @staticmethod
    def _compact_item_content(content: str, limit: int = 220) -> str:
        text = " ".join(content.split())
        if QwenEngine._looks_like_bad_card_text(text):
            return ""
        if len(text) <= limit:
            return text
        return text[: limit - 3].rstrip() + "..."

    def _all_evidence_from_source_types(
        self,
        item: dict[str, Any],
        sources: list[ChatSource],
        allowed_types: frozenset[str],
    ) -> bool:
        evidence_ids = self._normalize_source_id_list(
            item.get("evidence_chunk_ids"),
            sources,
        )
        if not evidence_ids:
            return False
        source_by_id = {source.chunk_id: source for source in sources}
        return all(
            chunk_id in source_by_id
            and source_by_id[chunk_id].source_type.casefold() in allowed_types
            for chunk_id in evidence_ids
        )

    def _filter_evidence_items(
        self,
        value: Any,
        sources: list[ChatSource],
        *,
        allowed_types: frozenset[str] | None = None,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for item in self._as_dict_list(value):
            normalized = self._normalize_evidence_item(item, sources)
            if normalized is None:
                continue
            if allowed_types is not None and not self._all_evidence_from_source_types(
                normalized, sources, allowed_types
            ):
                continue
            items.append(normalized)
        return items

    def _has_valid_evidence(
        self,
        item: dict[str, Any],
        sources: list[ChatSource],
    ) -> bool:
        return bool(
            self._normalize_source_id_list(item.get("evidence_chunk_ids"), sources)
        )

    @staticmethod
    def _looks_like_actionable_manual_step(content: str) -> bool:
        text = " ".join(content.split())
        if not text or len(text) > 220:
            return False
        if text.count(".") + text.count("。") + text.count("?") + text.count("!") > 2:
            return False
        return bool(
            re.search(
                r"(?:확인|차단|잠금|표시|점검|검사|설치|분리|연결|정렬|고정|측정|"
                r"청소|교체|조정|기록|중지|준수|적용|verify|check|inspect|install|"
                r"remove|replace|lock|isolate|align|clean)",
                text,
                flags=re.IGNORECASE,
            )
        )

    @staticmethod
    def _source_label(source: ChatSource) -> str:
        label = source.original_filename or source.title
        location = source.section
        if source.page_start:
            location = (
                f"{location}, {source.page_start}페이지"
                if location
                else f"{source.page_start}페이지"
            )
        elif source.page:
            location = f"{location}, {source.page}페이지" if location else f"{source.page}페이지"
        return f"{label} ({location})" if location else label

    @classmethod
    def _concise_source_content(cls, source: ChatSource) -> str:
        text = cls._clean_source_excerpt(source.excerpt)
        sentences = re.split(r"(?<=[.!?。])\s+|(?<=다\.)\s+", text)
        content = next(
            (
                sentence.strip()
                for sentence in sentences
                if cls._looks_like_meaningful_source_sentence(sentence.strip())
            ),
            text,
        )
        if len(content) > 280:
            content = content[:277].rstrip() + "..."
        return content

    @staticmethod
    def _clean_source_excerpt(text: str) -> str:
        cleaned = " ".join(text.split())
        cleaned = re.sub(r"\[자료유형\]\s*.*?\[내용\]\s*", "", cleaned)
        cleaned = re.sub(r"\[제목\]\s*", "", cleaned)
        cleaned = re.sub(r"\s*\|\s*", " ", cleaned)
        cleaned = re.sub(r"\bNo\.\s*점검\s*항목\s*확인\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\b점검\s*항목\s*확인\b", "", cleaned)
        cleaned = cleaned.replace("○", " ")
        return re.sub(r"\s+", " ", cleaned).strip()

    @staticmethod
    def _looks_like_meaningful_source_sentence(text: str) -> bool:
        if len(text) < 10:
            return False
        if re.fullmatch(r"[\d\s.\-()]+", text):
            return False
        if re.match(r"^(?:및|또는|이|그|해당|검색된|있어|되어|하여야|시)\s+", text):
            return False
        return bool(re.search(r"[A-Za-z가-힣]", text))

    def _ensure_loaded(self, *, for_answer: bool = False) -> tuple[Any, Any]:
        if for_answer:
            if self._answer_tokenizer is not None and self._answer_model is not None:
                return self._answer_tokenizer, self._answer_model
            tokenizer, model = self._load_model(
                device=self.settings.answer_device,
                load_in_4bit=self.settings.answer_load_in_4bit,
                with_adapter=False,
            )
            self._answer_tokenizer = tokenizer
            self._answer_model = model
            return tokenizer, model
        if self._tokenizer is not None and self._model is not None:
            return self._tokenizer, self._model
        tokenizer, model = self._load_model(
            device=self.settings.device,
            load_in_4bit=self.settings.load_in_4bit,
            with_adapter=True,
        )
        self._tokenizer = tokenizer
        self._model = model
        return tokenizer, model

    def _load_model(
        self,
        *,
        device: str,
        load_in_4bit: bool,
        with_adapter: bool,
    ) -> tuple[Any, Any]:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(
            self.settings.base_model,
            trust_remote_code=True,
        )
        load_kwargs: dict[str, Any] = {"trust_remote_code": True}
        if device == "cuda":
            load_kwargs["device_map"] = "auto"
            load_kwargs["torch_dtype"] = "auto"
        else:
            load_kwargs["torch_dtype"] = torch.float32
        if load_in_4bit:
            from transformers import BitsAndBytesConfig

            load_kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_compute_dtype=torch.float16,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=True,
            )

        model = AutoModelForCausalLM.from_pretrained(
            self.settings.base_model,
            **load_kwargs,
        )
        adapter_path = self.settings.lora_adapter.strip()
        if with_adapter and adapter_path:
            from peft import PeftModel

            if not Path(adapter_path).exists():
                raise FileNotFoundError(f"QWEN_LORA_ADAPTER not found: {adapter_path}")
            prepared_adapter_path = self._prepare_adapter_path(Path(adapter_path))
            model = PeftModel.from_pretrained(model, str(prepared_adapter_path))
            self._adapter_loaded = True
        model.eval()
        return tokenizer, model

    def _prepare_adapter_path(self, adapter_path: Path) -> Path:
        if self._prepared_adapter_path is not None:
            return self._prepared_adapter_path

        config_path = adapter_path / "adapter_config.json"
        if not config_path.exists():
            self._prepared_adapter_path = adapter_path
            return adapter_path

        config = json.loads(config_path.read_text(encoding="utf-8"))
        target_modules = config.get("target_modules")
        config_changed = False
        if isinstance(target_modules, str) and "language_model" in target_modules:
            normalized = target_modules.replace(
                ".*language_model.*\\.",
                ".*\\.",
            )
            if normalized != target_modules:
                config["target_modules"] = normalized
                config_changed = True

        adapter_weights_path = adapter_path / "adapter_model.safetensors"
        rewrite_weights = self._adapter_weights_need_prefix_rewrite(adapter_weights_path)
        if not config_changed and not rewrite_weights:
            self._prepared_adapter_path = adapter_path
            return adapter_path

        prepared_path = Path(tempfile.mkdtemp(prefix="safemaint_qwen_adapter_"))
        for item in adapter_path.iterdir():
            if item.name == "adapter_config.json":
                continue
            if rewrite_weights and item.name == "adapter_model.safetensors":
                continue
            target = prepared_path / item.name
            if item.is_dir():
                shutil.copytree(item, target)
            else:
                shutil.copy2(item, target)

        (prepared_path / "adapter_config.json").write_text(
            json.dumps(config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if rewrite_weights:
            self._rewrite_adapter_weights(adapter_weights_path, prepared_path / adapter_weights_path.name)

        print(
            f"Prepared Qwen LoRA adapter for base model module names: {prepared_path}",
            flush=True,
        )
        self._prepared_adapter_path = prepared_path
        return prepared_path

    @staticmethod
    def _adapter_weights_need_prefix_rewrite(adapter_weights_path: Path) -> bool:
        if not adapter_weights_path.exists():
            return False
        from safetensors import safe_open

        with safe_open(str(adapter_weights_path), framework="pt", device="cpu") as weights:
            return any(".language_model." in key for key in weights.keys())

    @staticmethod
    def _rewrite_adapter_weights(source_path: Path, target_path: Path) -> None:
        from safetensors import safe_open
        from safetensors.torch import load_file, save_file

        with safe_open(str(source_path), framework="pt", device="cpu") as weights:
            metadata = weights.metadata()

        state = load_file(str(source_path), device="cpu")
        rewritten: dict[str, Any] = {}
        for key, tensor in state.items():
            new_key = key.replace(".language_model.", ".")
            if new_key in rewritten:
                raise ValueError(f"Duplicate LoRA adapter weight key after rewrite: {new_key}")
            rewritten[new_key] = tensor

        save_file(rewritten, str(target_path), metadata=metadata)

    def _generate(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        max_new_tokens: int,
        disable_adapter: bool,
    ) -> str:
        import torch

        tokenizer, model = self._ensure_loaded(for_answer=disable_adapter)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        if hasattr(tokenizer, "apply_chat_template"):
            try:
                prompt = tokenizer.apply_chat_template(
                    messages,
                    tokenize=False,
                    add_generation_prompt=True,
                    enable_thinking=False,
                )
            except TypeError:
                prompt = tokenizer.apply_chat_template(
                    messages,
                    tokenize=False,
                    add_generation_prompt=True,
                )
        else:
            # 기존 영문 생성 표지: Answer:
            prompt = f"{system_prompt}\n\n{user_prompt}\n\n답변:"
        inputs = tokenizer(prompt, return_tensors="pt")
        input_device = self._input_device(model)
        inputs = inputs.to(input_device)
        adapter_context = (
            model.disable_adapter()
            if (
                disable_adapter
                and model is self._model
                and self._adapter_loaded
                and hasattr(model, "disable_adapter")
            )
            else nullcontext()
        )
        started_at = time.perf_counter()
        with adapter_context:
            with torch.inference_mode():
                if self._is_cuda_device(input_device):
                    torch.cuda.synchronize(input_device)
                output_ids = model.generate(
                    **inputs,
                    max_new_tokens=max_new_tokens,
                    do_sample=False,
                    repetition_penalty=1.15,
                    no_repeat_ngram_size=3,
                    pad_token_id=tokenizer.eos_token_id,
                )
                if self._is_cuda_device(input_device):
                    torch.cuda.synchronize(input_device)
        elapsed_seconds = time.perf_counter() - started_at
        generated = output_ids[0][inputs["input_ids"].shape[-1] :]
        self._log_generation_metrics(
            model,
            torch_module=torch,
            input_tokens=int(inputs["input_ids"].shape[-1]),
            new_tokens=int(generated.shape[-1]),
            elapsed_seconds=elapsed_seconds,
            max_new_tokens=max_new_tokens,
            disable_adapter=disable_adapter,
        )
        return tokenizer.decode(generated, skip_special_tokens=True).strip()

    @staticmethod
    def _is_cuda_device(device: Any) -> bool:
        return str(getattr(device, "type", device)).startswith("cuda")

    @staticmethod
    def _log_generation_metrics(
        model: Any,
        *,
        torch_module: Any,
        input_tokens: int,
        new_tokens: int,
        elapsed_seconds: float,
        max_new_tokens: int,
        disable_adapter: bool,
    ) -> None:
        tokens_per_second = (
            new_tokens / elapsed_seconds if elapsed_seconds > 0 else None
        )
        gpu_name = None
        if torch_module.cuda.is_available():
            try:
                gpu_name = torch_module.cuda.get_device_name(0)
            except Exception:
                gpu_name = None
        is_loaded_in_4bit = bool(getattr(model, "is_loaded_in_4bit", False))
        if not is_loaded_in_4bit:
            base_model = getattr(model, "base_model", None)
            is_loaded_in_4bit = bool(
                getattr(base_model, "is_loaded_in_4bit", False)
            )
        print(
            json.dumps(
                {
                    "event": "qwen_generate",
                    "input_tokens": input_tokens,
                    "new_tokens": new_tokens,
                    "elapsed_seconds": round(elapsed_seconds, 3),
                    "tokens_per_second": (
                        round(tokens_per_second, 3)
                        if tokens_per_second is not None
                        else None
                    ),
                    "max_new_tokens": max_new_tokens,
                    "disable_adapter": disable_adapter,
                    "cuda_available": bool(torch_module.cuda.is_available()),
                    "gpu_name": gpu_name,
                    "hf_device_map": str(getattr(model, "hf_device_map", None)),
                    "is_loaded_in_4bit": is_loaded_in_4bit,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    @staticmethod
    def _clean_answer_text(text: str) -> str:
        cleaned = re.sub(r"(?is)<think>.*?</think>", "", text).strip()
        final_markers = (
            "최종 답변:",
            "답변:",
            "Final Answer:",
            "Final:",
            "Answer:",
        )
        lowered = cleaned.lower()
        for marker in final_markers:
            index = lowered.find(marker.lower())
            if index >= 0:
                return cleaned[index + len(marker) :].strip()
        if re.match(r"(?is)^\s*(thinking process|analysis|reasoning)\s*:", cleaned):
            return ""
        return cleaned

    @staticmethod
    def _input_device(model: Any) -> Any:
        try:
            return next(model.parameters()).device
        except StopIteration:
            return "cpu"

    def _parse_classification(
        self, text: str
    ) -> tuple[
        str,
        float | None,
        str | None,
        float | None,
        str | None,
    ]:
        parsed = self._extract_json(text)
        if parsed:
            occurrence_type = str(parsed.get("occurrence_type") or "").strip()
            confidence = parsed.get("confidence")
            question_intent = str(parsed.get("question_intent") or "").strip()
            if question_intent not in {
                "document_qa",
                "maintenance_guide",
                "component_info",
                "clarification_required",
            }:
                question_intent = ""
            intent_confidence = parsed.get("intent_confidence")
            clarification_question = str(
                parsed.get("clarification_question") or ""
            ).strip() or None
            if occurrence_type:
                try:
                    confidence_value = float(confidence)
                except (TypeError, ValueError):
                    confidence_value = None
                try:
                    intent_confidence_value = float(intent_confidence)
                except (TypeError, ValueError):
                    intent_confidence_value = None
                return (
                    occurrence_type,
                    confidence_value,
                    question_intent or None,
                    intent_confidence_value,
                    clarification_question,
                )
        for label in self.settings.occurrence_labels:
            if label and label in text:
                return label, None, None, None, None
        return "기타", None, None, None, None

    def _parse_occurrence_classification(
        self,
        text: str,
    ) -> tuple[str, float | None, list[str]]:
        occurrence_type, confidence, _, _, _ = self._parse_classification(text)
        risk_factors = self._parse_occurrence_risk_factors(text, occurrence_type)
        return occurrence_type, confidence, risk_factors

    def _parse_occurrence_risk_factors(
        self,
        text: str,
        occurrence_type: str,
    ) -> list[str]:
        labels: list[str] = []

        def add(value: Any) -> None:
            normalized = str(value or "").strip()
            if normalized in self.settings.occurrence_labels and normalized not in labels:
                labels.append(normalized)

        add(occurrence_type)
        parsed = self._extract_json(text)
        if isinstance(parsed, dict):
            raw_values = (
                parsed.get("explicit_risk_factors")
                or parsed.get("risk_factors")
                or parsed.get("hazards")
                or []
            )
            if isinstance(raw_values, str):
                raw_values = [raw_values]
            if isinstance(raw_values, list):
                for raw_value in raw_values:
                    add(raw_value)
                    if len(labels) >= 3:
                        break
        if len(labels) < 3:
            for label in self.settings.occurrence_labels:
                if label in text:
                    add(label)
                    if len(labels) >= 3:
                        break
        return labels[:3]

    def _parse_intent_classification(
        self,
        text: str,
    ) -> tuple[str, float | None, str | None]:
        parsed = self._extract_json(text)
        if parsed:
            question_intent = str(parsed.get("question_intent") or "").strip()
            if question_intent not in {
                "document_qa",
                "maintenance_guide",
                "component_info",
                "clarification_required",
            }:
                question_intent = "clarification_required"
            try:
                confidence = (
                    float(parsed.get("intent_confidence"))
                    if parsed.get("intent_confidence") is not None
                    else None
                )
            except (TypeError, ValueError):
                confidence = None
            if confidence is not None:
                confidence = min(max(confidence, 0.0), 1.0)
            clarification_question = str(
                parsed.get("clarification_question") or ""
            ).strip() or None
            return question_intent, confidence, clarification_question
        return "clarification_required", None, None

    def _compact_answer_format_for_type(self, request: AnswerRequest) -> str:
        source_count = len(request.sources)
        # 기존 영문 프롬프트:
        # Return JSON only. Do not include structured_answer. Cite sources by number,
        # use {content, evidence_chunk_ids}, and keep answers/cards short.
        common = (
            "JSON만 반환하고 structured_answer는 포함하지 마세요. "
            "근거의 [n] 표시에 맞춰 출처 번호만 인용하세요"
            f"(유효한 번호는 1부터 {source_count}까지). chunk_id 문자열은 사용하지 마세요. "
            "근거가 필요한 각 카드 항목은 content와 evidence_chunk_ids를 가진 객체여야 하며, "
            'evidence_chunk_ids에는 ["2"]처럼 출처 번호 목록을 넣으세요. '
            "answer는 한국어 두 문장 이내, 각 카드 항목은 한국어 40자 이내로 작성하세요."
        )
        if request.answer_type == "document_qa":
            return (
                f"{common}\n"
                "스키마: {answer, main_contents, related_equipment, related_components, "
                "supported_tasks, unverified_information, conflicts, used_source_ids}. "
                "supported_tasks에는 선택한 PDF 근거에서 실제로 확인된 작업만 넣으세요. "
                "확인된 작업이 없으면 supported_tasks는 []로 반환하세요."
            )
        if request.answer_type == "component_info":
            return (
                f"{common}\n"
                "스키마: {answer, one_line_description, main_roles, usage_locations, "
                "precautions, additional_information_needed, conflicts, used_source_ids}. "
                "설치 또는 유지보수 절차 단계는 작성하지 마세요."
            )
        manual_numbers = [
            index
            for index, source in enumerate(request.sources, start=1)
            if source.source_type.casefold() in MANUAL_SOURCE_TYPES
        ]
        reference_numbers = [
            index
            for index, source in enumerate(request.sources, start=1)
            if source.source_type.casefold() in MAINTENANCE_REFERENCE_SOURCE_TYPES
        ]
        precaution_numbers = [
            index
            for index, source in enumerate(request.sources, start=1)
            if source.source_type.casefold()
            in (MANUAL_SOURCE_TYPES | PRECAUTION_REFERENCE_SOURCE_TYPES)
        ]
        return (
            f"{common}\n"
            "스키마: {answer, status, core_warning, risk_basis, pre_checks, hazards, "
            "manual_steps, precautions, stop_conditions, related_regulations_and_incidents, "
            "additional_information_needed, conflicts, used_source_ids}. "
            "hazards 항목은 {name, content, evidence_chunk_ids} 형식으로 최대 세 개만 작성하세요. "
            f"manual_steps는 다음 출처 번호만 인용할 수 있습니다: {manual_numbers}. "
            f"precautions는 작업 주의사항이며 정지 조건이 아닙니다. 다음 출처 번호만 인용하세요: {precaution_numbers}. "
            f"related_regulations_and_incidents와 risk_basis는 다음 출처 번호만 인용하세요: {reference_numbers}. "
            "checklist_items는 생성하지 마세요. 백엔드가 pre_checks에서 생성합니다. "
            "위험 점수나 위험 등급은 출력하지 마세요."
        )

    def _candidate_text(self, request: AnswerRequest) -> str:
        candidate = self._normalized_candidate_structured_answer(request)
        if not candidate:
            return "백엔드 후보 카드가 제공되지 않았습니다."
        compact = self._compact_candidate_value(candidate)
        return json.dumps(compact, ensure_ascii=False)

    def _compact_candidate_value(self, value: Any) -> Any:
        if isinstance(value, dict):
            compact: dict[str, Any] = {}
            for key, nested in value.items():
                if key in {"id", "sequence", "is_completed", "completed_at"}:
                    continue
                compact[key] = self._compact_candidate_value(nested)
            return compact
        if isinstance(value, list):
            return [self._compact_candidate_value(item) for item in value[:6]]
        if isinstance(value, str):
            return self._short_text(value, limit=120)
        return value

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any] | None:
        stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
        candidates = [stripped]
        decoder = json.JSONDecoder()
        for match in re.finditer(r"\{", stripped):
            try:
                value, _ = decoder.raw_decode(stripped[match.start() :])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
        match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
        if match:
            candidates.append(match.group(0))
        for candidate in candidates:
            try:
                value = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
        return None

    @staticmethod
    def _context_text(context: Any) -> str:
        values = context.model_dump(exclude_none=True)
        if not values:
            return "No structured work context was supplied."
        return "\n".join(f"- {key}: {value}" for key, value in values.items())

    @staticmethod
    def _analysis_text(analysis: QueryAnalysis | None) -> str:
        if analysis is None:
            return "No structured analysis was supplied."
        values = analysis.model_dump(exclude_none=True)
        if not values:
            return "No structured analysis was supplied."
        return "\n".join(f"- {key}: {value}" for key, value in values.items())

    @staticmethod
    def _evidence_text(request: AnswerRequest) -> str:
        if not request.sources:
            return "No evidence was supplied."
        lines: list[str] = []
        for index, source in enumerate(request.sources, start=1):
            location_parts = []
            if source.section:
                location_parts.append(source.section)
            if source.page_start:
                page_text = f"page {source.page_start}"
                if source.page_end and source.page_end != source.page_start:
                    page_text += f"-{source.page_end}"
                location_parts.append(page_text)
            elif source.page:
                location_parts.append(f"page {source.page}")
            location = ", ".join(location_parts) if location_parts else "unknown location"
            profile = source.document_profile if isinstance(source.document_profile, dict) else {}
            profile_text = QwenEngine._compact_document_profile_text(profile)
            lines.extend(
                [
                    f"[{index}] title: {source.title}",
                    f"[{index}] scope/type: {source.document_scope or 'unknown'} / {source.source_type}",
                    f"[{index}] location: {location}",
                    *([f"[{index}] document_profile: {profile_text}"] if profile_text else []),
                    f"[{index}] evidence: {source.excerpt}",
                ]
            )
        return "\n".join(lines)

    @staticmethod
    def _compact_document_profile_text(profile: dict[str, Any]) -> str:
        parts: list[str] = []
        for field in (
            "product_names",
            "model_names",
            "aliases",
            "components",
            "equipment",
            "supported_tasks",
            "safety_topics",
            "summary_points",
        ):
            values = profile.get(field)
            if isinstance(values, list):
                compact_values = [
                    " ".join(str(value).split())
                    for value in values[:5]
                    if str(value).strip()
                ]
            elif isinstance(values, str):
                compact_values = [" ".join(values.split())]
            else:
                compact_values = []
            if compact_values:
                parts.append(f"{field}={compact_values}")
        text = "; ".join(parts)
        return text[:1200]
