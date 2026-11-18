"""RS256 session tokens: pinned algorithm, required claims, kid lookup, rotation (03 section 6.1, 02 section 4)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives import serialization

from app.core.jwt_service import (
    AGENT_AUDIENCE,
    SESSION_AUDIENCE,
    JwtService,
    generate_dev_private_key_pem,
)
from app.domain.errors import Unauthenticated

ISSUER = "test-issuer"
USER, ORG = uuid.uuid4(), uuid.uuid4()


@pytest.fixture(scope="module")
def pem() -> str:
    return generate_dev_private_key_pem()


@pytest.fixture
def svc(pem: str) -> JwtService:
    return JwtService(private_key_pem=pem, key_id="k1", issuer=ISSUER)


def now() -> datetime:
    return datetime.now(UTC)


def issue(svc: JwtService, **kw: object) -> str:
    args: dict[str, object] = {
        "user_id": USER, "org_id": ORG, "role": "admin", "token_version": 3,
        "audience": SESSION_AUDIENCE, "now": now(),
    }  # fmt: skip
    args.update(kw)
    return svc.issue(**args)[0]  # type: ignore[arg-type]


def b64(data: dict[str, object]) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


def test_roundtrip_carries_every_claim(svc: JwtService) -> None:
    claims = svc.decode(issue(svc), audience=SESSION_AUDIENCE)
    assert (claims.user_id, claims.org_id, claims.role, claims.token_version) == (
        USER,
        ORG,
        "admin",
        3,
    )
    assert claims.audience == ("revops",)
    assert claims.expires_at - claims.issued_at == timedelta(minutes=60)


def test_header_has_kid_and_rs256(svc: JwtService) -> None:
    header = jwt.get_unverified_header(issue(svc))
    assert header["alg"] == "RS256"
    assert header["kid"] == "k1"


def test_agent_token_lives_30_minutes_and_has_agent_audience(svc: JwtService) -> None:
    token = issue(svc, audience=AGENT_AUDIENCE)
    claims = svc.decode(token, audience=AGENT_AUDIENCE)
    assert claims.expires_at - claims.issued_at == timedelta(minutes=30)
    assert claims.audience == ("agent",)


def test_audience_is_enforced_both_ways(svc: JwtService) -> None:
    with pytest.raises(Unauthenticated):
        svc.decode(issue(svc, audience=AGENT_AUDIENCE), audience=SESSION_AUDIENCE)
    with pytest.raises(Unauthenticated):
        svc.decode(issue(svc), audience=AGENT_AUDIENCE)


def test_expired_token_is_rejected(svc: JwtService) -> None:
    old = now() - timedelta(hours=2)
    with pytest.raises(Unauthenticated):
        svc.decode(issue(svc, now=old), audience=SESSION_AUDIENCE)


def test_wrong_issuer_is_rejected(pem: str, svc: JwtService) -> None:
    other = JwtService(private_key_pem=pem, key_id="k1", issuer="someone-else")
    with pytest.raises(Unauthenticated):
        svc.decode(issue(other), audience=SESSION_AUDIENCE)


def test_tampered_payload_is_rejected(svc: JwtService) -> None:
    head, _payload, sig = issue(svc).split(".")
    forged = b64({"iss": ISSUER, "aud": ["revops"], "sub": str(USER), "org": str(uuid.uuid4()), "role": "admin",
                  "tv": 3, "jti": "x", "iat": int(now().timestamp()), "exp": int((now() + timedelta(hours=1)).timestamp()),
                  "auth_time": int(now().timestamp())})  # fmt: skip
    with pytest.raises(Unauthenticated):
        svc.decode(f"{head}.{forged}.{sig}", audience=SESSION_AUDIENCE)


def test_alg_none_is_rejected(svc: JwtService) -> None:
    header = b64({"alg": "none", "typ": "JWT", "kid": "k1"})
    payload = b64({"iss": ISSUER, "aud": ["revops"], "sub": str(USER), "org": str(ORG), "role": "admin", "tv": 3,
                   "jti": "x", "iat": int(now().timestamp()), "exp": int((now() + timedelta(hours=1)).timestamp()),
                   "auth_time": int(now().timestamp())})  # fmt: skip
    with pytest.raises(Unauthenticated):
        svc.decode(f"{header}.{payload}.", audience=SESSION_AUDIENCE)


def test_hs256_signed_with_the_public_key_is_rejected(pem: str, svc: JwtService) -> None:
    """The classic algorithm-confusion attack: the public key (which JWKS publishes) used as an HMAC secret."""
    private = serialization.load_pem_private_key(pem.encode(), password=None)
    public_pem = private.public_key().public_bytes(  # type: ignore[union-attr]
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    payload = {"iss": ISSUER, "aud": ["revops"], "sub": str(USER), "org": str(ORG), "role": "admin", "tv": 3,
               "jti": "x", "iat": int(now().timestamp()), "exp": int((now() + timedelta(hours=1)).timestamp()),
               "auth_time": int(now().timestamp())}  # fmt: skip
    # Built by hand: PyJWT itself refuses to HMAC with a PEM key, which is exactly why a real attacker would not use it.
    signing_input = f"{b64({'alg': 'HS256', 'typ': 'JWT', 'kid': 'k1'})}.{b64(payload)}"
    sig = base64.urlsafe_b64encode(
        hmac.new(public_pem, signing_input.encode(), hashlib.sha256).digest()
    ).rstrip(b"=")
    with pytest.raises(Unauthenticated):
        svc.decode(f"{signing_input}.{sig.decode()}", audience=SESSION_AUDIENCE)


def test_unknown_kid_is_rejected(pem: str, svc: JwtService) -> None:
    other = JwtService(private_key_pem=pem, key_id="rotated-away", issuer=ISSUER)
    with pytest.raises(Unauthenticated):
        svc.decode(issue(other), audience=SESSION_AUDIENCE)


def test_token_signed_by_a_different_key_is_rejected(svc: JwtService) -> None:
    attacker = JwtService(
        private_key_pem=generate_dev_private_key_pem(), key_id="k1", issuer=ISSUER
    )
    with pytest.raises(Unauthenticated):
        svc.decode(issue(attacker), audience=SESSION_AUDIENCE)


def test_garbage_is_rejected(svc: JwtService) -> None:
    for junk in ("", "a.b.c", "not-a-jwt", "....", "x" * 5000):
        with pytest.raises(Unauthenticated):
            svc.decode(junk, audience=SESSION_AUDIENCE)


def test_previous_public_key_still_verifies_during_rotation(pem: str) -> None:
    old = JwtService(private_key_pem=pem, key_id="old", issuer=ISSUER)
    old_public = (
        serialization.load_pem_private_key(pem.encode(), password=None)
        .public_key()
        .public_bytes(  # type: ignore[union-attr]
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        .decode()
    )
    new = JwtService(
        private_key_pem=generate_dev_private_key_pem(), key_id="new", issuer=ISSUER,
        previous_public_key_pem=old_public, previous_key_id="old",
    )  # fmt: skip
    assert new.decode(issue(old), audience=SESSION_AUDIENCE).user_id == USER
    assert {k["kid"] for k in new.jwks()["keys"]} == {"new", "old"}


def test_jwks_has_public_members_only(svc: JwtService) -> None:
    (key,) = svc.jwks()["keys"]
    assert set(key) == {"kty", "use", "alg", "kid", "n", "e"}
    assert key["kty"] == "RSA"
    assert key["alg"] == "RS256"


def test_jwks_key_verifies_a_token(svc: JwtService) -> None:
    """The published JWK really is the verification key (this is what P2 will do)."""
    token = issue(svc)
    (jwk,) = svc.jwks()["keys"]
    public = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))
    payload = jwt.decode(token, public, algorithms=["RS256"], audience="revops", issuer=ISSUER)  # type: ignore[arg-type]
    assert payload["sub"] == str(USER)


def test_absolute_session_limit_caps_expiry(svc: JwtService) -> None:
    started = now() - timedelta(hours=11, minutes=50)
    _, claims = svc.issue(user_id=USER, org_id=ORG, role="admin", token_version=0, audience=SESSION_AUDIENCE,
                          now=now(), auth_time=started)  # fmt: skip
    assert claims.expires_at <= started + timedelta(hours=12)


def test_weak_or_non_rsa_keys_are_refused() -> None:
    from cryptography.hazmat.primitives.asymmetric import ec, rsa

    weak = (
        rsa.generate_private_key(public_exponent=65537, key_size=1024)  # noqa: S505 - deliberately weak, to prove it is refused
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode()
    )
    with pytest.raises(ValueError, match="2048"):
        JwtService(private_key_pem=weak, key_id="k", issuer=ISSUER)
    ec_pem = (
        ec.generate_private_key(ec.SECP256R1())
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode()
    )
    with pytest.raises(ValueError, match="RSA"):
        JwtService(private_key_pem=ec_pem, key_id="k", issuer=ISSUER)
