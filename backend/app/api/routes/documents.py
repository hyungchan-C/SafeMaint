import hashlib
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import require_document_upload
from app.core.config import settings
from app.db.models import Document, DocumentProcessingJob, DocumentVersion, User
from app.db.session import get_db
from app.schemas.documents import UploadDocumentResponse

router = APIRouter(prefix="/documents", tags=["documents"])

ALLOWED_ACCESS_LEVELS = {"public", "restricted", "private"}
PDF_MAGIC = b"%PDF-"


def _normalized_form_value(value: str, *, field: str, max_length: int) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > max_length:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{field}은(는) 1자 이상 {max_length}자 이하여야 합니다.",
        )
    return normalized


@router.post(
    "/upload",
    response_model=UploadDocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
def upload_document(
    current_user: Annotated[User, Depends(require_document_upload)],
    db: Annotated[Session, Depends(get_db)],
    file: Annotated[UploadFile, File()],
    product_type: Annotated[str, Form()],
    model_name: Annotated[str, Form()],
    manufacturer: Annotated[str, Form()] = "오토닉스",
    document_type_code: Annotated[str, Form()] = "equipment_manual",
    source_type: Annotated[str, Form()] = "manual",
    access_level: Annotated[str, Form()] = "restricted",
) -> UploadDocumentResponse:
    product_type = _normalized_form_value(
        product_type, field="product_type", max_length=100
    )
    model_name = _normalized_form_value(
        model_name, field="model_name", max_length=100
    )
    manufacturer = _normalized_form_value(
        manufacturer, field="manufacturer", max_length=200
    )
    document_type_code = _normalized_form_value(
        document_type_code, field="document_type_code", max_length=50
    )
    source_type = _normalized_form_value(
        source_type, field="source_type", max_length=50
    )
    access_level = access_level.strip().lower()
    if access_level not in ALLOWED_ACCESS_LEVELS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="access_level은 public/restricted/private 중 하나여야 합니다.",
        )
    original_filename = Path((file.filename or "").replace("\\", "/")).name
    if not original_filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="PDF 파일만 업로드할 수 있습니다.",
        )

    content = file.file.read(settings.document_max_upload_bytes + 1)
    if not content:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="빈 파일은 업로드할 수 없습니다.",
        )
    if len(content) > settings.document_max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"파일 크기는 {settings.document_max_upload_bytes} 바이트를 넘을 수 없습니다.",
        )
    if not content.startswith(PDF_MAGIC):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="PDF 파일 헤더가 올바르지 않습니다.",
        )

    doc_name = _normalized_form_value(
        Path(original_filename).stem, field="파일명", max_length=500
    )
    external_id = f"{source_type}:{manufacturer}:{model_name}:{doc_name}"
    if len(external_id) > 200:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="문서 식별 정보가 너무 깁니다. 제조사·모델명·파일명을 줄여 주세요.",
        )

    storage_path: Path | None = None
    try:
        document = db.scalar(
            select(Document).where(Document.external_id == external_id)
        )
        if document is None:
            document = Document(
                external_id=external_id,
                title=doc_name,
                source_type=source_type,
                document_type_code=document_type_code,
                access_level=access_level,
                created_by_user_id=current_user.id,
                metadata_json={
                    "manufacturer": manufacturer,
                    "model_number": model_name,
                    "product_type": product_type,
                },
            )
            db.add(document)
            db.flush()

        next_version_number = (
            db.scalar(
                select(func.max(DocumentVersion.version_number)).where(
                    DocumentVersion.document_id == document.id
                )
            )
            or 0
        ) + 1

        storage_dir = Path(settings.document_storage_dir)
        storage_dir.mkdir(parents=True, exist_ok=True)
        stored_filename = f"{uuid4().hex}.pdf"
        storage_path = storage_dir / stored_filename
        storage_path.write_bytes(content)

        version = DocumentVersion(
            document_id=document.id,
            version_number=next_version_number,
            original_filename=original_filename,
            stored_filename=stored_filename,
            storage_path=str(storage_path),
            sha256=hashlib.sha256(content).hexdigest(),
            file_size=len(content),
            mime_type="application/pdf",
            uploaded_by_user_id=current_user.id,
        )
        db.add(version)
        db.flush()
        db.add(DocumentProcessingJob(document_version_id=version.id))
        db.commit()
    except IntegrityError as error:
        db.rollback()
        if storage_path is not None:
            storage_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="업로드 요청이 문서 제약 조건과 맞지 않습니다 (document_type_code 등을 확인하세요).",
        ) from error
    except OSError as error:
        db.rollback()
        if storage_path is not None:
            storage_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="문서 저장소에 파일을 저장하지 못했습니다.",
        ) from error
    except Exception:
        db.rollback()
        if storage_path is not None:
            storage_path.unlink(missing_ok=True)
        raise

    return UploadDocumentResponse(
        document_id=document.id,
        document_version_id=version.id,
        version_number=version.version_number,
        status=version.status,
    )
