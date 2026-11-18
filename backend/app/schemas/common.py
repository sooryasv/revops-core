"""Shared DTO bases and the paging envelope."""

from __future__ import annotations

from typing import Annotated

from fastapi import Query
from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Request bodies: unknown fields are rejected and strings are trimmed (03 section 6.5)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ApiModel(BaseModel):
    """Response models read from ORM objects."""

    model_config = ConfigDict(from_attributes=True)


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


Limit = Annotated[int, Query(ge=1, le=100, description="page size")]
Offset = Annotated[int, Query(ge=0, le=1_000_000)]
