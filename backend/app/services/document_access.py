from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.db.models import Document, User
from app.schemas.chat import RetrievalAccessScope


def can_access_document(
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


def require_accessible_document(
    db: Session,
    document_id: UUID,
    current_user: User,
    access_scope: RetrievalAccessScope,
) -> Document:
    document = db.get(Document, document_id)
    if document is None or not can_access_document(document, current_user, access_scope):
        # Do not reveal whether an inaccessible document exists.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "문서를 찾을 수 없습니다.")
    return document
