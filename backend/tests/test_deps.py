from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.deps import (
    get_current_user,
    get_optional_current_user,
    get_retrieval_access_scope,
    require_permission,
)
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


def test_optional_current_user_allows_anonymous_request() -> None:
    assert get_optional_current_user(token=None, db=object()) is None


def test_require_permission_returns_user_when_role_grants_permission() -> None:
    user = _make_user()
    db = type("PermissionDb", (), {"scalar": lambda self, _statement: uuid4()})()
    dependency = require_permission("document.upload")

    assert dependency(current_user=user, db=db) is user


def test_require_permission_rejects_missing_permission() -> None:
    user = _make_user()
    db = type("PermissionDb", (), {"scalar": lambda self, _statement: None})()
    dependency = require_permission("document.upload")

    with pytest.raises(HTTPException) as excinfo:
        dependency(current_user=user, db=db)

    assert excinfo.value.status_code == 403


class _ScalarRows:
    def __init__(self, rows: list[object]) -> None:
        self.rows = rows

    def all(self) -> list[object]:
        return self.rows


class _ScopeDb:
    def __init__(self, scalar_value: object, scalar_rows: list[list[object]]) -> None:
        self.scalar_value = scalar_value
        self.scalar_rows = list(scalar_rows)

    def scalar(self, _statement):
        return self.scalar_value

    def scalars(self, _statement) -> _ScalarRows:
        return _ScalarRows(self.scalar_rows.pop(0))


def test_retrieval_scope_is_public_only_for_anonymous_request() -> None:
    scope = get_retrieval_access_scope(current_user=None, db=object())

    assert scope.allow_company is False
    assert scope.all_sites is False
    assert scope.site_ids == []


def test_retrieval_scope_uses_active_site_assignments_for_worker() -> None:
    user = _make_user()
    site_id = uuid4()
    db = _ScopeDb(uuid4(), [["worker"], [site_id]])

    scope = get_retrieval_access_scope(current_user=user, db=db)  # type: ignore[arg-type]

    assert scope.allow_company is True
    assert scope.all_sites is False
    assert scope.allow_private is False
    assert scope.site_ids == [str(site_id)]


def test_document_manager_retrieval_scope_covers_all_company_sites() -> None:
    user = _make_user()
    db = _ScopeDb(uuid4(), [["document_manager"]])

    scope = get_retrieval_access_scope(current_user=user, db=db)  # type: ignore[arg-type]

    assert scope.allow_company is True
    assert scope.all_sites is True
    assert scope.site_ids == []
