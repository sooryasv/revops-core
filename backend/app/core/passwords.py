"""Password hashing: argon2id via argon2-cffi (03 section 6.1). passlib and python-jose are banned (00 B5)."""

from __future__ import annotations

import asyncio

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.domain.errors import FieldError, ValidationFailed

MIN_LENGTH = 10
MAX_LENGTH = 128


def validate_password(password: str) -> str:
    if not MIN_LENGTH <= len(password) <= MAX_LENGTH:
        raise ValidationFailed(
            "invalid password",
            errors=[FieldError("password", f"{MIN_LENGTH} to {MAX_LENGTH} characters")],
        )
    return password


def make_hasher(*, fast_for_tests: bool = False) -> PasswordHasher:
    # Library defaults are argon2id with the RFC 9106 low-memory profile; the cheap profile exists only so the
    # test suite does not spend minutes hashing. Settings refuses it outside env=test.
    if fast_for_tests:
        return PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    return PasswordHasher()


class PasswordService:
    def __init__(self, hasher: PasswordHasher) -> None:
        self._hasher = hasher
        # Verified against when the email is unknown, so "no such user" costs the same time as "wrong password".
        self._dummy_hash = hasher.hash("dummy-password-for-timing-equalisation")

    async def hash(self, password: str) -> str:
        return await asyncio.to_thread(self._hasher.hash, validate_password(password))

    async def verify(self, stored_hash: str | None, password: str) -> bool:
        """Always performs exactly one argon2 verification, even when there is no stored hash."""
        target = stored_hash or self._dummy_hash
        try:
            ok = await asyncio.to_thread(self._hasher.verify, target, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False
        return ok and stored_hash is not None

    def needs_rehash(self, stored_hash: str) -> bool:
        return self._hasher.check_needs_rehash(stored_hash)
