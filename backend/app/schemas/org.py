from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field

from app.schemas.common import ApiModel, StrictModel

UdyamCategory = Literal["micro", "small", "medium", "none"]


class OrgOut(ApiModel):
    id: uuid.UUID
    name: str
    legal_name: str
    gstin: str | None
    state_code: str
    udyam_category: UdyamCategory
    address: str
    default_terms_days: int
    updated_at: datetime


class OrgPatch(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    legal_name: str | None = Field(default=None, min_length=1, max_length=200)
    gstin: str | None = Field(default=None, max_length=15)
    state_code: str | None = Field(default=None, min_length=2, max_length=2)
    udyam_category: UdyamCategory | None = None
    address: str | None = Field(default=None, max_length=500)
    default_terms_days: int | None = Field(default=None, ge=0, le=365)
