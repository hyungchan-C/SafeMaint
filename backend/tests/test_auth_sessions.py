import hashlib
from datetime import datetime, timezone
from uuid import uuid4

from app.db.models import AuthSession
from app.services.auth_sessions import _hash_token, create_session


class _FakeSession:
    def __init__(self) -> None:
        self.added: list[AuthSession] = []

    def add(self, obj: AuthSession) -> None:
        self.added.append(obj)


def test_hash_token_is_deterministic_sha256_hex() -> None:
    digest = _hash_token("some-token-value")

    assert digest == hashlib.sha256(b"some-token-value").hexdigest()
    assert len(digest) == 64


def test_create_session_stores_hash_not_raw_token() -> None:
    db = _FakeSession()
    user_id = uuid4()

    token = create_session(db, user_id)

    assert len(db.added) == 1
    session = db.added[0]
    assert session.user_id == user_id
    assert session.token_hash == _hash_token(token)
    assert session.token_hash != token
    assert session.expires_at > datetime.now(timezone.utc)


def test_create_session_tokens_are_unique() -> None:
    db = _FakeSession()
    user_id = uuid4()

    first = create_session(db, user_id)
    second = create_session(db, user_id)

    assert first != second
