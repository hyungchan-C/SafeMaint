import hashlib
import json
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.services.public_packages import (
    PACKAGE_SCHEMA_VERSION,
    PublicPackageError,
    verify_public_package,
)


def canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def package_fixture(
    path: Path,
    *,
    document_type: str = "public_incident",
    tamper_chunks: bool = False,
) -> bytes:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    document_bytes = canonical(
        {
            "source_document_id": "doc-1",
            "external_id": "incident-1",
            "title": "Test incident",
            "source_type": "incident",
            "document_type_code": document_type,
            "metadata": {},
        }
    ) + b"\n"
    chunk_bytes = canonical(
        {
            "source_document_id": "doc-1",
            "source_chunk_id": "chunk-1",
            "chunk_index": 0,
            "content": "Lock out all hazardous energy.",
            "content_hash": hashlib.sha256(b"Lock out all hazardous energy.").hexdigest(),
            "metadata": {},
            "embedding": [1.0, 0.0, 0.0],
            "embedding_model": "test/model",
            "embedding_dimension": 3,
        }
    ) + b"\n"
    manifest = {
        "package_version": "2026.07.16-test",
        "created_at": "2026-07-16T00:00:00+00:00",
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "embedding_model": "test/model",
        "embedding_dimension": 3,
        "sources": [document_type],
        "document_count": 1,
        "chunk_count": 1,
        "previous_version": None,
        "files": {
            "documents.jsonl": hashlib.sha256(document_bytes).hexdigest(),
            "chunks.jsonl": hashlib.sha256(chunk_bytes).hexdigest(),
        },
        "signature_algorithm": "Ed25519",
    }
    manifest_bytes = canonical(manifest)
    signature = private_key.sign(manifest_bytes)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", manifest_bytes)
        archive.writestr("documents.jsonl", document_bytes)
        archive.writestr(
            "chunks.jsonl", chunk_bytes + (b"tampered" if tamper_chunks else b"")
        )
        archive.writestr("signature.ed25519", signature)
    return public_key


def test_signed_public_package_verifies(tmp_path: Path) -> None:
    path = tmp_path / "valid.safemaint-rag.zip"
    key = package_fixture(path)

    verified = verify_public_package(
        path,
        key,
        expected_embedding_model="test/model",
        expected_embedding_dimension=3,
    )

    assert verified.manifest["document_count"] == 1
    assert len(verified.chunks) == 1


def test_tampered_public_package_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "tampered.safemaint-rag.zip"
    key = package_fixture(path, tamper_chunks=True)

    with pytest.raises(PublicPackageError, match="checksum"):
        verify_public_package(path, key)


def test_company_document_cannot_enter_public_package(tmp_path: Path) -> None:
    path = tmp_path / "private.safemaint-rag.zip"
    key = package_fixture(path, document_type="company_policy")

    with pytest.raises(PublicPackageError, match="non-public"):
        verify_public_package(path, key)


def test_incompatible_embedding_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "incompatible.safemaint-rag.zip"
    key = package_fixture(path)

    with pytest.raises(PublicPackageError, match="dimension"):
        verify_public_package(path, key, expected_embedding_dimension=1024)
