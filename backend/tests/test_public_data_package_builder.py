from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BUILDER_PATH = (
    REPOSITORY_ROOT
    / "safemaint_api_data"
    / "safemaint_public_rag_package"
    / "build_public_rag_package.py"
)
QWEN_LOADER_PATH = (
    REPOSITORY_ROOT
    / "safemaint_api_data"
    / "safemaint_qwen35_9b_finetuning_result"
    / "01_finetuned_model"
    / "load_and_predict.py"
)


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


builder = _load_module("safemaint_public_package_builder", BUILDER_PATH)
qwen_loader = _load_module("safemaint_qwen_accident_loader", QWEN_LOADER_PATH)


def test_collector_source_types_map_to_existing_document_types() -> None:
    assert builder.public_source_mapping("domestic_case") == (
        "incident",
        "public_incident",
    )
    assert builder.public_source_mapping("kosha_guide") == (
        "guide",
        "public_guide",
    )
    with pytest.raises(builder.PipelineError, match="매핑되지 않은"):
        builder.public_source_mapping("unknown")


def test_builder_archive_passes_existing_backend_verifier(tmp_path: Path) -> None:
    pytest.importorskip("pgvector")
    from app.services.public_packages import verify_public_package

    private_key = Ed25519PrivateKey.generate()
    private_key_path = tmp_path / "private.key"
    private_key_path.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
    )
    package_path = tmp_path / "public-package.zip"
    documents = [
        {
            "source_document_id": "public:domestic:1",
            "external_id": "public:domestic:1",
            "title": "컨베이어 끼임 사고",
            "source_type": "incident",
            "document_type_code": "public_incident",
            "publisher": "한국산업안전보건공단",
            "source_url": None,
            "revision": None,
            "published_at": None,
            "access_level": "public",
            "metadata": {"source_id": "1"},
        }
    ]
    chunks = [
        {
            "source_document_id": "public:domestic:1",
            "source_chunk_id": "public:chunk:1",
            "chunk_index": 0,
            "page_number": None,
            "page_start": None,
            "page_end": None,
            "section_path": [],
            "content": "가동 전 전원을 차단하고 잠금표지를 적용한다.",
            "content_hash": "test-content-hash",
            "metadata": {"source_id": "1"},
            "embedding": [0.25, 0.75],
            "embedding_model": "test-model",
            "embedding_dimension": 2,
        }
    ]
    manifest = {
        "package_version": "test-1",
        "created_at": "2026-07-20T00:00:00Z",
        "schema_version": builder.PACKAGE_SCHEMA_VERSION,
        "embedding_model": "test-model",
        "embedding_dimension": 2,
        "sources": ["public_incident"],
        "previous_version": None,
    }

    builder.write_signed_archive(
        package_path,
        manifest,
        documents,
        chunks,
        private_key_path,
    )
    verified = verify_public_package(
        package_path,
        private_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        ),
        expected_embedding_model="test-model",
        expected_embedding_dimension=2,
    )

    assert verified.manifest["document_count"] == 1
    assert verified.manifest["chunk_count"] == 1
    assert verified.documents == documents
    assert verified.chunks == chunks


def test_qwen_loader_import_is_lazy_and_labels_are_manifest_driven() -> None:
    manifest_path = QWEN_LOADER_PATH.with_name("model_manifest.json")
    manifest = qwen_loader.load_manifest(manifest_path)
    labels = tuple(manifest["labels"])

    assert qwen_loader.parse_label("분류 결과: 끼임", labels) == "끼임"
    assert "torch" not in qwen_loader.__dict__
