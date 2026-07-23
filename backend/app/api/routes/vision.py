import json
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from httpx import Client, HTTPError, RequestError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    get_current_user,
    get_retrieval_access_scope,
    require_document_read,
    require_document_upload,
)
from app.core.config import settings
from app.db.models import Document, DocumentVersion, User
from app.db.session import get_db
from app.schemas.chat import RetrievalAccessScope


router = APIRouter(prefix="/vision", tags=["vision"])

CATALOG_METADATA_KEY = "vision_catalog_id"
CATALOG_INDEX_VERSION_KEY = "vision_catalog_index_version"
PDF_MAGIC = b"%PDF-"
IMAGE_SIGNATURES = (
    b"\xff\xd8\xff",
    b"\x89PNG\r\n\x1a\n",
)


def _detail(response: object, fallback: str) -> str:
    try:
        payload = response.json()  # type: ignore[attr-defined]
        return str(payload.get("detail") or fallback)
    except (AttributeError, TypeError, ValueError):
        return fallback


def _can_access_document(
    document: Document,
    current_user: User,
    access_scope: RetrievalAccessScope,
) -> bool:
    if document.lifecycle_status == "deleted":
        return False
    if document.created_by_user_id == current_user.id:
        return True
    if document.access_level == "public":
        return True
    if document.access_level == "private" or not access_scope.allow_company:
        return False
    return access_scope.all_sites or (
        document.site_id is not None
        and str(document.site_id) in access_scope.site_ids
    )


def _require_accessible_document(
    db: Session,
    document_id: UUID,
    current_user: User,
    access_scope: RetrievalAccessScope,
) -> Document:
    document = db.get(Document, document_id)
    if document is None or not _can_access_document(document, current_user, access_scope):
        # Do not reveal whether an inaccessible document exists.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "문서를 찾을 수 없습니다.")
    return document


def _catalog_id_for_match(document: Document) -> str:
    metadata = document.metadata_json or {}
    catalog_id = str(metadata.get(CATALOG_METADATA_KEY) or "")
    if not catalog_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "선택한 문서의 이미지 인덱스가 준비되지 않았습니다.")
    if not str(metadata.get(CATALOG_INDEX_VERSION_KEY) or ""):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "선택한 문서는 이전 비전 인덱스 형식입니다. 문서 목록에서 비전 재인덱싱을 실행해 주세요.",
        )
    return catalog_id


def _current_catalog_id(document: Document) -> str | None:
    """Return the catalog id only when the document has a current vision index."""
    metadata = document.metadata_json or {}
    catalog_id = str(metadata.get(CATALOG_METADATA_KEY) or "")
    index_version = str(metadata.get(CATALOG_INDEX_VERSION_KEY) or "")
    return catalog_id if catalog_id and index_version else None


def _read_upload(file: UploadFile, *, limit: int, expected: str) -> bytes:
    content = file.file.read(limit + 1)
    if not content:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "빈 파일은 처리할 수 없습니다.")
    if len(content) > limit:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"파일 크기는 {limit} 바이트를 넘을 수 없습니다.",
        )
    if expected == "pdf" and not content.startswith(PDF_MAGIC):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "PDF 파일 헤더가 올바르지 않습니다.")
    if expected == "image" and not (
        content.startswith(IMAGE_SIGNATURES)
        or (content.startswith(b"RIFF") and content[8:12] == b"WEBP")
    ):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "이미지 파일 헤더가 올바르지 않습니다.")
    return content


def _forward_content(
    path: str,
    *,
    filename: str,
    content: bytes,
    content_type: str,
    fallback: str,
    data: dict[str, str] | None = None,
) -> dict[str, object]:
    service_url = settings.vision_service_url.rstrip("/")
    if not service_url:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "비전 서비스가 설정되지 않았습니다. Colab 비전 노트북을 실행한 뒤 출력된 "
            "VISION_SERVICE_URL과 VISION_API_KEY를 .env에 설정하고 backend를 재시작해 주세요.",
        )
    try:
        headers = {"Authorization": f"Bearer {settings.vision_api_key}"} if settings.vision_api_key else None
        with Client(timeout=900.0) as client:
            response = client.post(
                f"{service_url}{path}",
                files={"file": (filename, content, content_type)},
                data=data,
                headers=headers,
            )
        if response.is_error:
            raise HTTPException(response.status_code, _detail(response, fallback))
        payload = response.json()
        if not isinstance(payload, dict):
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, fallback)
        return payload
    except HTTPException:
        raise
    except RequestError as error:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "비전 서비스에 연결할 수 없습니다. Colab 런타임과 ngrok 주소가 살아 있는지 확인하고, "
            "노트북이 출력한 VISION_SERVICE_URL과 VISION_API_KEY를 .env에 반영한 뒤 "
            "backend를 재시작해 주세요.",
        ) from error
    except (HTTPError, OSError, ValueError) as error:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "로컬 비전 서비스에 연결할 수 없습니다.",
        ) from error


@router.get("/catalog/image/{document_id}/{page}/{image_index}")
def catalog_image(
    document_id: UUID,
    page: int,
    image_index: int,
    current_user: Annotated[User, Depends(require_document_read)],
    access_scope: Annotated[RetrievalAccessScope, Depends(get_retrieval_access_scope)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    if page < 1 or image_index < 1:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "후보 이미지를 찾을 수 없습니다.")
    document = _require_accessible_document(db, document_id, current_user, access_scope)
    catalog_id = str((document.metadata_json or {}).get(CATALOG_METADATA_KEY) or "")
    if not catalog_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "카탈로그 인덱스를 찾을 수 없습니다.")
    try:
        headers = {"Authorization": f"Bearer {settings.vision_api_key}"} if settings.vision_api_key else None
        with Client(timeout=30.0) as client:
            response = client.get(
                f"{settings.vision_service_url.rstrip('/')}/v1/catalog/image/"
                f"{catalog_id}/{page}/{image_index}",
                headers=headers,
            )
        if response.is_error:
            raise HTTPException(response.status_code, _detail(response, "후보 이미지를 찾을 수 없습니다."))
        return Response(
            content=response.content,
            media_type=response.headers.get("content-type", "image/jpeg"),
            headers={"Cache-Control": "private, no-store"},
        )
    except HTTPException:
        raise
    except (HTTPError, OSError) as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "로컬 비전 서비스에 연결할 수 없습니다.") from error


@router.post("/catalog/index")
def index_catalog(
    current_user: Annotated[User, Depends(require_document_upload)],
    access_scope: Annotated[RetrievalAccessScope, Depends(get_retrieval_access_scope)],
    db: Annotated[Session, Depends(get_db)],
    document_id: Annotated[UUID, Form()],
) -> dict[str, object]:
    document = _require_accessible_document(db, document_id, current_user, access_scope)
    if document.created_by_user_id != current_user.id and not access_scope.all_sites:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "문서를 찾을 수 없습니다.")
    version = db.scalar(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == document.id)
        .order_by(DocumentVersion.version_number.desc())
        .limit(1)
    )
    if version is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "인덱싱할 문서 버전이 없습니다.")
    try:
        with Path(version.storage_path).open("rb") as source:
            content = source.read(settings.document_max_upload_bytes + 1)
    except OSError as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "저장된 PDF를 읽지 못했습니다.") from error
    if len(content) > settings.document_max_upload_bytes:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "저장된 PDF가 허용 크기를 초과합니다.")
    if not content.startswith(PDF_MAGIC):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "저장된 PDF 헤더가 올바르지 않습니다.")

    payload = _forward_content(
        "/v1/catalog/index",
        filename=version.original_filename,
        content=content,
        content_type="application/pdf",
        fallback="카탈로그 이미지 인덱싱에 실패했습니다.",
    )
    catalog_id = str(payload.pop("catalog_id", ""))
    index_version = str(payload.pop("index_version", ""))
    if not catalog_id:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "비전 서비스가 카탈로그 ID를 반환하지 않았습니다.")
    if not index_version:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "비전 서비스가 인덱스 형식을 반환하지 않았습니다.")
    document.metadata_json = {
        **(document.metadata_json or {}),
        CATALOG_METADATA_KEY: catalog_id,
        CATALOG_INDEX_VERSION_KEY: index_version,
    }
    db.add(document)
    db.commit()
    return {"document_id": str(document.id), **payload}


def _parse_document_ids(value: str) -> list[UUID]:
    try:
        raw_ids = json.loads(value)
    except json.JSONDecodeError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "document_ids는 JSON 배열이어야 합니다.") from error
    if not isinstance(raw_ids, list) or len(raw_ids) > 20:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "document_ids는 최대 20개까지 허용됩니다.")
    try:
        return list(dict.fromkeys(UUID(str(value)) for value in raw_ids))
    except (TypeError, ValueError) as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "문서 ID 형식이 올바르지 않습니다.") from error


@router.post("/catalog/match")
def match_catalog(
    current_user: Annotated[User, Depends(require_document_read)],
    access_scope: Annotated[RetrievalAccessScope, Depends(get_retrieval_access_scope)],
    db: Annotated[Session, Depends(get_db)],
    file: UploadFile = File(...),
    document_ids: str = Form("[]"),
    analysis_mode: str = Form("deep"),
) -> dict[str, object]:
    if analysis_mode not in {"fast", "deep"}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "analysis_mode는 fast 또는 deep이어야 합니다.")
    selected_ids = _parse_document_ids(document_ids)
    catalog_to_document: dict[str, str] = {}
    skipped_documents: list[str] = []
    for document_id in selected_ids:
        document = _require_accessible_document(db, document_id, current_user, access_scope)
        catalog_id = _current_catalog_id(document)
        if catalog_id is None:
            skipped_documents.append(document.title)
            continue
        catalog_to_document[catalog_id] = str(document.id)
    if selected_ids and not catalog_to_document:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "선택한 문서에 최신 비전 인덱스가 없습니다. 문서 목록에서 비전 재인덱싱을 실행해 주세요.",
        )

    content = _read_upload(file, limit=settings.vision_image_max_upload_bytes, expected="image")
    payload = _forward_content(
        "/v1/catalog/match",
        filename=file.filename or "field-image.bin",
        content=content,
        content_type=file.content_type or "application/octet-stream",
        fallback="카탈로그 이미지 비교에 실패했습니다.",
        data={
            "catalog_ids": json.dumps(list(catalog_to_document)),
            "analysis_mode": analysis_mode,
        },
    )
    safe_candidates = []
    candidates = payload.get("catalog_candidates")
    if isinstance(candidates, list):
        for raw_candidate in candidates:
            if not isinstance(raw_candidate, dict):
                continue
            catalog_id = str(raw_candidate.get("catalog_id") or "")
            document_id = catalog_to_document.get(catalog_id)
            if document_id is None:
                continue
            safe_candidate = {key: value for key, value in raw_candidate.items() if key != "catalog_id"}
            safe_candidate["document_id"] = document_id
            safe_candidates.append(safe_candidate)
    payload["catalog_candidates"] = safe_candidates
    if skipped_documents:
        warnings = payload.get("warnings")
        safe_warnings = [str(value) for value in warnings] if isinstance(warnings, list) else []
        names = ", ".join(skipped_documents[:3])
        remainder = len(skipped_documents) - 3
        suffix = f" 외 {remainder}개" if remainder > 0 else ""
        safe_warnings.append(
            f"구형 비전 인덱스 문서 {len(skipped_documents)}개를 검색에서 제외했습니다: {names}{suffix}"
        )
        payload["warnings"] = safe_warnings
    return payload


@router.post("/catalog/analyze")
def analyze_catalog(
    _current_user: Annotated[User, Depends(get_current_user)],
    file: UploadFile = File(...),
) -> dict[str, object]:
    content = _read_upload(file, limit=settings.vision_image_max_upload_bytes, expected="image")
    return _forward_content(
        "/v1/catalog/analyze",
        filename=file.filename or "field-image.bin",
        content=content,
        content_type=file.content_type or "application/octet-stream",
        fallback="로컬 이미지 분석에 실패했습니다.",
    )
