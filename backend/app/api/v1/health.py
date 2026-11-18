"""Liveness and readiness. Served at the root (not under /api/v1) because container platforms probe them."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.logging import get_logger
from app.core.permissions import public
from app.db.migrations_state import current_revision, expected_head

router = APIRouter(tags=["ops"], dependencies=[Depends(public)])
_log = get_logger("health")


@router.get("/healthz", summary="Liveness: the process is up")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get(
    "/readyz", summary="Readiness: database reachable and migrations at head", response_model=None
)
async def readyz(request: Request) -> JSONResponse | dict[str, Any]:
    engine: AsyncEngine = request.app.state.engine
    checks: dict[str, str] = {}
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
        head = expected_head()
        current = await current_revision(engine)
        checks["migrations"] = "ok" if current == head else "behind"
    except Exception as exc:
        _log.warning("readiness_check_failed", exc_type=type(exc).__name__)
        checks.setdefault("database", "unreachable")
        checks.setdefault("migrations", "unknown")
    if all(value == "ok" for value in checks.values()) and len(checks) == 2:
        return {"status": "ready", "checks": checks}
    return JSONResponse({"status": "not_ready", "checks": checks}, status_code=503)
