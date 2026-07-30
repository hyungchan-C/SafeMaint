import pytest

from app.core.config import _limit_env


def test_zero_limit_env_means_unlimited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCUMENT_MAX_UPLOAD_BYTES", "0")

    assert _limit_env("DOCUMENT_MAX_UPLOAD_BYTES", 123) is None


def test_positive_limit_env_is_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCUMENT_MAX_UPLOAD_BYTES", "456")

    assert _limit_env("DOCUMENT_MAX_UPLOAD_BYTES", 123) == 456


def test_negative_limit_env_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCUMENT_MAX_UPLOAD_BYTES", "-1")

    with pytest.raises(ValueError, match="zero or greater"):
        _limit_env("DOCUMENT_MAX_UPLOAD_BYTES", 123)
