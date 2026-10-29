"""Argon2id password hashing with bounded inputs and rehash support."""
from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
_dummy_hash = _hasher.hash("not-a-real-user-password")


def hash_password(password: str, *, minimum: int = 10) -> str:
    if not isinstance(password, str) or len(password) < minimum or len(password) > 128:
        raise ValueError(f"Password must be between {minimum} and 128 characters.")
    if not password.strip():
        raise ValueError("Password cannot be blank.")
    return _hasher.hash(password)


def verify_password(password: str, encoded_hash: str) -> bool:
    if not isinstance(password, str) or len(password) > 128:
        return False
    try:
        return bool(_hasher.verify(encoded_hash, password))
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def verify_dummy(password: str) -> None:
    verify_password(password, _dummy_hash)


def needs_rehash(encoded_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(encoded_hash)
    except (InvalidHashError, TypeError):
        return True
