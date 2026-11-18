from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.core.jwt_service import JwtService
from app.core.permissions import public

router = APIRouter(tags=["auth"], dependencies=[Depends(public)])


@router.get("/.well-known/jwks.json", summary="Public signing keys (verification only)")
async def jwks(request: Request) -> dict[str, list[dict[str, str]]]:
    jwt_service: JwtService = request.app.state.jwt
    return jwt_service.jwks()
