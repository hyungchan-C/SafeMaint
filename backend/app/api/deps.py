from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.db.models import Permission, Role, RolePermission, User, UserRole
from app.services.auth_sessions import resolve_session_user

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=f"{settings.api_prefix}/auth/login", auto_error=False
)

_CREDENTIALS_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="인증이 필요합니다.",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    token: Annotated[str | None, Depends(oauth2_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    if token is None:
        raise _CREDENTIALS_ERROR

    user = resolve_session_user(db, token)
    if user is None or user.status != "active":
        raise _CREDENTIALS_ERROR

    return user


def require_permission(permission_code: str) -> Callable[..., User]:
    """Build a dependency that requires an active permission through any role."""

    def dependency(
        current_user: Annotated[User, Depends(get_current_user)],
        db: Annotated[Session, Depends(get_db)],
    ) -> User:
        permission_id = db.scalar(
            select(Permission.id)
            .join(
                RolePermission,
                RolePermission.permission_id == Permission.id,
            )
            .join(Role, Role.id == RolePermission.role_id)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(
                UserRole.user_id == current_user.id,
                Permission.code == permission_code,
                Permission.is_active.is_(True),
                Role.is_active.is_(True),
            )
            .limit(1)
        )
        if permission_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="이 작업을 수행할 권한이 없습니다.",
            )
        return current_user

    return dependency


require_document_upload = require_permission("document.upload")
