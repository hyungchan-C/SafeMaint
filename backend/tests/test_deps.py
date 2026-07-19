from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.deps import get_current_user
from app.db.models import User


def _make_user(status: str = "active") -> User:
    user = User(employee_number="EMP-01", name="테스트 사용자", status=status)
    user.id = uuid4()
    return user


def test_get_current_user_returns_active_user_for_valid_token() -> None:
    user = _make_user()

    with patch("app.api.deps.resolve_session_user", return_value=user):
        result = get_current_user(token="a-valid-token", db=object())

    assert result is user


def test_get_current_user_rejects_missing_token() -> None:
    with pytest.raises(HTTPException) as excinfo:
        get_current_user(token=None, db=object())

    assert excinfo.value.status_code == 401


def test_get_current_user_rejects_invalid_token() -> None:
    with patch("app.api.deps.resolve_session_user", return_value=None):
        with pytest.raises(HTTPException) as excinfo:
            get_current_user(token="not-a-real-token", db=object())

    assert excinfo.value.status_code == 401


def test_get_current_user_rejects_inactive_user() -> None:
    user = _make_user(status="locked")

    with patch("app.api.deps.resolve_session_user", return_value=user):
        with pytest.raises(HTTPException) as excinfo:
            get_current_user(token="a-valid-token", db=object())

    assert excinfo.value.status_code == 401
