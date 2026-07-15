"""Password hashing helpers for local SafeMaint accounts."""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError


_password_hasher = PasswordHasher()

# Missing users still perform one Argon2 verification to reduce account-enumeration
# timing differences. This value is process-local and is never stored in the DB.
DUMMY_PASSWORD_HASH = _password_hasher.hash("SafeMaint-Dummy-Password-Only")


def hash_password(password: str) -> str:
    """Return an Argon2id hash with a fresh random salt."""

    return _password_hasher.hash(password)


def verify_password(password: str, encoded_hash: str) -> bool:
    """Return False for a mismatch or an invalid/unsupported stored hash."""

    try:
        return _password_hasher.verify(encoded_hash, password)
    except (VerifyMismatchError, InvalidHashError, VerificationError):
        return False


def password_needs_rehash(encoded_hash: str) -> bool:
    """Return whether a valid hash should be upgraded to current parameters."""

    try:
        return _password_hasher.check_needs_rehash(encoded_hash)
    except (InvalidHashError, VerificationError):
        return False
