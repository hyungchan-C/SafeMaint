import hashlib
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import (
    require_document_approve,
    require_document_read,
    require_document_upload,
)
from app.core.config import settings
from app.db.models import (
    Document,
    DocumentProcessingJob,
    DocumentType,
    DocumentVersion,
    User,
)
from app.db.session import get_db
from app.schemas.documents import (
    ApproveDocumentResponse,
    UploadDocumentResponse,
    UserDocumentSummary,
)
from app.services.document_approval import (
    DocumentApprovalConflictError,
    DocumentApprovalNotFoundError,
    approve_document_version,
)

router = APIRouter(prefix="/documents", tags=["documents"])

ALLOWED_ACCESS_LEVELS = {"public", "restricted", "private"}
COMPANY_UPLOAD_ACCESS_LEVELS = {"restricted", "private"}
PDF_MAGIC = b"%PDF-"
MAX_PROCESSING_WARNING_LENGTH = 500


def _normalized_form_value(value: str, *, field: str, max_length: int) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > max_length:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{field}은(는) 1자 이상 {max_length}자 이하여야 합니다.",
        )
    return normalized


def _processing_summary(
    version: DocumentVersion,
) -> tuple[str | None, bool, str | None]:
    metadata = dict(version.processing_metadata or {})
    extractor = str(metadata.get("extractor") or "").strip() or None
    fallback_used = bool(metadata.get("fallback_used"))
    raw_warning = metadata.get("fallback_reason")
    processing_warning = (
        str(raw_warning).strip()[:MAX_PROCESSING_WARNING_LENGTH]
        if raw_warning
        else None
    )
    return extractor, fallback_used, processing_warning


@router.get("/mine", response_model=list[UserDocumentSummary])
def list_my_documents(
    current_user: Annotated[User, Depends(require_document_read)],
    db: Annotated[Session, Depends(get_db)],
) -> list[UserDocumentSummary]:
    """Return the latest non-deleted version of each document uploaded by the user."""

    rows = db.execute(
        select(Document, DocumentVersion)
        .join(DocumentVersion, DocumentVersion.document_id == Document.id)
        .where(
            DocumentVersion.uploaded_by_user_id == current_user.id,
            Document.deleted_at.is_(None),
            Document.lifecycle_status != "deleted",
            DocumentVersion.status != "deleted",
        )
        .order_by(Document.updated_at.desc(), DocumentVersion.version_number.desc())
    ).all()
    summaries: list[UserDocumentSummary] = []
    seen_document_ids: set[UUID] = set()
    for document, version in rows:
        if document.id in seen_document_ids:
            continue
        seen_document_ids.add(document.id)
        extractor, fallback_used, processing_warning = _processing_summary(version)
        summaries.append(
            UserDocumentSummary(
                document_id=document.id,
                document_version_id=version.id,
                original_filename=version.original_filename,
                version_number=version.version_number,
                status=version.status,
                is_active=version.is_active,
                extractor=extractor,
                fallback_used=fallback_used,
                processing_warning=processing_warning,
            )
        )
    return summaries


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
    if access_level not in COMPANY_UPLOAD_ACCESS_LEVELS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="회사 문서 업로드는 restricted 또는 private만 허용됩니다.",
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
    document_type = db.scalar(
        select(DocumentType).where(
            DocumentType.code == document_type_code,
            DocumentType.is_active.is_(True),
        )
    )
    if document_type is None or document_type.scope != "company":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="회사 범위의 활성 문서 유형만 업로드할 수 있습니다.",
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
        elif (
            document.document_type_code != document_type_code
            or document.source_type != source_type
            or document.access_level != access_level
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="기존 문서와 문서 유형·출처·접근 등급이 일치하지 않습니다.",
            )

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


@router.post(
    "/{document_id}/versions/{version_id}/approve",
    response_model=ApproveDocumentResponse,
)
def approve_document(
    document_id: UUID,
    version_id: UUID,
    current_user: Annotated[User, Depends(require_document_approve)],
    db: Annotated[Session, Depends(get_db)],
) -> ApproveDocumentResponse:
    try:
        document, version = approve_document_version(
            db,
            document_id=document_id,
            version_id=version_id,
            approved_by=current_user,
        )
    except DocumentApprovalNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    except DocumentApprovalConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error

    return ApproveDocumentResponse(
        document_id=document.id,
        document_version_id=version.id,
        version_number=version.version_number,
        status=version.status,
        is_active=version.is_active,
    )
