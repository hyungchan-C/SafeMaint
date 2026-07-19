import asyncio
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from app.api.deps import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.main import app


def _current_user() -> User:
    user = User(employee_number="EMP-01", name="업로더", status="active")
    user.id = uuid4()
    return user


async def _upload(files: dict, data: dict, *, authenticated: bool = True):
    if authenticated:
        app.dependency_overrides[get_current_user] = _current_user
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

    oversized = b"0" * (settings.document_max_upload_bytes + 1)
    response = asyncio.run(
        _upload(
            _pdf_file(content=oversized),
            {"product_type": "포토센서", "model_name": "BTS"},
        )
    )

    assert response.status_code == 413
