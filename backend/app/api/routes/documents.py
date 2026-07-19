import hashlib
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.models import Document, DocumentProcessingJob, DocumentVersion, User
from app.db.session import get_db
from app.schemas.documents import UploadDocumentResponse

router = APIRouter(prefix="/documents", tags=["documents"])

ALLOWED_ACCESS_LEVELS = {"public", "restricted", "private"}


@router.post(
    "/upload",
    response_model=UploadDocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
def upload_document(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    file: Annotated[UploadFile, File()],
    product_type: Annotated[str, Form()],
    model_name: Annotated[str, Form()],
    manufacturer: Annotated[str, Form()] = "오토닉스",
    document_type_code: Annotated[str, Form()] = "equipment_manual",
    source_type: Annotated[str, Form()] = "manual",
    access_level: Annotated[str, Form()] = "restricted",
) -> UploadDocumentResponse:
    if access_level not in ALLOWED_ACCESS_LEVELS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="access_level은 public/restricted/private 중 하나여야 합니다.",
        )
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="PDF 파일만 업로드할 수 있습니다.",
        )

    content = file.file.read()
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

    doc_name = Path(file.filename).stem
    external_id = f"{source_type}:{manufacturer}:{model_name}:{doc_name}"

    document = db.scalar(select(Document).where(Document.external_id == external_id))
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
        original_filename=file.filename,
        stored_filename=stored_filename,
        storage_path=str(storage_path),
        sha256=hashlib.sha256(content).hexdigest(),
        file_size=len(content),
        mime_type=file.content_type or "application/pdf",
        uploaded_by_user_id=current_user.id,
    )
    db.add(version)

    try:
        db.flush()
        db.add(DocumentProcessingJob(document_version_id=version.id))
        db.commit()
    except IntegrityError as error:
        db.rollback()
        storage_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="업로드 요청이 문서 제약 조건과 맞지 않습니다 (document_type_code 등을 확인하세요).",
        ) from error

    return UploadDocumentResponse(
        document_id=document.id,
        document_version_id=version.id,
        version_number=version.version_number,
        status=version.status,
    )
