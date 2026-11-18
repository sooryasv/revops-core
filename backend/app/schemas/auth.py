from __future__ import annotations

import uuid
from typing import Literal

from pydantic import EmailStr, Field

from app.core.context import Role
from app.schemas.common import ApiModel, StrictModel


class LoginRequest(StrictModel):
    email: EmailStr = Field(max_length=254)
    password: str = Field(min_length=1, max_length=128)


class TokenRequest(LoginRequest):
    audience: Literal["agent"]


class TokenResponse(ApiModel):
    access_token: str
    token_type: Literal["Bearer"] = "Bearer"  # noqa: S105 - OAuth token type, not a secret
    expires_in: int


class LogoutRequest(StrictModel):
    everywhere: bool = False


class MeResponse(ApiModel):
    user_id: uuid.UUID
    org_id: uuid.UUID
    org_name: str
    email: str
    full_name: str
    role: Role
