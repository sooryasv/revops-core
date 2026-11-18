from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import Field

from app.schemas.common import ApiModel, StrictModel

Stage = Literal["lead", "qualified", "proposal", "won", "lost"]
MAX_ESTIMATE_PAISE = 10**13


class DealOut(ApiModel):
    id: uuid.UUID
    customer_id: uuid.UUID
    title: str
    stage: Stage
    amount_estimate_paise: int
    owner_user_id: uuid.UUID
    expected_close_date: date | None
    lost_reason: str | None
    stage_changed_at: datetime
    closed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class DealCreate(StrictModel):
    customer_id: uuid.UUID
    title: str = Field(min_length=1, max_length=200)
    amount_estimate_paise: int = Field(default=0, ge=0, le=MAX_ESTIMATE_PAISE)
    owner_user_id: uuid.UUID | None = None  # None: the caller
    expected_close_date: date | None = None


class DealPatch(StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    amount_estimate_paise: int | None = Field(default=None, ge=0, le=MAX_ESTIMATE_PAISE)
    owner_user_id: uuid.UUID | None = None
    expected_close_date: date | None = None


class DealTransition(StrictModel):
    stage: Stage
    lost_reason: str | None = Field(default=None, max_length=500)
