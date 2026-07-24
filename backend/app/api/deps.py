from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.db.models import (
    Permission,
    Role,
    RolePermission,
    Site,
    User,
    UserRole,
    UserSite,
)
from app.services.auth_sessions import resolve_session_user
from app.schemas.chat import RetrievalAccessScope

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=f"{settings.api_prefix}/auth/login", auto_error=False
)

_CREDENTIALS_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="인증이 필요합니다.",
    headers={"WWW-Authenticate": "Bearer"},
)

GLOBAL_DOCUMENT_ROLE_CODES = frozenset({"admin", "document_manager"})


def get_optional_current_user(
    token: Annotated[str | None, Depends(oauth2_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> User | None:
    if token is None:
        return None

    user = resolve_session_user(db, token)
    if user is None or user.status != "active":
        raise _CREDENTIALS_ERROR
    return user


def get_current_user(
    token: Annotated[str | None, Depends(oauth2_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    user = get_optional_current_user(token=token, db=db)
    if user is None:
        raise _CREDENTIALS_ERROR
    return user


def user_has_permission(db: Session, user_id: UUID, permission_code: str) -> bool:
    permission_id = db.scalar(
        select(Permission.id)
        .join(
            RolePermission,
            RolePermission.permission_id == Permission.id,
        )
        .join(Role, Role.id == RolePermission.role_id)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(
            UserRole.user_id == user_id,
            Permission.code == permission_code,
            Permission.is_active.is_(True),
            Role.is_active.is_(True),
        )
        .limit(1)
    )
    return permission_id is not None


def get_retrieval_access_scope(
    current_user: Annotated[User | None, Depends(get_optional_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> RetrievalAccessScope:
    """Derive trusted RAG access from DB roles and active site assignments."""

    if current_user is None or not user_has_permission(
        db, current_user.id, "document.read"
    ):
        return RetrievalAccessScope()

    role_codes = set(
        db.scalars(
            select(Role.code)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(
                UserRole.user_id == current_user.id,
                Role.is_active.is_(True),
            )
        ).all()
    )
    all_sites = bool(role_codes & GLOBAL_DOCUMENT_ROLE_CODES)
    site_ids = []
    if not all_sites:
        site_ids = [
            str(site_id)
            for site_id in db.scalars(
                select(UserSite.site_id)
                .join(Site, Site.id == UserSite.site_id)
                .where(
                    UserSite.user_id == current_user.id,
                    Site.is_active.is_(True),
                )
                .order_by(UserSite.site_id)
            ).all()
        ]

    return RetrievalAccessScope(
        requester_user_id=current_user.id,
        site_ids=site_ids,
        all_sites=all_sites,
        allow_company=True,
        allow_private=False,
    )


def require_permission(permission_code: str) -> Callable[..., User]:
    """Build a dependency that requires an active permission through any role."""

    def dependency(
        current_user: Annotated[User, Depends(get_current_user)],
        db: Annotated[Session, Depends(get_db)],
    ) -> User:
        if not user_has_permission(db, current_user.id, permission_code):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="이 작업을 수행할 권한이 없습니다.",
            )
        return current_user

    return dependency


require_document_upload = require_permission("document.upload")
require_document_read = require_permission("document.read")
require_document_approve = require_permission("document.approve")
require_document_delete = require_permission("document.delete")
