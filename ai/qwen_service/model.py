from __future__ import annotations

import asyncio
import json
import re
import shutil
import tempfile
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from qwen_service.config import Settings
from qwen_service.schemas import (
    AnswerRequest,
    AnswerResponse,
    ClassifyRequest,
    ClassifyResponse,
    QueryAnalysis,
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
            "SafeMaint 사고유형 분류기입니다. 반드시 JSON만 출력하세요. "
            "사고 과정, 분석 과정, 설명 문장은 출력하지 마세요."
        )
        user_prompt = (
            "Choose one occurrence_type from this label list:\n"
            f"{labels}\n\n"
            "Return JSON exactly like "
            '{"occurrence_type":"label","confidence":0.0}.\n\n'
            f"Work context:\n{self._context_text(request.context)}\n\n"
            f"Question:\n{request.question}"
        )
        text = self._generate(
            system_prompt,
            user_prompt,
            max_new_tokens=self.settings.classify_max_new_tokens,
            disable_adapter=False,
        )
        occurrence_type, confidence = self._parse_classification(text)
        analysis = QueryAnalysis(occurrence_type=occurrence_type)
        return ClassifyResponse(
            occurrence_type=occurrence_type,
            confidence=confidence,
            analysis=analysis,
            model=self.settings.base_model,
        )

    def _answer_sync(self, request: AnswerRequest) -> AnswerResponse:
        evidence = self._evidence_text(request)
        system_prompt = (
            "당신은 SafeMaint AI입니다. 한국어 최종 답변만 출력하세요. "
            "사고 과정, Thinking Process, 분석 과정, 계획, 내부 추론은 절대 출력하지 마세요. "
            "답변은 현재 근거로 가능한 안전관리/TBM 수준과, 근거가 없어 확정할 수 없는 상세 정비 절차를 분리하세요. "
            "사실 주장, 안전 수칙, 법령, 사고사례, 매뉴얼, 수치에는 반드시 [1]처럼 근거 번호를 붙이세요. "
            "제공된 근거만 사용하고, 제조사 PDF/매뉴얼 근거가 없으면 부품 분해 순서, 장력 해제 방법, "
            "체결 토크, 정렬값, 시운전 기준을 임의로 만들지 마세요. "
            "작업을 승인하지 말고 현장 상태 확인, 제조사 매뉴얼 확인, 안전관리자 최종 확인을 요구하세요."
        )
        answer_format = (
            "아래 형식으로 답하세요.\n"
            "1. 작업 판단: 현재 근거로 말할 수 있는 범위와 확정할 수 없는 범위를 구분합니다.\n"
            "2. 주요 위험: 검색 근거에서 확인되는 위험을 씁니다.\n"
            "3. TBM 체크리스트: [ ] 형식으로 작업 전 확인항목 5~8개를 씁니다.\n"
            "4. 작업 중지 기준: 즉시 멈춰야 하는 조건을 씁니다.\n"
            "5. 부족한 근거: 제조사 PDF/매뉴얼에서 추가 확인할 항목을 씁니다.\n"
            "질문이 TBM 또는 체크리스트가 아니어도 정비, 교체, 청소, 점검 질문이면 TBM 체크리스트를 포함하세요. "
            "상세 교체 절차 근거가 없으면 상세 절차를 확정하지 말고, 현재 근거로 가능한 안전 준비와 확인사항을 제시하세요."
        )
        user_prompt = (
            f"{answer_format}\n\n"
            f"작업 정보:\n{self._context_text(request.context)}\n\n"
            f"분석:\n{self._analysis_text(request.analysis)}\n\n"
            f"근거:\n{evidence}\n\n"
            f"질문:\n{request.question}"
        )
        answer = self._generate(
            system_prompt,
            user_prompt,
            max_new_tokens=self.settings.max_new_tokens,
            disable_adapter=True,
        )
        return AnswerResponse(
            answer=self._clean_answer_text(answer),
            model=self.settings.base_model,
        )

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
        with adapter_context:
            with torch.inference_mode():
                output_ids = model.generate(
                    **inputs,
                    max_new_tokens=max_new_tokens,
                    do_sample=False,
                    pad_token_id=tokenizer.eos_token_id,
                )
        generated = output_ids[0][inputs["input_ids"].shape[-1] :]
        return tokenizer.decode(generated, skip_special_tokens=True).strip()

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

    def _parse_classification(self, text: str) -> tuple[str, float | None]:
        parsed = self._extract_json(text)
        if parsed:
            occurrence_type = str(parsed.get("occurrence_type") or "").strip()
            confidence = parsed.get("confidence")
            if occurrence_type:
                try:
                    return occurrence_type, float(confidence)
                except (TypeError, ValueError):
                    return occurrence_type, None
        for label in self.settings.occurrence_labels:
            if label and label in text:
                return label, None
        return "기타", None

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any] | None:
        stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
        candidates = [stripped]
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
                    f"[{index}] scope/type: {source.document_scope or 'unknown'} / {source.source_type}",
                    f"[{index}] location: {location}",
                    f"[{index}] evidence: {source.excerpt}",
                ]
            )
        return "\n".join(lines)
