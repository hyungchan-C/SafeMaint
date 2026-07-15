from app.services.passwords import hash_password, verify_password


def test_password_hash_round_trip() -> None:
    encoded = hash_password("correct-horse-123")

    assert encoded != "correct-horse-123"
    assert verify_password("correct-horse-123", encoded)
    assert not verify_password("wrong-password", encoded)

