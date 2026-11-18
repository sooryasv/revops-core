from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import EmailStr, Field

from app.schemas.common import ApiModel, StrictModel


class CustomerOut(ApiModel):
    id: uuid.UUID
    name: str
    gstin: str | None
    state_code: str
    email: str | None
    phone: str | None
    billing_address: str
    has_written_agreement: bool
    default_terms_days: int
    notes: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class CustomerCreate(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    gstin: str | None = Field(default=None, max_length=15)
    state_code: str = Field(min_length=2, max_length=2)
    email: EmailStr | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=32)
    billing_address: str = Field(default="", max_length=500)
    has_written_agreement: bool = False
    default_terms_days: int | None = Field(
        default=None, ge=0, le=365
    )  # None: use the organization's default
    notes: str | None = Field(default=None, max_length=5000)


class CustomerPatch(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    gstin: str | None = Field(default=None, max_length=15)
    state_code: str | None = Field(default=None, min_length=2, max_length=2)
    email: EmailStr | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=32)
    billing_address: str | None = Field(default=None, max_length=500)
    has_written_agreement: bool | None = None
    default_terms_days: int | None = Field(default=None, ge=0, le=365)
    notes: str | None = Field(default=None, max_length=5000)
    is_active: bool | None = None
