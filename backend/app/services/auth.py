"""Login, session verification, agent tokens, logout (03 section 6.1)."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.clock import Clock
from app.core.context import AuthContext, RequestMeta
from app.core.jwt_service import (
    AGENT_AUDIENCE,
    SESSION_AUDIENCE,
    SESSION_REISSUE_BELOW,
    JwtService,
    TokenClaims,
)
from app.core.logging import get_logger
from app.core.passwords import PasswordService
from app.core.rate_limit import RateLimiter
from app.db.models.user import User
from app.db.uow import UnitOfWorkFactory
from app.domain.errors import Unauthenticated
from app.repositories.audit import record_audit
from app.repositories.organizations import OrganizationRepository
from app.repositories.users import UserRepository, find_user_for_login

_log = get_logger("auth")
_GENERIC_FAILURE = "Incorrect email or password."


@dataclass(frozen=True, slots=True)
class LoginResult:
    token: str
    claims: TokenClaims
    user: User


@dataclass(frozen=True, slots=True)
class SessionResult:
    ctx: AuthContext
    reissued_token: str | None
    reissued_claims: TokenClaims | None


@dataclass(frozen=True, slots=True)
class MeResult:
    user: User
    org_name: str


class AuthService:
    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        jwt_service: JwtService,
        passwords: PasswordService,
        limiter: RateLimiter,
        clock: Clock,
    ) -> None:
        self._uow_factory = uow_factory
        self._jwt = jwt_service
        self._passwords = passwords
        self._limiter = limiter
        self._clock = clock

    # ------------------------------------------------------------------ credentials
    async def _authenticate(
        self, email: str, password: str, meta: RequestMeta
    ) -> tuple[User, str | None]:
        """Rate-limit, then check credentials with identical cost and wording for every failure mode."""
        self._limiter.check("10/minute", "login-ip", meta.ip or "unknown")
        self._limiter.check("5/minute", "login-email", email)
        async with self._uow_factory() as uow:
            user = await find_user_for_login(uow.session, email)
            stored = user.password_hash if user is not None else None
            ok = await self._passwords.verify(stored, password)
            if user is None or not ok or not user.is_active:
                _log.info("login_failed")
                raise Unauthenticated(_GENERIC_FAILURE)
            new_hash = (
                await self._passwords.hash(password)
                if self._passwords.needs_rehash(user.password_hash)
                else None
            )
            if new_hash is not None:
                user.password_hash = new_hash
            user.last_login_at = self._clock.now_utc()
            return user, new_hash

    async def login(self, email: str, password: str, meta: RequestMeta) -> LoginResult:
        user, _ = await self._authenticate(email, password, meta)
        now = self._clock.now_utc()
        token, claims = self._jwt.issue(
            user_id=user.id,
            org_id=user.org_id,
            role=user.role,  # type: ignore[arg-type]
            token_version=user.token_version,
            audience=SESSION_AUDIENCE,
            now=now,
        )
        async with self._uow_factory() as uow:
            record_audit(
                uow.session,
                org_id=user.org_id,
                action="auth.login",
                entity_type="user",
                entity_id=user.id,
                actor=AuthContext(user.id, user.org_id, user.role),  # type: ignore[arg-type]
                meta=meta,
            )
        return LoginResult(token=token, claims=claims, user=user)

    async def agent_token(self, email: str, password: str, meta: RequestMeta) -> tuple[str, int]:
        user, _ = await self._authenticate(email, password, meta)
        token, claims = self._jwt.issue(
            user_id=user.id,
            org_id=user.org_id,
            role=user.role,  # type: ignore[arg-type]
            token_version=user.token_version,
            audience=AGENT_AUDIENCE,
            now=self._clock.now_utc(),
        )
        expires_in = int((claims.expires_at - claims.issued_at).total_seconds())
        return token, expires_in

    # ------------------------------------------------------------------ session
    async def verify_session(self, token: str) -> SessionResult:
        """Verify the cookie JWT, re-check the user row (revocation), and slide the session when it is old enough."""
        claims = self._jwt.decode(token, audience=SESSION_AUDIENCE, now=self._clock.now_utc())
        async with self._uow_factory() as uow:
            user = await UserRepository(uow.session, claims.org_id).find(claims.user_id)
        if user is None or not user.is_active or user.token_version != claims.token_version:
            raise Unauthenticated("Invalid session.")
        now = self._clock.now_utc()
        reissued: tuple[str, TokenClaims] | None = None
        if claims.expires_at - now < SESSION_REISSUE_BELOW:
            candidate = self._jwt.issue(
                user_id=user.id,
                org_id=user.org_id,
                role=user.role,  # type: ignore[arg-type]
                token_version=user.token_version,
                audience=SESSION_AUDIENCE,
                now=now,
                auth_time=claims.auth_time,  # the 12 h absolute limit counts from the original login
            )
            if candidate[1].expires_at > claims.expires_at:
                reissued = candidate
        # The role in the database wins over the role baked into an older token.
        ctx = AuthContext(user.id, user.org_id, user.role)  # type: ignore[arg-type]
        return SessionResult(
            ctx=ctx,
            reissued_token=reissued[0] if reissued else None,
            reissued_claims=reissued[1] if reissued else None,
        )

    async def me(self, ctx: AuthContext) -> MeResult:
        async with self._uow_factory() as uow:
            user = await UserRepository(uow.session, ctx.org_id).get(ctx.user_id)
            org = await OrganizationRepository(uow.session, ctx.org_id).get()
            return MeResult(user=user, org_name=org.name)

    async def logout(self, ctx: AuthContext, *, everywhere: bool, meta: RequestMeta) -> None:
        async with self._uow_factory() as uow:
            if everywhere:
                user = await UserRepository(uow.session, ctx.org_id).get(
                    ctx.user_id, for_update=True
                )
                user.token_version += 1
            record_audit(
                uow.session,
                org_id=ctx.org_id,
                action="auth.logout_everywhere" if everywhere else "auth.logout",
                entity_type="user",
                entity_id=ctx.user_id,
                actor=ctx,
                meta=meta,
            )
