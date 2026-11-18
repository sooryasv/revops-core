from __future__ import annotations

import pytest

from app.core.passwords import PasswordService, make_hasher, validate_password
from app.core.permissions import ROLE_PERMISSIONS, Permission
from app.core.rate_limit import RateLimiter
from app.domain.errors import RateLimited, ValidationFailed

P = Permission


@pytest.fixture(scope="module")
def svc() -> PasswordService:
    return PasswordService(make_hasher(fast_for_tests=True))


async def test_hash_is_argon2id_and_verifies(svc: PasswordService) -> None:
    hashed = await svc.hash("a-long-enough-password")
    assert hashed.startswith("$argon2id$")
    assert await svc.verify(hashed, "a-long-enough-password")
    assert not await svc.verify(hashed, "a-long-enough-passworD")


async def test_hashes_are_salted(svc: PasswordService) -> None:
    assert await svc.hash("a-long-enough-password") != await svc.hash("a-long-enough-password")


async def test_unknown_user_still_runs_a_verify_and_fails() -> None:
    calls: list[str] = []

    class Spy:
        def __init__(self) -> None:
            self.real = make_hasher(fast_for_tests=True)

        def hash(self, password: str) -> str:
            return self.real.hash(password)

        def verify(self, hash_: str, password: str) -> bool:
            calls.append(hash_)
            return self.real.verify(hash_, password)

    spy_service = PasswordService(Spy())  # type: ignore[arg-type]
    assert not await spy_service.verify(None, "whatever")
    assert calls == [spy_service._dummy_hash]


async def test_corrupt_stored_hash_is_a_failed_login_not_a_crash(svc: PasswordService) -> None:
    assert not await svc.verify("not-an-argon2-hash", "whatever")


def test_length_limits() -> None:
    for bad in ("short", "x" * 9, "x" * 129):
        with pytest.raises(ValidationFailed):
            validate_password(bad)
    assert validate_password("x" * 10)
    assert validate_password("x" * 128)


async def test_rehash_detected_when_parameters_change() -> None:
    weak = PasswordService(make_hasher(fast_for_tests=True))
    hashed = await weak.hash("a-long-enough-password")
    strong = PasswordService(make_hasher())
    assert strong.needs_rehash(hashed)
    assert not weak.needs_rehash(hashed)


# ----------------------------------------------------------------------------- permission matrix (03 section 6.3)
MATRIX = {
    # permission: (admin, finance, sales)
    P.ORG_READ: (1, 1, 1), P.ORG_WRITE: (1, 0, 0), P.USERS_MANAGE: (1, 0, 0),
    P.CUSTOMERS_READ: (1, 1, 1), P.CUSTOMERS_WRITE: (1, 1, 1),
    P.DEALS_READ: (1, 1, 1), P.DEALS_WRITE: (1, 0, 1),
    P.INVOICES_READ: (1, 1, 1), P.INVOICES_DRAFT: (1, 1, 1),
    P.INVOICES_ISSUE: (1, 1, 0), P.INVOICES_VOID: (1, 1, 0), P.INVOICES_PAYLINK: (1, 1, 0),
    P.PAYMENTS_READ: (1, 1, 1), P.PAYMENTS_MANUAL: (1, 1, 0),
    P.REPORTS_FINANCE: (1, 1, 0), P.REPORTS_PIPELINE: (1, 1, 1),
    P.AUDIT_READ: (1, 0, 0), P.ADMIN_JOBS: (1, 0, 0),
}  # fmt: skip


def test_matrix_covers_every_permission() -> None:
    assert set(MATRIX) == set(Permission)


@pytest.mark.parametrize("permission", list(Permission))
def test_role_permissions_match_the_spec_table(permission: Permission) -> None:
    got = tuple(int(permission in ROLE_PERMISSIONS[r]) for r in ("admin", "finance", "sales"))  # type: ignore[index]
    assert got == MATRIX[permission]


# ----------------------------------------------------------------------------- rate limiting
def test_rate_limiter_blocks_after_the_limit() -> None:
    limiter = RateLimiter()
    for _ in range(3):
        limiter.check("3/minute", "b", "a@example.com")
    with pytest.raises(RateLimited) as caught:
        limiter.check("3/minute", "b", "a@example.com")
    assert caught.value.retry_after > 0


def test_rate_limiter_buckets_and_identifiers_are_independent_and_case_insensitive() -> None:
    limiter = RateLimiter()
    for _ in range(2):
        limiter.check("2/minute", "b", "A@Example.com")
    with pytest.raises(RateLimited):
        limiter.check("2/minute", "b", "a@example.com")
    limiter.check("2/minute", "b", "other@example.com")
    limiter.check("2/minute", "other-bucket", "a@example.com")
