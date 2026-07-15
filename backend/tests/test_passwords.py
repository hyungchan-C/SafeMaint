from app.services.passwords import (
    hash_password,
    password_needs_rehash,
    verify_password,
)


def test_argon2id_password_hash_round_trip() -> None:
    password = "Correct-Horse-2026!"
    first_hash = hash_password(password)
    second_hash = hash_password(password)

    assert first_hash.startswith("$argon2id$")
    assert first_hash != password
    assert first_hash != second_hash
    assert verify_password(password, first_hash)
    assert not verify_password("wrong-password", first_hash)
    assert not password_needs_rehash(first_hash)


def test_invalid_password_hash_is_rejected_safely() -> None:
    assert not verify_password("any-password", "not-an-argon-hash")
    assert not password_needs_rehash("not-an-argon-hash")
