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
    QueryAnalysis,
)


MANUAL_SOURCE_TYPES = frozenset(
    {"equipment_manual", "component_manual"}
)
PUBLIC_REFERENCE_SOURCE_TYPES = frozenset(
    {"public_law", "public_guide", "public_incident", "public_media"}
)


class QwenEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = asyncio.Lock()
        self._tokenizer: Any = None
        self._model: Any = None
        self._adapter_loaded = False
        self._prepared_adapter_path: Path | None = None

    async def classify(self, request: ClassifyRequest) -> ClassifyResponse:
        async with self._lock:
            return await asyncio.to_thread(self._classify_sync, request)

    async def answer(self, request: AnswerRequest) -> AnswerResponse:
        async with self._lock:
            return await asyncio.to_thread(self._answer_sync, request)

    def _classify_sync(self, request: ClassifyRequest) -> ClassifyResponse:
        labels = ", ".join(self.settings.occurrence_labels)
        system_prompt = (
            "SafeMaint 질문목적 및 사고유형 분류기입니다. 반드시 JSON만 출력하세요. "
            "사고 과정, 분석 과정, 설명 문장은 출력하지 마세요."
        )
        user_prompt = (
            "Choose one occurrence_type from this label list:\n"
            f"{labels}\n\n"
            "Choose question_intent by the user's purpose, not by a component noun:\n"
            "- document_qa: asks what a selected PDF/document contains or requests a summary\n"
            "- maintenance_guide: asks how to install, inspect, clean, repair, or replace\n"
            "- component_info: asks definition, role, purpose, or where a component is used\n"
            "- clarification_required: purpose is ambiguous\n"
            "Examples:\n"
            "이 PDF를 요약해줘 -> document_qa\n"
            "'X가 무슨 장비야' -> component_info\n"
            "'X 설치 방법' -> maintenance_guide\n"
            "'X 관련해서 알려줘' -> clarification_required\n\n"
            "Return JSON exactly like "
            '{"occurrence_type":"label","confidence":0.0,'
            '"question_intent":"component_info","intent_confidence":0.0,'
            '"clarification_question":null}.\n\n'
            f"Work context:\n{self._context_text(request.context)}\n\n"
            f"Question:\n{request.question}"
        )
        text = self._generate(
            system_prompt,
            user_prompt,
            max_new_tokens=self.settings.classify_max_new_tokens,
            disable_adapter=False,
        )
        (
            occurrence_type,
            confidence,
            question_intent,
            intent_confidence,
            clarification_question,
        ) = self._parse_classification(text)
        analysis = QueryAnalysis(
            occurrence_type=occurrence_type,
            question_intent=question_intent,
            intent_confidence=intent_confidence,
            clarification_question=clarification_question,
        )
        return ClassifyResponse(
            occurrence_type=occurrence_type,
            confidence=confidence,
            question_intent=question_intent,
            intent_confidence=intent_confidence,
            clarification_question=clarification_question,
            analysis=analysis,
            model=self.settings.base_model,
        )

    def _answer_sync(self, request: AnswerRequest) -> AnswerResponse:
        if self.settings.answer_mode == "text":
            return self._answer_text_sync(request)

        evidence = self._evidence_text(request)
        system_prompt = (
            "당신은 SafeMaint AI입니다. 반드시 유효한 JSON 객체 하나만 출력하세요. "
            "사고 과정, Thinking Process, 분석 과정, 계획, 내부 추론은 절대 출력하지 마세요. "
            "제공된 근거만 사용하고 파일명, 페이지, 법령, 사고사례, 절차를 만들지 마세요. "
            "evidence_chunk_ids와 used_source_ids에는 제공된 chunk_id만 넣으세요. "
            "작업 승인, 안전함, 그대로 작업해도 됨 같은 표현을 사용하지 마세요."
        )
        user_prompt = (
            f"Answer type: {request.answer_type}\n"
            f"{self._answer_format_for_type(request)}\n\n"
            f"작업 정보:\n{self._context_text(request.context)}\n\n"
            f"분석:\n{self._analysis_text(request.analysis)}\n\n"
            f"근거:\n{evidence}\n\n"
            f"질문:\n{request.question}"
        )
        max_answer_tokens = (
            min(self.settings.max_new_tokens, 768)
            if request.answer_type == "maintenance_guide"
            else self.settings.max_new_tokens
        )
        generated = self._generate(
            system_prompt,
            user_prompt,
            max_new_tokens=max_answer_tokens,
            disable_adapter=True,
        )
        response, validation_error = self._response_from_generation(
            request,
            generated,
        )
        if response is not None:
            return response

        if validation_error and self.settings.repair_enabled:
            repaired = self._generate(
                system_prompt,
                self._repair_prompt(request, generated, validation_error),
                max_new_tokens=min(max_answer_tokens, 512),
                disable_adapter=True,
            )
            response, _ = self._response_from_generation(request, repaired)
            if response is not None:
                return response
        elif validation_error:
            print(
                json.dumps(
                    {
                        "event": "qwen_repair_skipped",
                        "answer_type": request.answer_type,
                        "reason": validation_error[:300],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

        return self._fallback_answer(request, generated)

    def _answer_text_sync(self, request: AnswerRequest) -> AnswerResponse:
        evidence = self._evidence_text(request)
        system_prompt = (
            "You are SafeMaint AI. Write the final answer in Korean only. "
            "Use only the provided evidence. Do not output JSON, markdown tables, "
            "checklists, hidden reasoning, or source data structures. "
            "Cite every evidence-backed claim with the matching numeric marker "
            "such as [1]. Do not cite a source that does not support the claim."
        )
        user_prompt = (
            f"Answer type: {request.answer_type}\n"
            f"{self._answer_text_instructions(request)}\n\n"
            f"Work context:\n{self._context_text(request.context)}\n\n"
            f"Analysis:\n{self._analysis_text(request.analysis)}\n\n"
            f"Evidence:\n{evidence}\n\n"
            f"Question:\n{request.question}"
        )
        generated = self._generate(
            system_prompt,
            user_prompt,
            max_new_tokens=self.settings.max_new_tokens,
            disable_adapter=True,
        )
        return AnswerResponse(
            answer=self._answer_text_from_generation(request, generated),
            answer_type=request.answer_type,
            structured_answer=None,
            checklist_items=[],
            used_source_ids=self._source_ids_from_answer_citations(
                generated,
                request.sources,
            ),
            model=self.settings.base_model,
        )

    def _answer_text_from_generation(
        self,
        request: AnswerRequest,
        generated: str,
    ) -> str:
        parsed = self._extract_answer_payload(generated)
        parsed_answer = parsed and isinstance(parsed.get("answer"), str)
        if parsed_answer:
            answer_text = self._clean_answer_text(parsed["answer"])
        else:
            answer_text = self._clean_answer_text(generated)
        if (
            not parsed_answer
            and self._extract_json(generated) is not None
        ) or answer_text.startswith("{"):
            answer_text = ""
        return answer_text or self._fallback_answer_text(request)

    @staticmethod
    def _source_ids_from_answer_citations(
        answer: str,
        sources: list[ChatSource],
    ) -> list[str]:
        source_ids: list[str] = []
        for match in re.findall(r"\[\s*(\d+)\s*\]", answer):
            index = int(match)
            if 1 <= index <= len(sources):
                source_id = sources[index - 1].chunk_id
                if source_id not in source_ids:
                    source_ids.append(source_id)
        return source_ids

    def _response_from_generation(
        self,
        request: AnswerRequest,
        generated: str,
    ) -> tuple[AnswerResponse | None, str | None]:
        parsed = self._extract_answer_payload(generated)
        if parsed is None:
            return None, "No JSON object was found in the model output."
        normalized = self._normalize_answer_payload(parsed, request)
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
        return response, None

    def _normalize_answer_payload(
        self,
        payload: dict[str, Any],
        request: AnswerRequest,
    ) -> dict[str, Any]:
        normalized = dict(payload)
        normalized.setdefault("answer_type", request.answer_type)
        normalized["model"] = self.settings.base_model
        if not isinstance(normalized.get("answer"), str):
            normalized["answer"] = self._fallback_answer_text(request)

        structured = normalized.get("structured_answer")
        if isinstance(structured, str):
            structured = self._extract_json(structured)
        if isinstance(structured, dict):
            structured = self._normalize_structured_answer(structured, request)
            normalized["structured_answer"] = structured

        if request.answer_type == "maintenance_guide":
            normalized["checklist_items"] = self._normalize_checklist_items(
                normalized.get("checklist_items"),
                request.sources,
            )
        else:
            normalized["checklist_items"] = []
        normalized["used_source_ids"] = self._normalize_source_id_list(
            normalized.get("used_source_ids"),
            request.sources,
        )
        if not normalized["used_source_ids"] and isinstance(structured, dict):
            normalized["used_source_ids"] = sorted(
                self._collect_evidence_ids(structured)
            )
        return normalized

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
                    PUBLIC_REFERENCE_SOURCE_TYPES,
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
            normalized["stop_conditions"] = self._filter_evidence_items(
                normalized.get("stop_conditions"),
                request.sources,
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

    def _repair_prompt(
        self,
        request: AnswerRequest,
        generated: str,
        validation_error: str,
    ) -> str:
        allowed_ids = [source.chunk_id for source in request.sources]
        parsed = self._extract_answer_payload(generated)
        previous = (
            json.dumps(parsed, ensure_ascii=False)
            if parsed is not None
            else " ".join(generated.split())
        )
        previous = previous[:1200]
        error_text = " ".join(validation_error.split())[:600]
        evidence = self._evidence_text(request)[:1200]
        return (
            "The previous output did not match the API schema. "
            "Return one corrected JSON object only. Do not wrap JSON in a string. "
            "Do not use markdown fences.\n\n"
            f"Expected answer_type: {request.answer_type}\n"
            f"Allowed chunk_id values: {json.dumps(allowed_ids, ensure_ascii=False)}\n"
            "Rules:\n"
            "- evidence_chunk_ids and used_source_ids must contain only allowed chunk_id values.\n"
            "- If you meant source number 1, use the first allowed chunk_id exactly.\n"
            "- Every evidence-backed list item must be an object with content and evidence_chunk_ids.\n"
            "- maintenance_guide.summary must include status, risk_level, risk_basis, and core_warning.\n"
            "- maintenance_guide.hazards items must include name, content, and evidence_chunk_ids.\n"
            "- Checklist fields sequence/is_required belong only in checklist_items, never in hazards.\n"
            "- Only maintenance_guide may return checklist_items. Every checklist item must cite allowed evidence.\n"
            "- additional_information_needed must be a list of plain strings.\n"
            "- If a field cannot be verified from allowed evidence, return an empty list for that field.\n\n"
            f"Validation error summary:\n{error_text}\n\n"
            f"Previous output preview:\n{previous}\n\n"
            f"Question:\n{request.question}\n\n"
            f"Evidence preview:\n{evidence}"
        )

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
        structured_answer = self._fallback_structured_answer(request)
        payload: dict[str, Any] = {
            "answer": answer_text,
            "answer_type": request.answer_type,
            "structured_answer": structured_answer,
            "checklist_items": [],
            "used_source_ids": (
                sorted(self._collect_evidence_ids(structured_answer))
                if structured_answer
                else []
            ),
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
            if source.source_type.casefold() in PUBLIC_REFERENCE_SOURCE_TYPES
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

    def _normalize_checklist_items(
        self,
        value: Any,
        sources: list[ChatSource],
    ) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        sources_by_id = {source.chunk_id: source for source in sources}
        allowed_types = MANUAL_SOURCE_TYPES | frozenset(
            {"public_law", "public_guide"}
        )
        items: list[dict[str, Any]] = []
        for index, item in enumerate(value, start=1):
            if not isinstance(item, dict):
                continue
            content = str(item.get("content") or "").strip()
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
        content = str(item.get("content") or "").strip()
        if not content:
            return None
        evidence_ids = self._normalize_source_id_list(
            item.get("evidence_chunk_ids"),
            sources,
        )
        if not evidence_ids:
            return None
        return {
            "content": self._compact_item_content(content),
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
                conflicts.append(
                    {
                        "content": self._compact_item_content(content),
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
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for item in self._as_dict_list(value):
            normalized = self._normalize_evidence_item(item, sources)
            if normalized is not None:
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

    def _ensure_loaded(self) -> tuple[Any, Any]:
        if self._tokenizer is not None and self._model is not None:
            return self._tokenizer, self._model

        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(
            self.settings.base_model,
            trust_remote_code=True,
        )
        load_kwargs: dict[str, Any] = {"trust_remote_code": True}
        if self.settings.device == "cuda":
            load_kwargs["device_map"] = "auto"
            load_kwargs["torch_dtype"] = "auto"
        else:
            load_kwargs["torch_dtype"] = torch.float32
        if self.settings.load_in_4bit:
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
        if adapter_path:
            from peft import PeftModel

            if not Path(adapter_path).exists():
                raise FileNotFoundError(f"QWEN_LORA_ADAPTER not found: {adapter_path}")
            prepared_adapter_path = self._prepare_adapter_path(Path(adapter_path))
            model = PeftModel.from_pretrained(model, str(prepared_adapter_path))
            self._adapter_loaded = True
        model.eval()
        self._tokenizer = tokenizer
        self._model = model
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

        tokenizer, model = self._ensure_loaded()
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
            prompt = f"{system_prompt}\n\n{user_prompt}\n\nAnswer:"
        inputs = tokenizer(prompt, return_tensors="pt")
        input_device = self._input_device(model)
        inputs = inputs.to(input_device)
        adapter_context = (
            model.disable_adapter()
            if disable_adapter and self._adapter_loaded and hasattr(model, "disable_adapter")
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

    @staticmethod
    def _answer_text_instructions(request: AnswerRequest) -> str:
        if request.answer_type == "document_qa":
            return (
                "Return 2-4 concise sentences. Summarize only what the retrieved "
                "document evidence verifies. If the document/version/model is not "
                "clear from evidence, say that it must be checked."
            )
        if request.answer_type == "component_info":
            return (
                "Return 2-4 concise sentences. Explain what the component is, its "
                "main role, and one verified caution if evidence supports it. "
                "Do not give installation or repair steps."
            )
        return (
            "Return 3-5 concise sentences. Mention verified pre-checks and major "
            "hazards only from evidence. Do not approve the work. If evidence is "
            "insufficient, say that the manufacturer manual and site safety manager "
            "must be checked before work starts."
        )

    @staticmethod
    def _answer_format_for_type(request: AnswerRequest) -> str:
        allowed_source_ids = [source.chunk_id for source in request.sources]
        common = (
            "Top-level keys: answer, answer_type, structured_answer, "
            "checklist_items, used_source_ids. answer is a Korean readable text version. "
            "Every evidence-backed item has content and evidence_chunk_ids. "
            "Use exact chunk_id values for evidence_chunk_ids and used_source_ids; "
            f"allowed chunk_id values are {allowed_source_ids}. Never use numeric source indexes."
        )
        if request.answer_type == "document_qa":
            return (
                f"{common}\nstructured_answer keys: answer_type=document_qa, overview "
                "(filename, document_type, manufacturer, model_name, version, authored_at), "
                "main_contents, related_equipment, related_components, supported_tasks, "
                "evidence_chunk_ids, conflicts, unverified_information. "
                "checklist_items must be []. Do not add risk, stop conditions, or TBM."
            )
        if request.answer_type == "component_info":
            return (
                f"{common}\nstructured_answer keys: answer_type=component_info, "
                "one_line_description, main_roles, usage_locations, precautions, "
                "evidence_chunk_ids, conflicts, additional_information_needed. "
                "checklist_items must be []. Do not add installation or maintenance steps."
            )
        manual_ids = [
            source.chunk_id
            for source in request.sources
            if source.source_type.casefold() in MANUAL_SOURCE_TYPES
        ]
        checklist_ids = [
            source.chunk_id
            for source in request.sources
            if source.source_type.casefold()
            in {
                "equipment_manual",
                "component_manual",
                "public_law",
                "public_guide",
            }
        ]
        return (
            f"{common}\nstructured_answer keys: answer_type=maintenance_guide, summary "
            "(status, risk_level, risk_basis, core_warning), pre_checks, hazards(maximum 3), "
            "manual_steps, stop_conditions, related_regulations_and_incidents, "
            "evidence_chunk_ids, conflicts, additional_information_needed. "
            "Keep each content under 120 Korean characters. Do not copy long manual paragraphs. "
            "Allowed status: 안전관리자 확인 필요, 작업 중지 권고, 근거 부족. "
            "Allowed risk_level: 낮음, 보통, 높음, 매우 높음, 판단 불가. "
            "risk_basis is a list of evidence-backed items and every item must cite retrieved "
            "chunk IDs. If no verified risk basis exists, risk_level must be 판단 불가 and "
            "risk_basis must be []. "
            "Each hazard item must be exactly {name, content, evidence_chunk_ids}; "
            "do not put id, sequence, is_required, is_completed, completed_by_user_id, "
            "or completed_at inside structured_answer. "
            "additional_information_needed must be a list of plain strings, not objects. "
            "Only add a conflict when two or more retrieved chunks directly disagree, and "
            "cite every conflicting chunk ID. Otherwise conflicts must be []. "
            "Top-level checklist_items may contain only question-relevant TBM preview items "
            "with content and evidence_chunk_ids. Do not create a checklist item without evidence. "
            f"Only these chunk IDs may support checklist_items: {checklist_ids}. "
            "Never put checklist text inside answer as [ ]. "
            f"Only these manual chunk IDs may support manual_steps: {manual_ids}. "
            "If that list is empty, manual_steps must be [] and risk may be 판단 불가."
        )

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
            lines.extend(
                [
                    f"[{index}] title: {source.title}",
                    f"[{index}] chunk_id: {source.chunk_id}",
                    f"[{index}] scope/type: {source.document_scope or 'unknown'} / {source.source_type}",
                    f"[{index}] location: {location}",
                    f"[{index}] evidence: {source.excerpt}",
                ]
            )
        return "\n".join(lines)
