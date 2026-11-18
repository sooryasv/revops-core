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
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.mixins import TenantMixin


class PaymentOrder(TenantMixin, Base):
    """One checkout intent. At most one OPEN (status 'created') order per invoice, enforced by a partial unique index."""

    __tablename__ = "payment_orders"
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("provider", "provider_order_id"),
        UniqueConstraint("receipt"),
        ForeignKeyConstraint(["org_id", "invoice_id"], ["invoices.org_id", "invoices.id"]),
        CheckConstraint("provider IN ('razorpay')", name="provider"),
        CheckConstraint(
            "char_length(receipt) <= 40", name="receipt_len"
        ),  # Razorpay limit (02 section 2.1)
        CheckConstraint("amount_paise > 0", name="amount_positive"),
        CheckConstraint("status IN ('created','paid','superseded')", name="status"),
        Index(
            "uq_payment_orders_open_per_invoice",
            "invoice_id",
            unique=True,
            postgresql_where="status = 'created'",
        ),
    )

    invoice_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False, server_default="razorpay")
    provider_order_id: Mapped[str] = mapped_column(Text, nullable=False)
    receipt: Mapped[str] = mapped_column(Text, nullable=False)
    amount_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="created")


class Payment(TenantMixin, Base):
    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("provider", "provider_payment_id"),
        ForeignKeyConstraint(["org_id", "invoice_id"], ["invoices.org_id", "invoices.id"]),
        ForeignKeyConstraint(
            ["org_id", "payment_order_id"], ["payment_orders.org_id", "payment_orders.id"]
        ),
        ForeignKeyConstraint(["org_id", "recorded_by_user_id"], ["users.org_id", "users.id"]),
        CheckConstraint("provider IN ('razorpay','manual')", name="provider"),
        CheckConstraint("amount_paise > 0", name="amount_positive"),
        CheckConstraint("status IN ('authorized','captured','failed')", name="status"),
        CheckConstraint(
            "status <> 'captured' OR (captured_at IS NOT NULL AND paid_on IS NOT NULL)",
            name="captured_fields",
        ),
        Index("ix_payments_org_invoice", "org_id", "invoice_id"),
        Index("ix_payments_org_paid_on", "org_id", "paid_on"),
    )

    invoice_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    payment_order_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    provider_payment_id: Mapped[str] = mapped_column(Text, nullable=False)
    amount_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    method: Mapped[str | None] = mapped_column(Text)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_on: Mapped[date | None] = mapped_column(Date)  # IST calendar date of capture
    failure_code: Mapped[str | None] = mapped_column(Text)
    recorded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    reference: Mapped[str | None] = mapped_column(Text)
