"""DB-backed session tokens for SafeMaint accounts (auth_sessions table)."""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import AuthSession, User

_TOKEN_BYTES = 32


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(db: Session, user_id: UUID) -> str:
    """Issue a new opaque session token and store its hash. Returns the raw token."""

    token = secrets.token_urlsafe(_TOKEN_BYTES)
    now = datetime.now(timezone.utc)
    db.add(
        AuthSession(
            user_id=user_id,
            token_hash=_hash_token(token),
            expires_at=now + timedelta(minutes=settings.session_expire_minutes),
        )
    )
    return token


def resolve_session_user(db: Session, token: str) -> User | None:
    """Return the active User for a valid, unexpired, unrevoked token, else None."""

    now = datetime.now(timezone.utc)
    session = db.scalar(
        select(AuthSession).where(
            AuthSession.token_hash == _hash_token(token),
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > now,
        )
    )
    if session is None:
        return None

    session.last_seen_at = now
    return db.get(User, session.user_id)


def revoke_session(db: Session, token: str) -> None:
    now = datetime.now(timezone.utc)
    session = db.scalar(
        select(AuthSession).where(
            AuthSession.token_hash == _hash_token(token),
            AuthSession.revoked_at.is_(None),
        )
    )
    if session is not None:
        session.revoked_at = now
