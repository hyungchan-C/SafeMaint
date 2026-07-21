from __future__ import annotations

import gc
import json
import re
from pathlib import Path
from typing import Any

from vision_service.config import Settings
from vision_service.schemas import CatalogAnalysisResponse, CatalogCandidate, CatalogItem
from vision_service.table_parser import extract_table_rows


CATALOG_PROMPT = """이 이미지는 산업용 장비, 부품 사진 또는 카탈로그 페이지입니다.
이미지, 명판, 표, 주변 설명을 함께 분석하여 아래 JSON 형식으로만 답하세요.
확실하지 않은 값은 null로 두고 추측하지 마세요.
흐리거나 일부만 보이는 각인·모델·규격 문자는 절대 보완하거나 추정하지 마세요.
크기 기준이 없는 사진에서 M 규격, 길이, 피치, 강도 등급을 추정하지 마세요.
{
  "items": [{
    "manufacturer": null,
    "equipment_type": null,
    "model_number": null,
    "component_name": null,
    "description": null,
    "specifications": {},
    "visible_conditions": []
  }]
}
visible_conditions에는 사진에서 직접 관찰되는 상태만 기록하세요."""

UNVERIFIED_TEXT_PATTERN = re.compile(
    r"각인|인쇄|문자|모델|형식|규격|품번|[A-Z]{1,5}[- ]?\d|\bM\d|\d+(?:\.\d+)?\s*(?:mm|ms|V|A)\b",
    flags=re.I,
)


def _json_object(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.S)
        if not match:
            return {}
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def _has_verified_ocr_text(extracted: str | None) -> bool:
    if not extracted:
        return False
    try:
        payload = json.loads(extracted)
    except json.JSONDecodeError:
        return bool(re.sub(r"<[^>]+>", "", extracted).strip())

    def contains_text(value: Any) -> bool:
        if isinstance(value, dict):
            if value.get("block_label") in {"text", "table"}:
                content = value.get("block_content")
                if isinstance(content, str) and re.sub(r"<[^>]+>", "", content).strip():
                    return True
            return any(contains_text(item) for item in value.values())
        if isinstance(value, list):
            return any(contains_text(item) for item in value)
        return False

    return contains_text(payload)


def _remove_unverified_text_claims(items: list[CatalogItem]) -> list[CatalogItem]:
    sanitized: list[CatalogItem] = []
    for item in items:
        visible = [
            condition
            for condition in item.visible_conditions
            if not UNVERIFIED_TEXT_PATTERN.search(condition)
        ]
        sanitized.append(
            item.model_copy(
                update={
                    "manufacturer": None,
                    "model_number": None,
                    "specifications": {},
                    "visible_conditions": visible,
                }
            )
        )
    return sanitized


class CatalogAnalyzer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._qwen_model: Any = None
        self._qwen_processor: Any = None
        self._paddle_pipeline: Any = None

    def _load_qwen(self) -> tuple[Any, Any]:
        if self._qwen_model is not None and self._qwen_processor is not None:
            return self._qwen_model, self._qwen_processor

        import torch
        from transformers import AutoProcessor, BitsAndBytesConfig, Qwen3VLForConditionalGeneration

        kwargs: dict[str, Any] = {
            "cache_dir": self.settings.model_cache_dir,
            "device_map": "auto" if self.settings.device == "cuda" else "cpu",
            "dtype": "auto",
        }
        if self.settings.load_in_4bit and self.settings.device == "cuda":
            kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_compute_dtype=torch.float16,
                bnb_4bit_quant_type="nf4",
            )
        self._qwen_model = Qwen3VLForConditionalGeneration.from_pretrained(
            self.settings.qwen_model,
            **kwargs,
        )
        self._qwen_processor = AutoProcessor.from_pretrained(
            self.settings.qwen_model,
            cache_dir=self.settings.model_cache_dir,
        )
        return self._qwen_model, self._qwen_processor

    def _analyze_with_qwen(self, image_path: Path) -> str:
        from qwen_vl_utils import process_vision_info

        model, processor = self._load_qwen()
        messages = [{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "image": str(image_path),
                    "max_pixels": self.settings.qwen_max_pixels,
                },
                {"type": "text", "text": CATALOG_PROMPT},
            ],
        }]
        prompt = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        images, videos = process_vision_info(messages)
        inputs = processor(
            text=[prompt], images=images, videos=videos, padding=True, return_tensors="pt"
        ).to(model.device)
        generated = model.generate(**inputs, max_new_tokens=self.settings.max_new_tokens)
        trimmed = [output[len(source):] for source, output in zip(inputs.input_ids, generated)]
        return processor.batch_decode(trimmed, skip_special_tokens=True)[0].strip()

    def rerank_catalog_candidates(
        self,
        field_image: Path,
        candidates: list[CatalogCandidate],
        candidate_paths: list[Path],
        fallback_category: str | None = None,
    ) -> list[CatalogCandidate]:
        """Use the already-local VLM as a conservative semantic gate after coarse search."""
        if not candidates or not self.settings.enable_qwen:
            return []
        from qwen_vl_utils import process_vision_info

        model, processor = self._load_qwen()
        content: list[dict[str, Any]] = [{
            "type": "image",
            "image": str(field_image),
            "max_pixels": self.settings.qwen_max_pixels,
        }]
        content.extend({
            "type": "image",
            "image": str(path),
            "max_pixels": self.settings.qwen_max_pixels,
        } for path in candidate_paths)
        content.append({
            "type": "text",
            "text": (
                "Image 0 is a field photo. Images 1..N are catalog candidates in order. "
                "Reject maps, certificates, logos, covers, and unrelated objects. Select only candidates "
                "that visibly show the same object category. Shape differences should lower relevance, not force rejection. "
                "For each selected image, describe only the visible generic product category and 1-3 visible features. "
                "Do not identify model, size, material grade, or unreadable markings. "
                "Return JSON only: {\"matches\":[{\"index\":1,\"relevance\":0.0,"
                "\"category\":\"generic product type\",\"features\":[\"visible feature\"]}]}. "
                "Use relevance >= 0.55 when the object category agrees; use >= 0.80 only when shape also agrees."
            ),
        })
        messages = [{"role": "user", "content": content}]
        prompt = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        images, videos = process_vision_info(messages)
        inputs = processor(text=[prompt], images=images, videos=videos, padding=True, return_tensors="pt").to(model.device)
        generated = model.generate(**inputs, max_new_tokens=128)
        trimmed = [output[len(source):] for source, output in zip(inputs.input_ids, generated)]
        parsed = _json_object(processor.batch_decode(trimmed, skip_special_tokens=True)[0])
        matches = parsed.get("matches", []) if isinstance(parsed.get("matches"), list) else []
        accepted: list[CatalogCandidate] = []
        for match in matches:
            if not isinstance(match, dict):
                continue
            try:
                index = int(match["index"]) - 1
                relevance = float(match["relevance"])
            except (KeyError, TypeError, ValueError):
                continue
            if not 0 <= index < len(candidates) or relevance < 0.55:
                continue
            original = candidates[index]
            category = str(match.get("category") or "").strip()[:120] or None
            raw_features = match.get("features") if isinstance(match.get("features"), list) else []
            features = [str(value).strip()[:160] for value in raw_features if str(value).strip()][:3]
            if category and UNVERIFIED_TEXT_PATTERN.search(category):
                category = None
            features = [value for value in features if not UNVERIFIED_TEXT_PATTERN.search(value)]
            combined = round(min(1.0, original.similarity * 0.35 + relevance * 0.65), 4)
            confidence = "높음" if combined >= 0.90 else "보통" if combined >= 0.82 else "낮음"
            accepted.append(original.model_copy(update={
                "similarity": combined,
                "confidence": confidence,
                "note": "로컬 외형 검색과 Qwen3-VL 의미 검토를 통과한 후보이며 동일 모델 확정은 아닙니다.",
                "visual_category": category,
                "visual_features": features,
            }))
        if accepted:
            return sorted(accepted, key=lambda item: item.similarity, reverse=True)[:3]

        # Embedding similarity alone is not sufficient to prove that the object type
        # matches (for example, bearings and screws can share a circular silhouette).
        # If the local VLM does not accept a candidate, showing none is safer than
        # filling the UI with misleading low-confidence cards.
        return []

    def _load_paddle(self) -> Any:
        if self._paddle_pipeline is None:
            from paddleocr import PaddleOCRVL

            self._paddle_pipeline = PaddleOCRVL(
                pipeline_version="v1",
                device=self.settings.paddle_device,
            )
        return self._paddle_pipeline

    @staticmethod
    def _paddle_markdown(result: Any) -> str:
        for name in ("markdown", "markdown_text"):
            value = getattr(result, name, None)
            if isinstance(value, str):
                return value
        payload = getattr(result, "json", None)
        if isinstance(payload, dict):
            return json.dumps(payload, ensure_ascii=False)
        return str(result)

    def _analyze_with_paddle(self, image_path: Path) -> str:
        results = self._load_paddle().predict(str(image_path))
        return "\n\n".join(self._paddle_markdown(result) for result in results)

    def analyze(
        self,
        image_path: Path,
        filename: str,
        catalog_candidates: list[Any] | None = None,
        *,
        include_ocr: bool = True,
    ) -> CatalogAnalysisResponse:
        warnings: list[str] = []
        models: list[str] = []
        markdown: str | None = None
        visual: str | None = None

        if self.settings.enable_paddle and include_ocr:
            try:
                markdown = self._analyze_with_paddle(image_path)
                models.append(self.settings.paddle_model)
            except Exception as exc:
                warnings.append(
                    f"PaddleOCR-VL 분석 실패: {type(exc).__name__}: {str(exc)[:200]}"
                )

        if self.settings.enable_qwen:
            try:
                visual = self._analyze_with_qwen(image_path)
                models.append(self.settings.qwen_model)
            except Exception as exc:
                warnings.append(
                    f"Qwen3-VL 분석 실패: {type(exc).__name__}: {str(exc)[:200]}"
                )

        parsed = _json_object(visual or "")
        raw_items = parsed.get("items", []) if isinstance(parsed.get("items", []), list) else []
        items = [CatalogItem.model_validate(item) for item in raw_items if isinstance(item, dict)]
        verified_ocr_text = _has_verified_ocr_text(markdown)
        if not verified_ocr_text:
            items = _remove_unverified_text_claims(items)
            if visual:
                visual = json.dumps(
                    {"items": [item.model_dump() for item in items]},
                    ensure_ascii=False,
                )
            warnings.append(
                "OCR로 확인된 문자가 없어 각인·모델·규격 값은 확정하지 않았습니다."
            )
        items = [item for item in items if any(item.model_dump().values())]
        table_rows = extract_table_rows(markdown)
        if not items and not table_rows and not warnings:
            warnings.append("구조화된 카탈로그 항목을 찾지 못했습니다.")
        gc.collect()
        return CatalogAnalysisResponse(
            filename=filename,
            items=items,
            extracted_markdown=markdown,
            table_rows=table_rows,
            raw_visual_description=visual,
            warnings=warnings,
            models=models,
            catalog_candidates=catalog_candidates or [],
        )
