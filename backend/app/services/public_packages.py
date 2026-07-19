from __future__ import annotations

import base64
import hashlib
import json
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import (
    AuditEvent,
    Document,
    DocumentChunk,
    DocumentType,
    PublicRagPackage,
)


PACKAGE_SCHEMA_VERSION = "0003_secure_documents"
PACKAGE_FILES = {"manifest.json", "documents.jsonl", "chunks.jsonl", "signature.ed25519"}


class PublicPackageError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class VerifiedPackage:
    manifest: dict[str, Any]
    documents: list[dict[str, Any]]
    chunks: list[dict[str, Any]]
    manifest_sha256: str


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _json_lines(values: list[dict[str, Any]]) -> bytes:
    return b"".join(_canonical_json(value) + b"\n" for value in values)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read_key_material(value: str | Path | bytes) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, Path):
        return value.read_bytes()
    stripped = value.strip()
    if not stripped:
        raise PublicPackageError("A package signing or verification key is required.")
    if stripped.startswith("-----BEGIN"):
        return stripped.encode("utf-8")
    path = Path(stripped)
    if path.is_file():
        return path.read_bytes()
    try:
        return base64.b64decode(stripped, validate=True)
    except ValueError as exc:
        raise PublicPackageError("The key is neither PEM, a readable path, nor base64.") from exc


def _private_key(value: str | Path | bytes) -> Ed25519PrivateKey:
    material = _read_key_material(value)
    if material.startswith(b"-----BEGIN"):
        key = serialization.load_pem_private_key(material, password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise PublicPackageError("The private key is not an Ed25519 key.")
        return key
    if len(material) != 32:
        raise PublicPackageError("A raw Ed25519 private key must contain 32 bytes.")
    return Ed25519PrivateKey.from_private_bytes(material)


def _public_key(value: str | Path | bytes) -> Ed25519PublicKey:
    material = _read_key_material(value)
    if material.startswith(b"-----BEGIN"):
        key = serialization.load_pem_public_key(material)
        if not isinstance(key, Ed25519PublicKey):
            raise PublicPackageError("The public key is not an Ed25519 key.")
        return key
    if len(material) != 32:
        raise PublicPackageError("A raw Ed25519 public key must contain 32 bytes.")
    return Ed25519PublicKey.from_public_bytes(material)


def _date_value(value: date | None) -> str | None:
    return value.isoformat() if value else None


def create_public_package(
    session: Session,
    output_path: Path,
    *,
    package_version: str,
    private_key: str | Path,
    previous_version: str | None = None,
) -> dict[str, Any]:
    documents = list(
        session.scalars(
            select(Document)
            .join(DocumentType, DocumentType.code == Document.document_type_code)
            .where(
                DocumentType.scope == "public",
                DocumentType.is_exportable.is_(True),
                Document.lifecycle_status == "active",
                Document.access_level != "private",
            )
            .order_by(Document.external_id)
        )
    )
    document_rows: list[dict[str, Any]] = []
    chunk_rows: list[dict[str, Any]] = []
    dimensions: set[int] = set()
    models: set[str] = set()
    for document in documents:
        document_rows.append(
            {
                "source_document_id": str(document.id),
                "external_id": document.external_id,
                "title": document.title,
                "source_type": document.source_type,
                "document_type_code": document.document_type_code,
                "publisher": document.publisher,
                "source_url": document.source_url,
                "revision": document.revision,
                "published_at": _date_value(document.published_at),
                "access_level": document.access_level,
                "metadata": document.metadata_json,
            }
        )
        chunks = list(
            session.scalars(
                select(DocumentChunk)
                .where(
                    DocumentChunk.document_id == document.id,
                    DocumentChunk.embedding_status == "ready",
                    DocumentChunk.embedding.is_not(None),
                )
                .order_by(DocumentChunk.chunk_index)
            )
        )
        for chunk in chunks:
            if chunk.embedding_dimension is None or chunk.embedding_model is None:
                raise PublicPackageError(f"Chunk {chunk.id} has incomplete embedding metadata.")
            dimensions.add(chunk.embedding_dimension)
            models.add(chunk.embedding_model)
            chunk_rows.append(
                {
                    "source_document_id": str(document.id),
                    "source_chunk_id": str(chunk.id),
                    "chunk_index": chunk.chunk_index,
                    "page_number": chunk.page_number,
                    "page_start": chunk.page_start,
                    "page_end": chunk.page_end,
                    "section_path": chunk.section_path,
                    "content": chunk.content,
                    "content_hash": chunk.content_hash,
                    "metadata": chunk.metadata_json,
                    "embedding": (
                        []
                        if chunk.embedding is None
                        else [float(value) for value in chunk.embedding]
                    ),
                    "embedding_model": chunk.embedding_model,
                    "embedding_dimension": chunk.embedding_dimension,
                }
            )
    if not document_rows or not chunk_rows:
        raise PublicPackageError("No active exportable public documents with ready chunks were found.")
    if len(models) != 1 or len(dimensions) != 1:
        raise PublicPackageError("All exported chunks must use one embedding model and dimension.")

    document_bytes = _json_lines(document_rows)
    chunk_bytes = _json_lines(chunk_rows)
    manifest = {
        "package_version": package_version,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "embedding_model": next(iter(models)),
        "embedding_dimension": next(iter(dimensions)),
        "sources": sorted({row["document_type_code"] for row in document_rows}),
        "document_count": len(document_rows),
        "chunk_count": len(chunk_rows),
        "previous_version": previous_version,
        "files": {
            "documents.jsonl": _sha256(document_bytes),
            "chunks.jsonl": _sha256(chunk_bytes),
        },
        "signature_algorithm": "Ed25519",
    }
    manifest_bytes = _canonical_json(manifest)
    signature = _private_key(private_key).sign(manifest_bytes)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", manifest_bytes)
        archive.writestr("documents.jsonl", document_bytes)
        archive.writestr("chunks.jsonl", chunk_bytes)
        archive.writestr("signature.ed25519", signature)
    return manifest


def _parse_json_lines(value: bytes, filename: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(value.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PublicPackageError(f"Invalid JSON in {filename} line {line_number}.") from exc
        if not isinstance(row, dict):
            raise PublicPackageError(f"{filename} line {line_number} is not an object.")
        rows.append(row)
    return rows


def verify_public_package(
    package_path: Path,
    public_key: str | Path | bytes,
    *,
    expected_schema_version: str = PACKAGE_SCHEMA_VERSION,
    expected_embedding_model: str | None = None,
    expected_embedding_dimension: int | None = None,
) -> VerifiedPackage:
    try:
        with zipfile.ZipFile(package_path, "r") as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or set(names) != PACKAGE_FILES:
                raise PublicPackageError("The package file list is invalid.")
            if any(Path(name).name != name for name in names):
                raise PublicPackageError("Package paths must not contain directories.")
            manifest_bytes = archive.read("manifest.json")
            document_bytes = archive.read("documents.jsonl")
            chunk_bytes = archive.read("chunks.jsonl")
            signature = archive.read("signature.ed25519")
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        raise PublicPackageError("The package is not a valid SafeMaint archive.") from exc
    try:
        manifest = json.loads(manifest_bytes)
    except json.JSONDecodeError as exc:
        raise PublicPackageError("manifest.json is invalid.") from exc
    if _canonical_json(manifest) != manifest_bytes:
        raise PublicPackageError("manifest.json is not in canonical form.")
    try:
        _public_key(public_key).verify(signature, manifest_bytes)
    except Exception as exc:
        raise PublicPackageError("The Ed25519 package signature is invalid.") from exc
    expected_files = manifest.get("files", {})
    if expected_files.get("documents.jsonl") != _sha256(document_bytes):
        raise PublicPackageError("documents.jsonl checksum does not match.")
    if expected_files.get("chunks.jsonl") != _sha256(chunk_bytes):
        raise PublicPackageError("chunks.jsonl checksum does not match.")
    if manifest.get("schema_version") != expected_schema_version:
        raise PublicPackageError("The package database schema is incompatible.")
    if expected_embedding_model and manifest.get("embedding_model") != expected_embedding_model:
        raise PublicPackageError("The package embedding model is incompatible.")
    if expected_embedding_dimension and manifest.get("embedding_dimension") != expected_embedding_dimension:
        raise PublicPackageError("The package embedding dimension is incompatible.")
    documents = _parse_json_lines(document_bytes, "documents.jsonl")
    chunks = _parse_json_lines(chunk_bytes, "chunks.jsonl")
    if manifest.get("document_count") != len(documents) or manifest.get("chunk_count") != len(chunks):
        raise PublicPackageError("Manifest row counts do not match package contents.")
    allowed_types = {row["document_type_code"] for row in documents}
    if not allowed_types or any(not value.startswith("public_") for value in allowed_types):
        raise PublicPackageError("The package contains a non-public document type.")
    document_ids = {row["source_document_id"] for row in documents}
    if any(row.get("source_document_id") not in document_ids for row in chunks):
        raise PublicPackageError("A chunk references a document outside the package.")
    dimension = int(manifest["embedding_dimension"])
    model = str(manifest["embedding_model"])
    for row in chunks:
        if row.get("embedding_model") != model or row.get("embedding_dimension") != dimension:
            raise PublicPackageError("Chunk embedding metadata is inconsistent.")
        if len(row.get("embedding", [])) != dimension:
            raise PublicPackageError("A chunk embedding has the wrong dimension.")
    return VerifiedPackage(
        manifest=manifest,
        documents=documents,
        chunks=chunks,
        manifest_sha256=_sha256(manifest_bytes),
    )


def import_public_package(
    session: Session,
    verified: VerifiedPackage,
    *,
    actor_user_id: UUID | None = None,
    request_id: str | None = None,
) -> PublicRagPackage:
    manifest = verified.manifest
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext('safemaint_public_package'))"))
    if session.scalar(
        select(PublicRagPackage.id).where(
            PublicRagPackage.package_version == manifest["package_version"]
        )
    ):
        raise PublicPackageError("This package version is already installed.")
    active_package = session.scalar(
        select(PublicRagPackage)
        .where(PublicRagPackage.status == "active")
        .with_for_update()
    )
    package = PublicRagPackage(
        package_version=manifest["package_version"],
        schema_version=manifest["schema_version"],
        embedding_model=manifest["embedding_model"],
        embedding_dimension=manifest["embedding_dimension"],
        manifest_sha256=verified.manifest_sha256,
        status="importing",
        imported_by_user_id=actor_user_id,
    )
    session.add(package)
    session.flush()
    id_map: dict[str, UUID] = {}
    for row in verified.documents:
        document = Document(
            external_id=f"public-package:{manifest['package_version']}:{row['external_id']}",
            title=row["title"],
            source_type=row["source_type"],
            document_type_code=row["document_type_code"],
            lifecycle_status="active",
            publisher=row.get("publisher"),
            source_url=row.get("source_url"),
            revision=row.get("revision"),
            published_at=(date.fromisoformat(row["published_at"]) if row.get("published_at") else None),
            access_level="public",
            public_package_id=package.id,
            metadata_json=row.get("metadata") or {},
        )
        session.add(document)
        session.flush()
        id_map[row["source_document_id"]] = document.id
    for row in verified.chunks:
        session.add(
            DocumentChunk(
                id=uuid4(),
                document_id=id_map[row["source_document_id"]],
                chunk_index=row["chunk_index"],
                page_number=row.get("page_number"),
                page_start=row.get("page_start"),
                page_end=row.get("page_end"),
                section_path=row.get("section_path") or [],
                content=row["content"],
                content_hash=row["content_hash"],
                metadata_json=row.get("metadata") or {},
                embedding=row["embedding"],
                embedding_model=row["embedding_model"],
                embedding_dimension=row["embedding_dimension"],
                embedding_status="ready",
            )
        )
    if active_package is not None:
        active_package.status = "superseded"
        session.execute(
            update(Document)
            .where(Document.public_package_id == active_package.id)
            .values(lifecycle_status="deleted", deleted_at=datetime.now(timezone.utc))
        )
    package.status = "active"
    package.activated_at = datetime.now(timezone.utc)
    session.add(
        AuditEvent(
            event_type="PUBLIC_PACKAGE_IMPORTED",
            actor_user_id=actor_user_id,
            entity_type="public_rag_package",
            entity_id=package.id,
            success=True,
            request_id=request_id,
            payload={
                "package_version": package.package_version,
                "document_count": len(verified.documents),
                "chunk_count": len(verified.chunks),
            },
        )
    )
    session.commit()
    session.refresh(package)
    return package


def rollback_public_package(
    session: Session,
    package_version: str,
    *,
    actor_user_id: UUID | None = None,
    request_id: str | None = None,
) -> PublicRagPackage:
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext('safemaint_public_package'))"))
    target = session.scalar(
        select(PublicRagPackage)
        .where(PublicRagPackage.package_version == package_version)
        .with_for_update()
    )
    current = session.scalar(
        select(PublicRagPackage)
        .where(PublicRagPackage.status == "active")
        .with_for_update()
    )
    if target is None or target.status not in {"superseded", "active"}:
        raise PublicPackageError("The requested rollback package is not installed.")
    if current is not None and current.id != target.id:
        current.status = "superseded"
        session.execute(
            update(Document)
            .where(Document.public_package_id == current.id)
            .values(lifecycle_status="deleted", deleted_at=datetime.now(timezone.utc))
        )
    target.status = "active"
    target.activated_at = datetime.now(timezone.utc)
    session.execute(
        update(Document)
        .where(Document.public_package_id == target.id)
        .values(lifecycle_status="active", deleted_at=None)
    )
    session.add(
        AuditEvent(
            event_type="PUBLIC_PACKAGE_ROLLED_BACK",
            actor_user_id=actor_user_id,
            entity_type="public_rag_package",
            entity_id=target.id,
            success=True,
            request_id=request_id,
            payload={"package_version": package_version},
        )
    )
    session.commit()
    return target
