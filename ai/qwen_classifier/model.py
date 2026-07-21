from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from threading import Lock
from types import ModuleType
from typing import Any


class ModelLoadError(RuntimeError):
    pass


class TeamQwenClassifier:
    """Loads the team-owned loader and serializes GPU inference calls."""

    def __init__(
        self,
        model_root: Path,
        *,
        allow_remote_code: bool = False,
        device_map: str = "auto",
    ) -> None:
        self.model_root = model_root.resolve()
        self.manifest_path = self.model_root / "model_manifest.json"
        self.loader_path = self.model_root / "load_and_predict.py"
        self.allow_remote_code = allow_remote_code
        self.device_map = device_map
        self._load_lock = Lock()
        self._inference_lock = Lock()
        self._classifier: Any | None = None
        self._error: str | None = None
        self._manifest = self._read_manifest()

    @property
    def model_id(self) -> str:
        return str(self._manifest["base_model_id"])

    @property
    def adapter_name(self) -> str:
        return str(self._manifest["adapter_directory"])

    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(str(value) for value in self._manifest["labels"])

    @property
    def ready(self) -> bool:
        return self._classifier is not None

    @property
    def error(self) -> str | None:
        return self._error

    def _read_manifest(self) -> dict[str, Any]:
        if not self.manifest_path.is_file():
            raise ModelLoadError(
                f"Team model manifest is missing: {self.manifest_path}"
            )
        if not self.loader_path.is_file():
            raise ModelLoadError(
                f"Team model loader is missing: {self.loader_path}"
            )
        try:
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ModelLoadError("Team model manifest is invalid.") from exc
        if not str(manifest.get("base_model_id") or "").strip():
            raise ModelLoadError(
                "Team model manifest field is empty: base_model_id"
            )
        for field in ("adapter_directory", "tokenizer_directory"):
            value = str(manifest.get(field) or "").strip()
            if not value:
                raise ModelLoadError(f"Team model manifest field is empty: {field}")
            artifact = (self.model_root / value).resolve()
            try:
                artifact.relative_to(self.model_root)
            except ValueError as exc:
                raise ModelLoadError(
                    f"Team model artifact escapes its root: {field}"
                ) from exc
            if not artifact.is_dir():
                raise ModelLoadError(f"Team model artifact is missing: {field}")
        labels = manifest.get("labels")
        if not isinstance(labels, list) or not labels:
            raise ModelLoadError("Team model labels are missing.")
        return manifest

    def _import_loader(self) -> ModuleType:
        spec = importlib.util.spec_from_file_location(
            "safemaint_team_qwen_loader", self.loader_path
        )
        if spec is None or spec.loader is None:
            raise ModelLoadError("Team model loader cannot be imported.")
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            raise ModelLoadError("Team model loader import failed.") from exc
        return module

    def load(self) -> None:
        if self._classifier is not None:
            return
        with self._load_lock:
            if self._classifier is not None:
                return
            if self._error:
                raise ModelLoadError(self._error)
            try:
                module = self._import_loader()
                classifier_type = getattr(module, "QwenAccidentClassifier", None)
                if classifier_type is None:
                    raise ModelLoadError(
                        "QwenAccidentClassifier is missing from the team loader."
                    )
                self._classifier = classifier_type(
                    self.manifest_path,
                    allow_remote_code=self.allow_remote_code,
                    device_map=self.device_map,
                )
            except Exception as exc:
                self._error = str(exc) or type(exc).__name__
                raise ModelLoadError(self._error) from exc

    def classify(self, title: str, text: str) -> str:
        self.load()
        with self._inference_lock:
            label = str(self._classifier.classify(title, text)).strip()
        if label not in self.labels:
            raise ModelLoadError("Team model returned an unknown accident label.")
        return label
