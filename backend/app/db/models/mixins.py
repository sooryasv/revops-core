"""Columns shared by every tenant table (03 section 5)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.ids import new_id


class TenantMixin:
    """id, org_id, created_at, updated_at. Every tenant table also declares UNIQUE (org_id, id) so child tables can
    reference it with a composite foreign key (org_id, parent_id): a cross-tenant link is then impossible in SQL."""

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("organizations.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # "Trigger-free": the application sets updated_at on every UPDATE (onupdate) instead of a database trigger.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
