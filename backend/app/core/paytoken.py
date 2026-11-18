"""Pay-link tokens (03 section 8.2): 32 random bytes, shown once; only the SHA-256 hash is stored."""

from __future__ import annotations

import base64
import hashlib
import secrets

PREFIX = "rvp_"
PAY_TOKEN_DAYS = 30


def generate_pay_token() -> tuple[str, str]:
    """Returns (token, sha256 hex of token). The token is never persisted."""
    raw = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    token = f"{PREFIX}{raw}"
    return token, hash_pay_token(token)


def hash_pay_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def looks_like_pay_token(value: str) -> bool:
    """Cheap shape check so garbage never reaches the database. Not a security control."""
    return value.startswith(PREFIX) and 40 <= len(value) <= 64
