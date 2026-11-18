"""RS256 JWT issue and verify, plus the JWKS document (02 section 4, 03 section 6.1).

PyJWT only (python-jose is banned). The algorithm is pinned on decode, `exp`, `iss`, `aud`, `sub` are required, and the
`kid` header selects the verification key so a previous public key can stay valid during rotation.
"""

from __future__ import annotations

import base64
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey

from app.core.context import ROLES, AuthContext, Role
from app.core.ids import new_id
from app.domain.errors import Unauthenticated

ALGORITHM = "RS256"
Audience = Literal["revops", "agent"]
SESSION_AUDIENCE: Audience = "revops"
AGENT_AUDIENCE: Audience = "agent"
SESSION_TTL = timedelta(minutes=60)
SESSION_REISSUE_BELOW = timedelta(minutes=30)
SESSION_ABSOLUTE = timedelta(hours=12)
AGENT_TTL = timedelta(minutes=30)


def generate_dev_private_key_pem() -> str:
    """An explicit, ephemeral RSA-2048 key for dev and test. Production refuses to start without a configured key."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def _b64url_uint(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _jwk(public_key: RSAPublicKey, kid: str) -> dict[str, str]:
    numbers = public_key.public_numbers()
    return {
        "kty": "RSA",
        "use": "sig",
        "alg": ALGORITHM,
        "kid": kid,
        "n": _b64url_uint(numbers.n),
        "e": _b64url_uint(numbers.e),
    }


@dataclass(frozen=True, slots=True)
class TokenClaims:
    user_id: uuid.UUID
    org_id: uuid.UUID
    role: Role
    token_version: int
    jti: str
    issued_at: datetime
    expires_at: datetime
    auth_time: datetime
    audience: tuple[str, ...]

    def context(self) -> AuthContext:
        return AuthContext(user_id=self.user_id, org_id=self.org_id, role=self.role)


class JwtService:
    def __init__(
        self,
        *,
        private_key_pem: str,
        key_id: str,
        issuer: str,
        previous_public_key_pem: str | None = None,
        previous_key_id: str | None = None,
    ) -> None:
        loaded = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
        if not isinstance(loaded, RSAPrivateKey):
            raise ValueError("JWT private key must be an RSA key")
        if loaded.key_size < 2048:
            raise ValueError("JWT private key must be at least RSA-2048")
        self._private = loaded
        self._kid = key_id
        self._issuer = issuer
        self._public_keys: dict[str, RSAPublicKey] = {key_id: loaded.public_key()}
        if previous_public_key_pem and previous_key_id:
            previous = serialization.load_pem_public_key(previous_public_key_pem.encode())
            if not isinstance(previous, RSAPublicKey):
                raise ValueError("previous JWT public key must be an RSA key")
            self._public_keys[previous_key_id] = previous

    # ------------------------------------------------------------------ issue
    def issue(
        self,
        *,
        user_id: uuid.UUID,
        org_id: uuid.UUID,
        role: Role,
        token_version: int,
        audience: Audience,
        now: datetime,
        auth_time: datetime | None = None,
    ) -> tuple[str, TokenClaims]:
        auth_at = auth_time or now
        if audience == AGENT_AUDIENCE:
            expires = now + AGENT_TTL
        else:
            expires = min(now + SESSION_TTL, auth_at + SESSION_ABSOLUTE)
        jti = new_id().hex
        payload: dict[str, Any] = {
            "iss": self._issuer,
            "aud": [audience],
            "sub": str(user_id),
            "org": str(org_id),
            "role": role,
            "tv": token_version,
            "jti": jti,
            "iat": int(now.timestamp()),
            "exp": int(expires.timestamp()),
            "auth_time": int(auth_at.timestamp()),
        }
        token = jwt.encode(payload, self._private, algorithm=ALGORITHM, headers={"kid": self._kid})
        claims = TokenClaims(
            user_id=user_id,
            org_id=org_id,
            role=role,
            token_version=token_version,
            jti=jti,
            issued_at=datetime.fromtimestamp(payload["iat"], UTC),
            expires_at=datetime.fromtimestamp(payload["exp"], UTC),
            auth_time=datetime.fromtimestamp(payload["auth_time"], UTC),
            audience=(audience,),
        )
        return token, claims

    # ------------------------------------------------------------------ verify
    def decode(self, token: str, *, audience: Audience, now: datetime | None = None) -> TokenClaims:
        """Verify signature, algorithm, issuer, audience and expiry. Every failure is the same `Unauthenticated`."""
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != ALGORITHM:
                raise Unauthenticated("Invalid session.")
            key = self._public_keys.get(str(header.get("kid")))
            if key is None:
                raise Unauthenticated("Invalid session.")
            options: Any = {"require": ["exp", "iss", "aud", "sub", "iat"]}
            payload = jwt.decode(
                token,
                key,
                algorithms=[ALGORITHM],
                audience=audience,
                issuer=self._issuer,
                options=options,
                leeway=0,
                # PyJWT validates exp against the wall clock; tests that need another "now" re-issue tokens instead.
            )
            claims = self._claims_from(payload)
        except (jwt.PyJWTError, KeyError, ValueError, TypeError) as exc:
            raise Unauthenticated("Invalid session.") from exc
        if now is not None and claims.expires_at <= now:
            raise Unauthenticated("Invalid session.")
        return claims

    @staticmethod
    def _claims_from(payload: dict[str, Any]) -> TokenClaims:
        role = payload["role"]
        if role not in ROLES:
            raise ValueError("unknown role")
        audience = payload["aud"]
        return TokenClaims(
            user_id=uuid.UUID(payload["sub"]),
            org_id=uuid.UUID(payload["org"]),
            role=role,
            token_version=int(payload["tv"]),
            jti=str(payload["jti"]),
            issued_at=datetime.fromtimestamp(payload["iat"], UTC),
            expires_at=datetime.fromtimestamp(payload["exp"], UTC),
            auth_time=datetime.fromtimestamp(payload["auth_time"], UTC),
            audience=tuple(audience) if isinstance(audience, list) else (str(audience),),
        )

    # ------------------------------------------------------------------ JWKS
    def jwks(self) -> dict[str, list[dict[str, str]]]:
        return {"keys": [_jwk(public, kid) for kid, public in self._public_keys.items()]}
