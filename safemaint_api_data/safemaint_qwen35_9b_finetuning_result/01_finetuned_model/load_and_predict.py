from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any


ROOT = Path(__file__).resolve().parent
DEFAULT_MANIFEST_PATH = ROOT / "model_manifest.json"


class ModelConfigurationError(RuntimeError):
    pass


def normalize_label(value: str) -> str:
    value = re.sub(r"<think>.*?</think>", " ", value, flags=re.S)
    value = re.sub(r"\s+", "", value)
    return re.sub(r"[^0-9A-Za-z가-힣]", "", value).lower()


def parse_label(text: str, labels: tuple[str, ...]) -> str:
    normalized = normalize_label(text)
    normalized_labels = {normalize_label(label): label for label in labels}
    if normalized in normalized_labels:
        return normalized_labels[normalized]
    matches = [
        (key, label)
        for key, label in normalized_labels.items()
        if key and key in normalized
    ]
    if not matches:
        raise ValueError(f"라벨 형식이 아닙니다: {text!r}")
    matches.sort(key=lambda item: len(item[0]), reverse=True)
    return matches[0][1]


def _artifact_path(root: Path, configured_path: Any, field: str) -> Path:
    value = str(configured_path or "").strip()
    if not value:
        raise ModelConfigurationError(f"model_manifest.json의 {field}가 비어 있습니다.")
    candidate = (root / value).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ModelConfigurationError(f"{field}는 모델 폴더 밖을 가리킬 수 없습니다.") from exc
    if not candidate.is_dir():
        raise ModelConfigurationError(f"모델 산출물 폴더가 없습니다: {candidate}")
    return candidate


def load_manifest(path: Path = DEFAULT_MANIFEST_PATH) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ModelConfigurationError(f"모델 manifest를 읽을 수 없습니다: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ModelConfigurationError("model_manifest.json이 올바른 JSON이 아닙니다.") from exc
    if not isinstance(manifest, dict):
        raise ModelConfigurationError("model_manifest.json 최상위 값은 객체여야 합니다.")
    labels = manifest.get("labels")
    if not isinstance(labels, list) or not labels or any(
        not isinstance(label, str) or not label.strip() for label in labels
    ):
        raise ModelConfigurationError("model_manifest.json에 유효한 labels가 필요합니다.")
    if len(labels) != len(set(labels)):
        raise ModelConfigurationError("model_manifest.json의 labels가 중복되었습니다.")
    if not str(manifest.get("base_model_id") or "").strip():
        raise ModelConfigurationError("model_manifest.json에 base_model_id가 필요합니다.")
    return manifest


class QwenAccidentClassifier:
    """Explicitly loaded local experiment; importing this module loads no model."""

    def __init__(
        self,
        manifest_path: Path = DEFAULT_MANIFEST_PATH,
        *,
        allow_remote_code: bool = False,
        device_map: str = "auto",
    ) -> None:
        manifest = load_manifest(manifest_path)
        model_root = manifest_path.resolve().parent
        adapter_dir = _artifact_path(
            model_root, manifest.get("adapter_directory"), "adapter_directory"
        )
        tokenizer_dir = _artifact_path(
            model_root, manifest.get("tokenizer_directory"), "tokenizer_directory"
        )
        try:
            import torch
            from peft import PeftModel
            from transformers import (
                AutoModelForMultimodalLM,
                AutoProcessor,
                BitsAndBytesConfig,
            )
        except ImportError as exc:
            raise ModelConfigurationError(
                "Qwen 실험 의존성이 없습니다. requirements.txt를 설치하세요."
            ) from exc

        inference = manifest.get("inference") or {}
        compute_dtype_name = str(manifest.get("compute_dtype") or "bfloat16")
        compute_dtype = getattr(torch, compute_dtype_name, None)
        if compute_dtype is None:
            raise ModelConfigurationError(
                f"지원하지 않는 compute_dtype입니다: {compute_dtype_name}"
            )
        self.labels = tuple(str(label) for label in manifest["labels"])
        self.max_length = int(inference.get("max_length", 1024))
        self.max_new_tokens = int(inference.get("max_new_tokens", 16))
        self.torch = torch
        self.processor = AutoProcessor.from_pretrained(
            tokenizer_dir,
            local_files_only=True,
            trust_remote_code=allow_remote_code,
        )
        self.tokenizer = self.processor.tokenizer
        if self.tokenizer.pad_token_id is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
        quantization_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=compute_dtype,
        )
        base_model = AutoModelForMultimodalLM.from_pretrained(
            str(manifest["base_model_id"]),
            quantization_config=quantization_config,
            torch_dtype=compute_dtype,
            device_map=device_map,
            low_cpu_mem_usage=True,
            trust_remote_code=allow_remote_code,
        )
        self.model = PeftModel.from_pretrained(
            base_model,
            adapter_dir,
            is_trainable=False,
        )
        self.model.eval()

    def classify(self, title: str, accident_text: str) -> str:
        system_prompt = (
            "너는 산업재해 발생형태 분류기다. 입력된 사고 사례를 다음 항목 중 "
            "하나로 분류하고 발생형태 이름만 출력한다: "
            + ", ".join(self.labels)
            + "."
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    f"제목: {title}\n사고 내용: {accident_text}\n\n"
                    "발생형태를 하나만 답하세요."
                ),
            },
        ]
        try:
            prompt = self.tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
                enable_thinking=False,
            )
        except TypeError:
            prompt = self.tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
            )
        inputs = self.tokenizer(
            prompt,
            return_tensors="pt",
            truncation=True,
            max_length=self.max_length,
            add_special_tokens=False,
        ).to(self.model.device)
        with self.torch.inference_mode():
            output = self.model.generate(
                **inputs,
                max_new_tokens=self.max_new_tokens,
                do_sample=False,
                pad_token_id=self.tokenizer.pad_token_id,
                eos_token_id=self.tokenizer.eos_token_id,
            )
        generated = output[:, inputs["input_ids"].shape[1] :]
        raw = self.tokenizer.decode(generated[0], skip_special_tokens=True)
        return parse_label(raw, self.labels)


def main() -> int:
    parser = argparse.ArgumentParser(description="SafeMaint Qwen 사고유형 분류 실험")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--title", required=True)
    parser.add_argument("--text", required=True)
    parser.add_argument("--device-map", default="auto")
    parser.add_argument(
        "--allow-remote-code",
        action="store_true",
        help="검토한 Hugging Face custom code가 꼭 필요할 때만 명시적으로 허용",
    )
    args = parser.parse_args()
    classifier = QwenAccidentClassifier(
        args.manifest,
        allow_remote_code=args.allow_remote_code,
        device_map=args.device_map,
    )
    print(classifier.classify(args.title, args.text))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
