from io import BytesIO
from uuid import uuid4

import pytest
from fastapi import HTTPException, UploadFile

from app.api.routes.vision import (
    _current_catalog_id,
    _forward_file,
    _parse_document_ids,
    _read_upload,
)
from app.api.routes import vision as vision_routes
from app.db.models import Document, User
from app.schemas.chat import RetrievalAccessScope
from app.services.document_access import can_access_document


def _user() -> User:
    user = User(employee_number="VISION-01", name="비전 사용자", status="active")
    user.id = uuid4()
    return user


def _document(owner_id=None, *, access_level: str = "restricted") -> Document:
    document = Document(
        external_id=f"manual:{uuid4()}",
        title="설비 매뉴얼",
        source_type="manual",
        document_type_code="equipment_manual",
        access_level=access_level,
        lifecycle_status="active",
        created_by_user_id=owner_id,
    )
    document.id = uuid4()
    return document


def test_private_catalog_is_accessible_only_to_its_owner() -> None:
    owner = _user()
    stranger = _user()
    document = _document(owner.id, access_level="private")
    scope = RetrievalAccessScope(allow_company=True, all_sites=True)

    assert can_access_document(document, owner, scope)
    assert not can_access_document(document, stranger, scope)


def test_restricted_catalog_requires_global_or_assigned_site_access() -> None:
    user = _user()
    document = _document(uuid4())
    document.site_id = uuid4()

    assert not can_access_document(
        document,
        user,
        RetrievalAccessScope(allow_company=True, site_ids=[]),
    )
    assert can_access_document(
        document,
        user,
        RetrievalAccessScope(allow_company=True, site_ids=[str(document.site_id)]),
    )
    assert can_access_document(
        document,
        user,
        RetrievalAccessScope(allow_company=True, all_sites=True),
    )


def test_catalog_document_ids_are_bounded_and_validated() -> None:
    document_id = uuid4()
    assert _parse_document_ids(f'["{document_id}"]') == [document_id]

    with pytest.raises(HTTPException) as error:
        _parse_document_ids("not-json")
    assert error.value.status_code == 422

    with pytest.raises(HTTPException) as error:
        _parse_document_ids(str([str(uuid4())] * 21).replace("'", '"'))
    assert error.value.status_code == 422


def test_vision_upload_rejects_oversize_and_spoofed_images() -> None:
    oversized = UploadFile(filename="photo.png", file=BytesIO(b"x" * 6))
    with pytest.raises(HTTPException) as error:
        _read_upload(oversized, limit=5, expected="image")
    assert error.value.status_code == 413

    spoofed = UploadFile(filename="photo.png", file=BytesIO(b"not-an-image"))
    with pytest.raises(HTTPException) as error:
        _read_upload(spoofed, limit=100, expected="image")
    assert error.value.status_code == 422


def test_stored_pdf_is_forwarded_as_file_stream(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    path = tmp_path / "catalog.pdf"
    path.write_bytes(b"%PDF-streamed")
    captured: dict[str, object] = {}

    class FakeResponse:
        is_error = False

        @staticmethod
        def json() -> dict[str, str]:
            return {"catalog_id": "streamed"}

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            pass

        @staticmethod
        def post(_url, *, files, **_kwargs):
            captured["file"] = files["file"][1]
            captured["content"] = files["file"][1].read()
            return FakeResponse()

    monkeypatch.setattr(vision_routes, "Client", FakeClient)

    result = _forward_file(
        "/v1/catalog/index",
        filename="catalog.pdf",
        file_path=path,
        content_type="application/pdf",
        fallback="failed",
    )

    assert result == {"catalog_id": "streamed"}
    assert captured["content"] == b"%PDF-streamed"
    assert not isinstance(captured["file"], bytes)
    assert captured["file"].closed is True  # type: ignore[union-attr]


def test_current_vision_catalog_can_be_matched() -> None:
    document = _document()
    document.metadata_json = {
        "vision_catalog_id": "siglip-index",
        "vision_catalog_index_version": "safemaint-matrix-v4:google/siglip2-base-patch16-naflex",
    }

    assert _current_catalog_id(document) == "siglip-index"


def test_legacy_vision_catalog_can_be_skipped_when_other_catalogs_are_current() -> None:
    document = _document()
    document.metadata_json = {"vision_catalog_id": "legacy-efficientnet-index"}

    assert _current_catalog_id(document) is None
