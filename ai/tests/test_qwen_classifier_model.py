from pathlib import Path

import pytest

from qwen_classifier.model import ModelLoadError, TeamQwenClassifier


def make_fake_model(tmp_path: Path, predicted_label: str = "끼임") -> Path:
    model_root = tmp_path / "model"
    (model_root / "best_adapter").mkdir(parents=True)
    (model_root / "tokenizer").mkdir()
    (model_root / "model_manifest.json").write_text(
        """{
          "base_model_id": "Qwen/Qwen3.5-9B",
          "adapter_directory": "best_adapter",
          "tokenizer_directory": "tokenizer",
          "labels": ["끼임", "화재"]
        }""",
        encoding="utf-8",
    )
    (model_root / "load_and_predict.py").write_text(
        f"""class QwenAccidentClassifier:
    def __init__(self, manifest_path, **kwargs):
        self.manifest_path = manifest_path

    def classify(self, title, accident_text):
        return {predicted_label!r}
""",
        encoding="utf-8",
    )
    return model_root


def test_team_loader_is_used_without_loading_dependencies(tmp_path: Path) -> None:
    model = TeamQwenClassifier(make_fake_model(tmp_path))

    assert model.classify("컨베이어", "롤러 점검") == "끼임"
    assert model.ready is True
    assert model.model_id == "Qwen/Qwen3.5-9B"
    assert model.adapter_name == "best_adapter"


def test_unknown_team_label_is_rejected(tmp_path: Path) -> None:
    model = TeamQwenClassifier(make_fake_model(tmp_path, "알수없음"))

    with pytest.raises(ModelLoadError, match="unknown accident label"):
        model.classify("컨베이어", "롤러 점검")
