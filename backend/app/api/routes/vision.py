from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from httpx import Client, HTTPError

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.models import User


router = APIRouter(prefix="/vision", tags=["vision"])


def _detail(response: object, fallback: str) -> str:
    try:
        payload = response.json()  # type: ignore[attr-defined]
        return str(payload.get("detail") or fallback)
    except (AttributeError, TypeError, ValueError):
        return fallback


@router.get("/catalog/image/{catalog_id}/{page}/{image_index}")
def catalog_image(
    catalog_id: str,
    page: int,
    image_index: int,
    _current_user: Annotated[User, Depends(get_current_user)],
) -> Response:
    try:
        with Client(timeout=30.0) as client:
            response = client.get(
                f"{settings.vision_service_url.rstrip('/')}/v1/catalog/image/"
                f"{catalog_id}/{page}/{image_index}"
            )
        if response.is_error:
            raise HTTPException(response.status_code, _detail(response, "후보 이미지를 찾을 수 없습니다."))
        return Response(
            content=response.content,
            media_type=response.headers.get("content-type", "image/jpeg"),
            headers={"Cache-Control": "private, max-age=3600"},
        )
    except HTTPException:
        raise
    except (HTTPError, OSError) as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "로컬 비전 서비스에 연결할 수 없습니다.") from error


@router.post("/catalog/index")
def index_catalog(
    _current_user: Annotated[User, Depends(get_current_user)],
    file: UploadFile = File(...),
) -> dict[str, object]:
    if (file.content_type or "").lower() != "application/pdf" and not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "PDF 파일만 인덱싱할 수 있습니다.")
    return _forward_file("/v1/catalog/index", file, "카탈로그 이미지 인덱싱에 실패했습니다.")


@router.post("/catalog/match")
def match_catalog(
    _current_user: Annotated[User, Depends(get_current_user)],
    file: UploadFile = File(...),
    catalog_ids: str = Form("[]"),
) -> dict[str, object]:
    return _forward_file(
        "/v1/catalog/match",
        file,
        "카탈로그 이미지 비교에 실패했습니다.",
        data={"catalog_ids": catalog_ids},
    )


@router.post("/catalog/analyze")
def analyze_catalog(
    _current_user: Annotated[User, Depends(get_current_user)],
    file: UploadFile = File(...),
) -> dict[str, object]:
    return _forward_file("/v1/catalog/analyze", file, "로컬 이미지 분석에 실패했습니다.")


def _forward_file(
    path: str,
    file: UploadFile,
    fallback: str,
    data: dict[str, str] | None = None,
) -> dict[str, object]:
    try:
        with Client(timeout=900.0) as client:
            response = client.post(
                f"{settings.vision_service_url.rstrip('/')}{path}",
                files={
                    "file": (
                        file.filename or "upload.bin",
                        file.file,
                        file.content_type or "application/octet-stream",
                    )
                },
                data=data,
            )
        if response.is_error:
            raise HTTPException(response.status_code, _detail(response, fallback))
        return response.json()
    except HTTPException:
        raise
    except (HTTPError, OSError, ValueError) as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "로컬 비전 서비스에 연결할 수 없습니다.") from error
