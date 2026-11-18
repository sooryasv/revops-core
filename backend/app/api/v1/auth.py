from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from app.api.deps import (
    AuthServiceDep,
    CurrentUser,
    MetaDep,
    SettingsDep,
    clear_session_cookie,
    set_session_cookie,
)
from app.core.permissions import public
from app.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    MeResponse,
    TokenRequest,
    TokenResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", dependencies=[Depends(public)], response_model=MeResponse)
async def login(
    body: LoginRequest,
    response: Response,
    settings: SettingsDep,
    auth: AuthServiceDep,
    meta: MetaDep,
) -> MeResponse:
    result = await auth.login(str(body.email), body.password, meta)
    claims = result.claims
    set_session_cookie(
        response,
        settings,
        result.token,
        int((claims.expires_at - claims.issued_at).total_seconds()),
    )
    me = await auth.me(claims.context())
    return MeResponse(
        user_id=me.user.id,
        org_id=me.user.org_id,
        org_name=me.org_name,
        email=me.user.email,
        full_name=me.user.full_name,
        role=me.user.role,  # type: ignore[arg-type]
    )


@router.post("/logout", status_code=204)
async def logout(
    response: Response,
    settings: SettingsDep,
    auth: AuthServiceDep,
    ctx: CurrentUser,
    meta: MetaDep,
    body: LogoutRequest | None = None,
) -> None:
    await auth.logout(ctx, everywhere=bool(body and body.everywhere), meta=meta)
    clear_session_cookie(response, settings)


@router.get("/me", response_model=MeResponse)
async def me(auth: AuthServiceDep, ctx: CurrentUser) -> MeResponse:
    result = await auth.me(ctx)
    return MeResponse(
        user_id=result.user.id,
        org_id=result.user.org_id,
        org_name=result.org_name,
        email=result.user.email,
        full_name=result.user.full_name,
        role=result.user.role,  # type: ignore[arg-type]
    )


@router.post("/token", dependencies=[Depends(public)], response_model=TokenResponse)
async def token(body: TokenRequest, auth: AuthServiceDep, meta: MetaDep) -> TokenResponse:
    """Programmatic token for the agent service only (02 section 4): audience is fixed to `agent`, 30 minutes."""
    access_token, expires_in = await auth.agent_token(str(body.email), body.password, meta)
    return TokenResponse(access_token=access_token, expires_in=expires_in)
