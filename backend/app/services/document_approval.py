from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AuditEvent, Document, DocumentVersion, User


class DocumentApprovalNotFoundError(LookupError):
    pass


class DocumentApprovalConflictError(RuntimeError):
    pass


def approve_document_version(
    db: Session,
    *,
    document_id: UUID,
    version_id: UUID,
    approved_by: User,
) -> tuple[Document, DocumentVersion]:
    """Activate one reviewed version and supersede any previous active version."""

    try:
        document = db.scalar(
            select(Document)
            .where(Document.id == document_id, Document.deleted_at.is_(None))
            .with_for_update()
        )
        if document is None:
            raise DocumentApprovalNotFoundError("문서를 찾을 수 없습니다.")

        versions = list(
            db.scalars(
                select(DocumentVersion)
                .where(DocumentVersion.document_id == document.id)
                .order_by(DocumentVersion.version_number)
                .with_for_update()
            ).all()
        )
        version = next((item for item in versions if item.id == version_id), None)
        if version is None:
            raise DocumentApprovalNotFoundError(
                "해당 문서에 속한 문서 버전을 찾을 수 없습니다."
            )
        if version.status != "review_required":
            raise DocumentApprovalConflictError(
                "검토 대기 상태의 문서 버전만 승인할 수 있습니다."
            )

        previous_version_id = document.current_version_id
        for candidate in versions:
            if candidate.id == version.id:
                continue
            if candidate.is_active or candidate.status == "active":
                candidate.is_active = False
                candidate.status = "superseded"
        # The partial unique index permits only one active version. Flush the
        # deactivation first while keeping both updates in this transaction.
        db.flush()

        now = datetime.now(timezone.utc)
        version.status = "active"
        version.is_active = True
        version.approved_by_user_id = approved_by.id
        version.approved_at = now
        document.current_version_id = version.id
        document.lifecycle_status = "active"
        db.add(
            AuditEvent(
                event_type="DOCUMENT_VERSION_APPROVED",
                actor_user_id=approved_by.id,
                entity_type="document",
                entity_id=document.id,
                document_version_id=version.id,
                success=True,
                payload={
                    "version_number": version.version_number,
                    "previous_version_id": (
                        str(previous_version_id) if previous_version_id else None
                    ),
                },
            )
        )
        db.commit()
        db.refresh(document)
        db.refresh(version)
        return document, version
    except (DocumentApprovalNotFoundError, DocumentApprovalConflictError):
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
