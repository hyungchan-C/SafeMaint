from io import BytesIO

import pytest
from fastapi import HTTPException, UploadFile
from PIL import Image
from starlette.datastructures import Headers

from vision_service.main import _read_limited, _validate_image, match_catalog


def _png(width: int = 4, height: int = 4) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), "white").save(output, format="PNG")
    return output.getvalue()


def test_internal_service_rejects_oversized_upload() -> None:
    upload = UploadFile(filename="large.png", file=BytesIO(b"x" * 6))
    with pytest.raises(HTTPException) as error:
        _read_limited(upload, 5)
    assert error.value.status_code == 413


def test_internal_service_validates_image_bytes() -> None:
    _validate_image(_png())

    with pytest.raises(HTTPException) as error:
        _validate_image(b"not-an-image")
    assert error.value.status_code == 422


def test_internal_service_rejects_non_list_catalog_ids() -> None:
    upload = UploadFile(
        filename="photo.png",
        file=BytesIO(_png()),
        headers=Headers({"content-type": "image/png"}),
    )
    with pytest.raises(HTTPException) as error:
        match_catalog(upload, '{"unexpected": "value"}')
    assert error.value.status_code == 422
