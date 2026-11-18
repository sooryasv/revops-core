from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.ids import new_id
from app.db.base import Base


class Organization(Base):
    """The tenant root. The only tenant table without an org_id of its own (its id IS the org id)."""

    __tablename__ = "organizations"
    __table_args__ = (
        CheckConstraint("char_length(state_code) = 2", name="state_code_len"),
        CheckConstraint(
            "udyam_category IN ('micro','small','medium','none')", name="udyam_category"
        ),
        CheckConstraint("default_terms_days BETWEEN 0 AND 365", name="default_terms"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    legal_name: Mapped[str] = mapped_column(Text, nullable=False)
    gstin: Mapped[str | None] = mapped_column(Text)
    state_code: Mapped[str] = mapped_column(Text, nullable=False)
    udyam_category: Mapped[str] = mapped_column(Text, nullable=False, server_default="none")
    address: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    default_terms_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default="30")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
