from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import (
    get_retrieval_access_scope,
    require_document_approve,
    require_document_delete,
    require_document_read,
    require_document_upload,
)
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
from app.schemas.chat import RetrievalAccessScope
from app.schemas.documents import (
    ApproveDocumentResponse,
    ReviewQueueDocumentSummary,
    UploadDocumentResponse,
    UserDocumentSummary,
)
from app.services.document_access import require_accessible_document
from app.services.document_approval import (
    DocumentApprovalConflictError,
    DocumentApprovalNotFoundError,
    approve_document_version,
)
from app.services.pdf_storage import (
    EmptyPdfUploadError,
    InvalidPdfHeaderError,
    PdfUploadIOError,
    PdfUploadTooLargeError,
    StagedPdfUpload,
    stage_pdf_upload,
)

router = APIRouter(prefix="/documents", tags=["documents"])

ALLOWED_ACCESS_LEVELS = {"public", "restricted", "private"}
COMPANY_UPLOAD_ACCESS_LEVELS = {"restricted", "private"}
MAX_PROCESSING_WARNING_LENGTH = 500


def _safe_unlink(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


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


def _resolve_document_version(
    db: Session,
    document: Document,
    version_id: UUID | None,
) -> DocumentVersion:
    if version_id is not None:
        version = db.scalar(
            select(DocumentVersion).where(
                DocumentVersion.id == version_id,
                DocumentVersion.document_id == document.id,
            )
        )
    else:
        version = db.scalar(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document.id)
            .order_by(
                DocumentVersion.is_active.desc(),
                DocumentVersion.version_number.desc(),
            )
            .limit(1)
        )
    if version is None or version.status == "deleted":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "문서 파일을 찾을 수 없습니다.")
    return version


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
                title=document.title,
                version_number=version.version_number,
                document_type_code=document.document_type_code,
                source_type=document.source_type,
                access_level=document.access_level,
                lifecycle_status=document.lifecycle_status,
                status=version.status,
                is_active=version.is_active,
                extractor=extractor,
                fallback_used=fallback_used,
                processing_warning=processing_warning,
                failure_reason=version.failure_reason,
                page_count=version.page_count,
                created_at=version.created_at,
            )
        )
    return summaries


@router.get("/review-queue", response_model=list[ReviewQueueDocumentSummary])
def list_document_review_queue(
    current_user: Annotated[User, Depends(require_document_approve)],
    db: Annotated[Session, Depends(get_db)],
    include_processing: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ReviewQueueDocumentSummary]:
    """Return each document's latest version that still needs operator attention."""

    del current_user
    visible_statuses = (
        ("pending", "processing", "review_required")
        if include_processing
        else ("review_required",)
    )
    ranked_versions = (
        select(
            DocumentVersion.id.label("document_version_id"),
            func.row_number()
            .over(
                partition_by=DocumentVersion.document_id,
                order_by=(
                    DocumentVersion.version_number.desc(),
                    DocumentVersion.created_at.desc(),
                    DocumentVersion.id.desc(),
                ),
            )
            .label("queue_rank"),
        )
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(
            Document.deleted_at.is_(None),
            Document.lifecycle_status.in_(visible_statuses),
            DocumentVersion.status.in_(visible_statuses),
        )
        .subquery()
    )
    rows = db.execute(
        select(Document, DocumentVersion, User.name)
        .join(DocumentVersion, DocumentVersion.document_id == Document.id)
        .join(
            ranked_versions,
            ranked_versions.c.document_version_id == DocumentVersion.id,
        )
        .outerjoin(User, User.id == DocumentVersion.uploaded_by_user_id)
        .where(ranked_versions.c.queue_rank == 1)
        .order_by(DocumentVersion.created_at.desc(), DocumentVersion.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()

    summaries: list[ReviewQueueDocumentSummary] = []
    for document, version, uploader_name in rows:
        extractor, fallback_used, processing_warning = _processing_summary(version)
        summaries.append(
            ReviewQueueDocumentSummary(
                document_id=document.id,
                document_version_id=version.id,
                original_filename=version.original_filename,
                title=document.title,
                version_number=version.version_number,
                document_type_code=document.document_type_code,
                source_type=document.source_type,
                access_level=document.access_level,
                lifecycle_status=document.lifecycle_status,
                version_status=version.status,
                is_active=version.is_active,
                uploaded_by_user_id=version.uploaded_by_user_id,
                uploader_name=uploader_name,
                created_at=version.created_at,
                extractor=extractor,
                fallback_used=fallback_used,
                processing_warning=processing_warning,
                failure_reason=version.failure_reason,
                page_count=version.page_count,
            )
        )
    return summaries


@router.get("/{document_id}/file")
def get_document_file(
    document_id: UUID,
    current_user: Annotated[User, Depends(require_document_read)],
    access_scope: Annotated[RetrievalAccessScope, Depends(get_retrieval_access_scope)],
    db: Annotated[Session, Depends(get_db)],
    version_id: Annotated[UUID | None, Query()] = None,
) -> FileResponse:
    """채팅 근거 문서를 원문 그대로 열람할 수 있도록 저장된 PDF를 반환한다.

    version_id를 지정하면(예: 인용된 근거의 document_version_id) 이후 새 버전이
    올라와도 인용 당시 그 버전을 그대로 볼 수 있다. 지정하지 않으면 활성 버전 중
    최신 버전을 돌려준다.
    """

    document = require_accessible_document(db, document_id, current_user, access_scope)
    version = _resolve_document_version(db, document, version_id)
    path = Path(version.storage_path)
    if not path.is_file():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "저장된 문서를 읽지 못했습니다."
        )
    return FileResponse(
        path,
        media_type=version.mime_type or "application/pdf",
        filename=version.original_filename,
        headers={"Cache-Control": "private, no-store"},
    )


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

    doc_name = _normalized_form_value(
        Path(original_filename).stem, field="파일명", max_length=500
    )
    external_id = f"{source_type}:{manufacturer}:{model_name}:{doc_name}"
    if len(external_id) > 200:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="문서 식별 정보가 너무 깁니다. 제조사·모델명·파일명을 줄여 주세요.",
        )
    staged_upload: StagedPdfUpload | None = None
    storage_path: Path | None = None
    try:
        staged_upload = stage_pdf_upload(
            file.file,
            Path(settings.document_storage_dir),
            max_bytes=settings.document_max_upload_bytes,
        )

        # 같은 사용자가 내용이 완전히 동일한 파일을 다시 올리는 경우(같은 파일을
        # 실수로 두 번 선택하거나, 파일명만 바꿔 다시 올리는 경우 등) 새 버전을
        # 만들지 않고 막는다. 그렇지 않으면 이미 처리 완료된 내용을 worker가
        # docling으로 처음부터 다시 처리하게 된다.
        duplicate_version = db.scalar(
            select(DocumentVersion)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(
                DocumentVersion.sha256 == staged_upload.sha256,
                DocumentVersion.status != "deleted",
                Document.lifecycle_status != "deleted",
                Document.created_by_user_id == current_user.id,
            )
            .order_by(DocumentVersion.version_number.desc())
            .limit(1)
        )
        if duplicate_version is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "동일한 내용의 파일이 이미 등록되어 있습니다: "
                    f"{duplicate_version.original_filename} "
                    f"(문서: {duplicate_version.document.title})."
                ),
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

        document = db.scalar(
            select(Document)
            .where(Document.external_id == external_id)
            .with_for_update()
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

        stored_filename = f"{uuid4().hex}.pdf"
        storage_path = Path(settings.document_storage_dir) / stored_filename
        staged_upload.move_to(storage_path)

        version = DocumentVersion(
            document_id=document.id,
            version_number=next_version_number,
            original_filename=original_filename,
            stored_filename=stored_filename,
            storage_path=str(storage_path),
            sha256=staged_upload.sha256,
            file_size=staged_upload.file_size,
            mime_type="application/pdf",
            uploaded_by_user_id=current_user.id,
        )
        db.add(version)
        db.flush()
        db.add(DocumentProcessingJob(document_version_id=version.id))
        db.commit()
    except EmptyPdfUploadError as error:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    except InvalidPdfHeaderError as error:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    except PdfUploadTooLargeError as error:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=str(error),
        ) from error
    except PdfUploadIOError as error:
        db.rollback()
        _safe_unlink(storage_path)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="문서 저장소에 파일을 저장하지 못했습니다.",
        ) from error
    except IntegrityError as error:
        db.rollback()
        _safe_unlink(storage_path)
        constraint_name = getattr(
            getattr(getattr(error, "orig", None), "diag", None),
            "constraint_name",
            None,
        )
        if constraint_name in {
            "uq_documents_external_id",
            "uq_document_versions_number",
        }:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="동일 문서가 동시에 업로드되었습니다. 잠시 후 다시 시도해 주세요.",
            ) from error
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="업로드 요청이 문서 제약 조건과 맞지 않습니다 (document_type_code 등을 확인하세요).",
        ) from error
    except OSError as error:
        db.rollback()
        _safe_unlink(storage_path)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="문서 저장소에 파일을 저장하지 못했습니다.",
        ) from error
    except Exception:
        db.rollback()
        _safe_unlink(storage_path)
        raise
    finally:
        if staged_upload is not None:
            staged_upload.cleanup()

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


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: UUID,
    current_user: Annotated[User, Depends(require_document_delete)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    """문서를 소프트 삭제한다(레코드/원본 파일은 남기고 접근·검색 대상에서만 제외).

    `lifecycle_status`를 'deleted'로만 바꾸면 되는 이유: RAG 검색(`retrieval.py`)은
    `lifecycle_status = 'active'`인 문서만 대상으로 하고, worker의 상태 전이 쿼리도
    전부 `lifecycle_status <> 'deleted'`를 조건으로 걸고 있어(`ai/rag_service/worker.py`)
    이미 처리 중이던 job이 나중에 끝나도 삭제 상태를 덮어쓰지 않는다. 실제 파일을 함께
    지우면(하드 삭제) 처리 중이던 job이 결과를 저장하려다 예외 없이 죽는 문제가 있었음
    (오늘 세션에서 재현) — 소프트 삭제는 이 문제 자체가 발생하지 않는다.
    """

    document = db.get(Document, document_id)
    if document is None or document.lifecycle_status == "deleted":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "문서를 찾을 수 없습니다.")

    document.lifecycle_status = "deleted"
    document.deleted_at = datetime.now(timezone.utc)
    db.add(
        AuditEvent(
            event_type="document.deleted",
            actor_user_id=current_user.id,
            entity_type="document",
            entity_id=document.id,
        )
    )
    db.commit()
