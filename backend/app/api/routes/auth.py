from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import oauth2_scheme
from app.db.models import AuditEvent, Role, User, UserRole
from app.db.session import get_db
from app.schemas.auth import AuthUserResponse, LoginRequest, LoginResponse, RegisterRequest
from app.services.auth_sessions import create_session, revoke_session
from app.services.passwords import (
    DUMMY_PASSWORD_HASH,
    hash_password,
    password_needs_rehash,
    verify_password,
)


router = APIRouter(prefix="/auth", tags=["auth"])

DEFAULT_ROLE_CODE = "worker"
MAX_FAILED_LOGINS = 5
LOCK_DURATION = timedelta(minutes=15)


def _role_codes(db: Session, user_id: UUID) -> list[str]:
    return list(
        db.scalars(
            select(Role.code)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user_id)
            .order_by(Role.code)
        )
    )


def _response(db: Session, user: User) -> AuthUserResponse:
    return AuthUserResponse(
        id=user.id,
        employee_number=user.employee_number,
        name=user.name,
        email=user.email,
        department=user.department,
        job_title=user.job_title,
        status=user.status,
        roles=_role_codes(db, user.id),
        last_login_at=user.last_login_at,
    )


@router.post(
    "/register",
    response_model=AuthUserResponse,
    status_code=status.HTTP_201_CREATED,
)
def register(
    payload: RegisterRequest,
    db: Annotated[Session, Depends(get_db)],
) -> AuthUserResponse:
    worker_role = db.scalar(
        select(Role).where(
            Role.code == DEFAULT_ROLE_CODE,
            Role.is_active.is_(True),
        )
    )
    if worker_role is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="기본 작업자 권한이 준비되지 않았습니다. seed 실행 상태를 확인해 주세요.",
        )

    duplicate_filters = [User.employee_number == payload.employee_number]
    if payload.email is not None:
        duplicate_filters.append(func.lower(User.email) == payload.email)
    if db.scalar(select(User.id).where(or_(*duplicate_filters))) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="이미 사용 중인 사원번호 또는 이메일입니다.",
        )

    now = datetime.now(timezone.utc)
    user = User(
        employee_number=payload.employee_number,
        name=payload.name,
        email=payload.email,
        department=payload.department,
        job_title=payload.job_title,
        auth_provider="local",
        password_hash=hash_password(payload.password.get_secret_value()),
        status="active",
        password_changed_at=now,
    )

    try:
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=worker_role.id))
        db.add(
            AuditEvent(
                event_type="user.registered",
                actor_user_id=user.id,
                entity_type="user",
                entity_id=user.id,
                payload={"role": DEFAULT_ROLE_CODE},
            )
        )
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="이미 사용 중인 사원번호 또는 이메일입니다.",
        ) from error

    return _response(db, user)


@router.post("/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    db: Annotated[Session, Depends(get_db)],
) -> LoginResponse:
    user = db.scalar(
        select(User)
        .where(User.employee_number == payload.employee_number)
        .with_for_update()
    )
    supplied_password = payload.password.get_secret_value()

    if user is None or user.auth_provider != "local" or not user.password_hash:
        verify_password(supplied_password, DUMMY_PASSWORD_HASH)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="사원번호 또는 비밀번호가 올바르지 않습니다.",
        )

    now = datetime.now(timezone.utc)
    if user.status == "locked":
        if user.locked_until is None or user.locked_until > now:
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail="로그인이 잠겨 있습니다. 잠시 후 다시 시도하거나 관리자에게 문의해 주세요.",
            )
        user.status = "active"
        user.failed_login_count = 0
        user.locked_until = None

    password_matches = verify_password(supplied_password, user.password_hash)

    if user.status == "retired":
        if not password_matches:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="사원번호 또는 비밀번호가 올바르지 않습니다.",
            )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="사용할 수 없는 계정입니다. 관리자에게 문의해 주세요.",
        )

    if not password_matches:
        user.failed_login_count += 1
        if user.failed_login_count >= MAX_FAILED_LOGINS:
            user.status = "locked"
            user.locked_until = now + LOCK_DURATION
            db.add(
                AuditEvent(
                    event_type="user.login_locked",
                    actor_user_id=user.id,
                    entity_type="user",
                    entity_id=user.id,
                    payload={"failed_login_count": user.failed_login_count},
                )
            )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="사원번호 또는 비밀번호가 올바르지 않습니다.",
        )

    user.status = "active"
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(supplied_password)
        user.password_changed_at = now
    db.add(
        AuditEvent(
            event_type="user.login_succeeded",
            actor_user_id=user.id,
            entity_type="user",
            entity_id=user.id,
            payload={},
        )
    )
    access_token = create_session(db, user.id)
    db.commit()

    return LoginResponse(
        access_token=access_token,
        user=_response(db, user),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    token: Annotated[str | None, Depends(oauth2_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    if token is not None:
        revoke_session(db, token)
        db.commit()
