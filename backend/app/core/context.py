"""Who is calling, and for which tenant. Built only from a verified session (03 section 6.4 rule 1)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

Role = Literal["admin", "finance", "sales"]
ROLES: tuple[Role, ...] = ("admin", "finance", "sales")


@dataclass(frozen=True, slots=True)
class AuthContext:
    user_id: uuid.UUID
    org_id: uuid.UUID
    role: Role


@dataclass(frozen=True, slots=True)
class RequestMeta:
    """Request-scoped facts that end up on audit rows."""

    ip: str | None = None
    request_id: str | None = None
