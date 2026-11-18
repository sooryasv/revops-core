from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.ids import new_id
from app.db.base import Base
from app.db.models.mixins import TenantMixin


class InvoiceSequence(Base):
    """One counter per (org, financial year). Incremented in the same transaction as the issue (03 section 4.3)."""

    __tablename__ = "invoice_sequences"
    __table_args__ = (
        PrimaryKeyConstraint("org_id", "fy_label"),
        CheckConstraint("last_value BETWEEN 0 AND 999999", name="last_value_range"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("organizations.id"), nullable=False)
    fy_label: Mapped[str] = mapped_column(Text, nullable=False)
    last_value: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Invoice(TenantMixin, Base):
    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "number"),  # NULLs are distinct, so many drafts are fine
        UniqueConstraint("pay_token_hash"),
        ForeignKeyConstraint(["org_id", "customer_id"], ["customers.org_id", "customers.id"]),
        ForeignKeyConstraint(["org_id", "deal_id"], ["deals.org_id", "deals.id"]),
        CheckConstraint("status IN ('draft','issued','void')", name="status"),
        CheckConstraint(
            "supply_type IS NULL OR supply_type IN ('intra','inter')", name="supply_type"
        ),
        CheckConstraint("currency = 'INR'", name="currency_inr"),
        CheckConstraint(
            "total_paise = subtotal_paise + cgst_paise + sgst_paise + igst_paise",
            name="total_matches",
        ),
        CheckConstraint(
            "igst_paise = 0 OR (cgst_paise = 0 AND sgst_paise = 0)", name="igst_exclusive"
        ),
        CheckConstraint(
            "subtotal_paise >= 0 AND cgst_paise >= 0 AND sgst_paise >= 0 AND igst_paise >= 0 "
            "AND amount_paid_paise >= 0",
            name="non_negative",
        ),
        CheckConstraint("status = 'draft' OR number IS NOT NULL", name="number_when_issued"),
        CheckConstraint(
            "number IS NULL OR char_length(number) <= 16", name="number_len"
        ),  # CGST Rule 46(b)
        CheckConstraint(
            "status = 'draft' OR (issue_date IS NOT NULL AND due_date IS NOT NULL AND supply_type IS NOT NULL)",
            name="issued_fields",
        ),
        CheckConstraint("status <> 'void' OR void_reason IS NOT NULL", name="void_needs_reason"),
        CheckConstraint("payment_terms_days BETWEEN 0 AND 365", name="terms_range"),
        CheckConstraint("char_length(place_of_supply_state_code) = 2", name="pos_len"),
        Index("ix_invoices_org_status_due", "org_id", "status", "due_date"),
        Index("ix_invoices_org_customer", "org_id", "customer_id"),
        Index("ix_invoices_org_created", "org_id", text("created_at DESC"), text("id DESC")),
    )

    customer_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    deal_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    number: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="draft")
    issue_date: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)
    payment_terms_days: Mapped[int] = mapped_column(Integer, nullable=False)
    place_of_supply_state_code: Mapped[str] = mapped_column(Text, nullable=False)
    supply_type: Mapped[str | None] = mapped_column(Text)
    subtotal_paise: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    cgst_paise: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    sgst_paise: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    igst_paise: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    total_paise: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    # Cached SUM of captured payments. Always recomputed, never incremented (03 section 4.4).
    amount_paid_paise: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    void_reason: Mapped[str | None] = mapped_column(Text)
    pay_token_hash: Mapped[str | None] = mapped_column(Text)
    pay_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default="INR")


class InvoiceItem(Base):
    __tablename__ = "invoice_items"
    __table_args__ = (
        UniqueConstraint("invoice_id", "line_no"),
        ForeignKeyConstraint(
            ["org_id", "invoice_id"], ["invoices.org_id", "invoices.id"], ondelete="CASCADE"
        ),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_price_paise >= 0", name="price_non_negative"),
        CheckConstraint("gst_rate_bp IN (0,500,1800,4000)", name="gst_rate"),
        CheckConstraint(
            "igst_paise = 0 OR (cgst_paise = 0 AND sgst_paise = 0)", name="igst_exclusive"
        ),
        CheckConstraint(
            "line_total_paise = taxable_paise + cgst_paise + sgst_paise + igst_paise",
            name="total_matches",
        ),
        Index("ix_invoice_items_org_invoice", "org_id", "invoice_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("organizations.id"), nullable=False)
    invoice_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    hsn_sac: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    gst_rate_bp: Mapped[int] = mapped_column(Integer, nullable=False)
    taxable_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cgst_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sgst_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    igst_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    line_total_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
