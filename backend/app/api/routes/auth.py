from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import User
from app.db.session import get_db
from app.schemas.auth import LoginRequest, RegisterRequest, UserResponse
from app.services.passwords import hash_password, verify_password


router = APIRouter(prefix="/auth", tags=["auth"])
MAX_FAILED_LOGINS = 5
LOCK_MINUTES = 15


def _response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        employee_number=user.employee_number,
        name=user.name,
        email=user.email,
        department=user.department,
        job_title=user.job_title,
        status=user.status,
    )


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> UserResponse:
    duplicate = db.scalar(
        select(User).where(
            or_(
                User.employee_number == payload.employee_number,
                User.email == payload.email if payload.email else False,
            )
        )
    )
    if duplicate:
        detail = "이미 등록된 이메일입니다." if payload.email and duplicate.email == payload.email else "이미 사용 중인 사원번호입니다."
        raise HTTPException(status_code=409, detail=detail)

    now = datetime.now(timezone.utc)
    user = User(
        employee_number=payload.employee_number,
        name=payload.name,
        email=payload.email,
        department=payload.department,
        job_title=payload.job_title,
        auth_provider="local",
        password_hash=hash_password(payload.password),
        status="active",
        failed_login_count=0,
        password_changed_at=now,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="이미 등록된 사원번호 또는 이메일입니다.") from exc
    db.refresh(user)
    return _response(user)


@router.post("/login", response_model=UserResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> UserResponse:
    user = db.scalar(select(User).where(User.employee_number == payload.employee_number))
    now = datetime.now(timezone.utc)
    if user is None or user.auth_provider != "local" or not user.password_hash:
        raise HTTPException(status_code=401, detail="사원번호 또는 비밀번호가 올바르지 않습니다.")
    if user.status == "retired":
        raise HTTPException(status_code=403, detail="비활성화된 계정입니다. 관리자에게 문의하세요.")
    if user.locked_until and user.locked_until > now:
        raise HTTPException(status_code=423, detail="로그인 실패가 반복되어 계정이 잠겼습니다. 잠시 후 다시 시도하세요.")
    if not verify_password(payload.password, user.password_hash):
        user.failed_login_count += 1
        if user.failed_login_count >= MAX_FAILED_LOGINS:
            user.status = "locked"
            user.locked_until = now + timedelta(minutes=LOCK_MINUTES)
        db.commit()
        raise HTTPException(status_code=401, detail="사원번호 또는 비밀번호가 올바르지 않습니다.")

    user.failed_login_count = 0
    user.locked_until = None
    user.status = "active"
    user.last_login_at = now
    db.commit()
    db.refresh(user)
    return _response(user)
