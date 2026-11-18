from __future__ import annotations

from sqlalchemy import Boolean, CheckConstraint, Index, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.mixins import TenantMixin


class Customer(TenantMixin, Base):
    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        CheckConstraint("char_length(state_code) = 2", name="state_code_len"),
        CheckConstraint("default_terms_days BETWEEN 0 AND 365", name="default_terms"),
        # A GSTIN identifies one buyer inside one tenant; two tenants may each have the same buyer.
        Index(
            "uq_customers_org_gstin",
            "org_id",
            "gstin",
            unique=True,
            postgresql_where="gstin IS NOT NULL",
        ),
        Index("ix_customers_org_name", "org_id", "name"),
    )

    name: Mapped[str] = mapped_column(Text, nullable=False)
    gstin: Mapped[str | None] = mapped_column(Text)
    state_code: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    billing_address: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    has_written_agreement: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    default_terms_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default="30")
    # Free text on purpose: it is the untrusted-input fixture for the agent's prompt-injection tests (03 section 5).
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
