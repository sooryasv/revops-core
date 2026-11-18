from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import MetaDep, OrgServiceDep, requires
from app.core.context import AuthContext
from app.core.permissions import Permission
from app.schemas.org import OrgOut, OrgPatch

router = APIRouter(prefix="/org", tags=["org"])

ReadCtx = Annotated[AuthContext, Depends(requires(Permission.ORG_READ))]
WriteCtx = Annotated[AuthContext, Depends(requires(Permission.ORG_WRITE))]


@router.get("", response_model=OrgOut)
async def get_org(ctx: ReadCtx, service: OrgServiceDep) -> OrgOut:
    return OrgOut.model_validate(await service.get(ctx))


@router.patch("", response_model=OrgOut)
async def patch_org(body: OrgPatch, ctx: WriteCtx, service: OrgServiceDep, meta: MetaDep) -> OrgOut:
    return OrgOut.model_validate(await service.update(ctx, body, meta))
