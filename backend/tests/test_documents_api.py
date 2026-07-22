import asyncio
import hashlib
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from fastapi import UploadFile

from app.api.deps import require_document_upload
from app.api.routes.documents import upload_document
from app.core.config import settings
from app.db.models import (
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


def test_commit_failure_removes_temp_and_final_file(tmp_path: Path) -> None:
    document = _existing_document()
    db = _UploadSession(
        [
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
