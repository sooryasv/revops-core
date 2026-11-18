from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.mixins import TenantMixin


class Deal(TenantMixin, Base):
    __tablename__ = "deals"
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        ForeignKeyConstraint(["org_id", "customer_id"], ["customers.org_id", "customers.id"]),
        ForeignKeyConstraint(["org_id", "owner_user_id"], ["users.org_id", "users.id"]),
        CheckConstraint("stage IN ('lead','qualified','proposal','won','lost')", name="stage"),
        CheckConstraint("amount_estimate_paise >= 0", name="amount_non_negative"),
        CheckConstraint("stage <> 'lost' OR lost_reason IS NOT NULL", name="lost_needs_reason"),
        CheckConstraint(
            "(stage IN ('won','lost')) = (closed_at IS NOT NULL)", name="closed_matches_stage"
        ),
        Index("ix_deals_org_stage", "org_id", "stage"),
    )

    customer_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    stage: Mapped[str] = mapped_column(Text, nullable=False, server_default="lead")
    amount_estimate_paise: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    owner_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    expected_close_date: Mapped[date | None] = mapped_column(Date)
    lost_reason: Mapped[str | None] = mapped_column(Text)
    stage_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
