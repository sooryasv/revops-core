from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import MetaDep, UserServiceDep, requires
from app.core.context import AuthContext
from app.core.permissions import Permission
from app.schemas.users import UserCreate, UserOut, UserPatch

router = APIRouter(prefix="/users", tags=["users"])

Admin = Annotated[AuthContext, Depends(requires(Permission.USERS_MANAGE))]


@router.get("", response_model=list[UserOut])
async def list_users(ctx: Admin, service: UserServiceDep) -> list[UserOut]:
    return [UserOut.model_validate(u) for u in await service.list(ctx)]


@router.post("", response_model=UserOut, status_code=201)
async def create_user(
    body: UserCreate, ctx: Admin, service: UserServiceDep, meta: MetaDep
) -> UserOut:
    return UserOut.model_validate(await service.create(ctx, body, meta))


@router.patch("/{user_id}", response_model=UserOut)
async def patch_user(
    user_id: uuid.UUID, body: UserPatch, ctx: Admin, service: UserServiceDep, meta: MetaDep
) -> UserOut:
    return UserOut.model_validate(await service.update(ctx, user_id, body, meta))
