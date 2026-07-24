import asyncio
import hashlib
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from fastapi import HTTPException, UploadFile

from app.api.deps import require_document_upload
from app.api.routes.documents import delete_document, upload_document
from app.core.config import settings
from app.db.models import (
    AuditEvent,
    Document,
    DocumentProcessingJob,
    DocumentType,
    DocumentVersion,
    User,
)
from app.db.session import get_db
from app.main import app


def _current_user() -> User:
    user = User(employee_number="EMP-01", name="업로더", status="active")
    user.id = uuid4()
    return user


async def _upload(files: dict, data: dict, *, authenticated: bool = True):
    if authenticated:
        app.dependency_overrides[require_document_upload] = _current_user
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/api/v1/documents/upload", files=files, data=data
            )
    finally:
        app.dependency_overrides.clear()


def _pdf_file(name: str = "manual.pdf", content: bytes = b"%PDF-1.4 fake") -> dict:
    return {"file": (name, content, "application/pdf")}


@pytest.fixture(autouse=True)
def _isolated_document_storage(tmp_path: Path):
    original = settings.document_storage_dir
    object.__setattr__(settings, "document_storage_dir", str(tmp_path))
    try:
        yield
    finally:
        object.__setattr__(settings, "document_storage_dir", original)


def test_upload_requires_authentication() -> None:
    response = asyncio.run(
        _upload(
            _pdf_file(),
            {"product_type": "포토센서", "model_name": "BTS"},
            authenticated=False,
        )
    )

    assert response.status_code == 401


def test_upload_rejects_non_pdf_filename() -> None:
    response = asyncio.run(
        _upload(
            {"file": ("manual.txt", b"not a pdf", "text/plain")},
            {"product_type": "포토센서", "model_name": "BTS"},
        )
    )

    assert response.status_code == 422


def test_upload_rejects_pdf_extension_with_invalid_header() -> None:
    response = asyncio.run(
        _upload(
            _pdf_file(content=b"not really a pdf"),
            {"product_type": "포토센서", "model_name": "BTS"},
        )
    )

    assert response.status_code == 422


def test_upload_rejects_invalid_access_level() -> None:
    response = asyncio.run(
        _upload(
            _pdf_file(),
            {
                "product_type": "포토센서",
                "model_name": "BTS",
                "access_level": "top-secret",
            },
        )
    )

    assert response.status_code == 422


def test_upload_rejects_public_access_level() -> None:
    response = asyncio.run(
        _upload(
            _pdf_file(),
            {
                "product_type": "포토센서",
                "model_name": "BTS",
                "access_level": "public",
            },
        )
    )

    assert response.status_code == 422


def test_upload_rejects_empty_file() -> None:
    response = asyncio.run(
        _upload(
            _pdf_file(content=b""),
            {"product_type": "포토센서", "model_name": "BTS"},
        )
    )

    assert response.status_code == 422


def test_upload_rejects_oversized_file() -> None:
    from app.core.config import settings

    original_limit = settings.document_max_upload_bytes
    object.__setattr__(settings, "document_max_upload_bytes", 32)
    try:
        oversized = b"%PDF-" + (b"0" * 28)
        response = asyncio.run(
            _upload(
                _pdf_file(content=oversized),
                {"product_type": "포토센서", "model_name": "BTS"},
            )
        )
    finally:
        object.__setattr__(settings, "document_max_upload_bytes", original_limit)

    assert response.status_code == 413


class _UploadSession:
    def __init__(self, scalar_values: list[object], *, commit_error=None) -> None:
        self.scalar_values = iter(scalar_values)
        self.added: list[object] = []
        self.commit_error = commit_error
        self.rollback_calls = 0

    def scalar(self, _statement):
        return next(self.scalar_values)

    def add(self, item: object) -> None:
        self.added.append(item)

    def flush(self) -> None:
        for item in self.added:
            if hasattr(item, "id") and getattr(item, "id", None) is None:
                item.id = uuid4()
            if isinstance(item, DocumentVersion) and item.status is None:
                item.status = "pending"

    def commit(self) -> None:
        if self.commit_error is not None:
            raise self.commit_error

    def rollback(self) -> None:
        self.rollback_calls += 1


def _existing_document() -> Document:
    return Document(
        id=uuid4(),
        external_id="manual:오토닉스:BTS:manual",
        title="manual",
        source_type="manual",
        document_type_code="equipment_manual",
        access_level="restricted",
    )


def test_successful_upload_persists_streamed_size_hash_and_job(tmp_path: Path) -> None:
    content = b"%PDF-1.4\nstreamed upload\n%%EOF"
    document = _existing_document()
    db = _UploadSession(
        [
            None,  # 중복 파일 검사(sha256 일치하는 기존 버전 없음)
            DocumentType(
                code="equipment_manual",
                name="Equipment manual",
                scope="company",
                is_active=True,
            ),
            document,
            1,
        ]
    )
    original = settings.document_storage_dir
    object.__setattr__(settings, "document_storage_dir", str(tmp_path))
    try:
        response = upload_document(
            current_user=_current_user(),
            db=db,  # type: ignore[arg-type]
            file=UploadFile(filename="manual.pdf", file=BytesIO(content)),
            product_type="sensor",
            model_name="BTS",
        )
    finally:
        object.__setattr__(settings, "document_storage_dir", original)

    version = next(item for item in db.added if isinstance(item, DocumentVersion))
    job = next(item for item in db.added if isinstance(item, DocumentProcessingJob))
    stored_path = Path(version.storage_path)
    assert response.version_number == 2
    assert version.file_size == len(content) == stored_path.stat().st_size
    assert version.sha256 == hashlib.sha256(content).hexdigest()
    assert stored_path.read_bytes() == content
    assert job.document_version_id == version.id
    assert list(tmp_path.glob(".pdf-upload-*.part")) == []


def test_upload_rejects_exact_duplicate_content(tmp_path: Path) -> None:
    content = b"%PDF-1.4\nduplicate\n%%EOF"
    document = _existing_document()
    existing_version = DocumentVersion(
        id=uuid4(),
        document_id=document.id,
        document=document,
        version_number=1,
        original_filename="manual.pdf",
        stored_filename="already-stored.pdf",
        storage_path=str(tmp_path / "already-stored.pdf"),
        sha256=hashlib.sha256(content).hexdigest(),
        file_size=len(content),
        mime_type="application/pdf",
        status="active",
    )
    db = _UploadSession([existing_version])

    with pytest.raises(HTTPException) as exc_info:
        upload_document(
            current_user=_current_user(),
            db=db,  # type: ignore[arg-type]
            # 파일명이 달라도(재업로드 시 흔한 실수) 내용(sha256)이 같으면 막아야 한다.
            file=UploadFile(filename="manual-renamed.pdf", file=BytesIO(content)),
            product_type="sensor",
            model_name="BTS",
        )

    assert exc_info.value.status_code == 409
    # 중복으로 걸렸으므로 새 버전/처리작업이 추가로 만들어지면 안 된다.
    assert not any(isinstance(item, DocumentVersion) for item in db.added)
    assert not any(isinstance(item, DocumentProcessingJob) for item in db.added)


def test_commit_failure_removes_temp_and_final_file(tmp_path: Path) -> None:
    document = _existing_document()
    db = _UploadSession(
        [
            None,  # 중복 파일 검사(sha256 일치하는 기존 버전 없음)
            DocumentType(
                code="equipment_manual",
                name="Equipment manual",
                scope="company",
                is_active=True,
            ),
            document,
            0,
        ],
        commit_error=RuntimeError("simulated commit failure"),
    )
    original = settings.document_storage_dir
    object.__setattr__(settings, "document_storage_dir", str(tmp_path))
    try:
        with pytest.raises(RuntimeError, match="commit failure"):
            upload_document(
                current_user=_current_user(),
                db=db,  # type: ignore[arg-type]
                file=UploadFile(
                    filename="manual.pdf",
                    file=BytesIO(b"%PDF-1.4\nrollback\n%%EOF"),
                ),
                product_type="sensor",
                model_name="BTS",
            )
    finally:
        object.__setattr__(settings, "document_storage_dir", original)

    assert db.rollback_calls == 1
    assert list(tmp_path.iterdir()) == []


class _DeleteSession:
    def __init__(self, document: Document | None) -> None:
        self.document = document
        self.added: list[object] = []
        self.committed = False

    def get(self, model, object_id):
        if (
            model is Document
            and self.document is not None
            and object_id == self.document.id
        ):
            return self.document
        return None

    def add(self, item: object) -> None:
        self.added.append(item)

    def commit(self) -> None:
        self.committed = True


def test_delete_document_soft_deletes() -> None:
    document = _existing_document()
    db = _DeleteSession(document)

    delete_document(
        document_id=document.id,
        current_user=_current_user(),
        db=db,  # type: ignore[arg-type]
    )

    assert document.lifecycle_status == "deleted"
    assert document.deleted_at is not None
    assert db.committed
    audit_events = [item for item in db.added if isinstance(item, AuditEvent)]
    assert len(audit_events) == 1
    assert audit_events[0].event_type == "document.deleted"


def test_delete_missing_document_returns_404() -> None:
    db = _DeleteSession(None)

    with pytest.raises(HTTPException) as exc_info:
        delete_document(
            document_id=uuid4(),
            current_user=_current_user(),
            db=db,  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 404


def test_delete_already_deleted_document_returns_404() -> None:
    document = _existing_document()
    document.lifecycle_status = "deleted"
    db = _DeleteSession(document)

    with pytest.raises(HTTPException) as exc_info:
        delete_document(
            document_id=document.id,
            current_user=_current_user(),
            db=db,  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 404
