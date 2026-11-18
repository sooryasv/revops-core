"""Serves the built React SPA from the same origin as the API (03 section 1, 13).

Real files under the static directory are served as they are; any other non-API GET returns index.html so client-side
routes (/invoices/123, /pay/<token>) survive a refresh. Paths under /api/ never fall back to the SPA: an unknown API
route must stay a JSON 404, not an HTML page."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, Response

from app.core.permissions import public

_NO_CACHE = {"Cache-Control": "no-cache"}
_IMMUTABLE = {"Cache-Control": "public, max-age=31536000, immutable"}
# Paths served by the API itself (or reserved) that must never be answered with index.html.
_RESERVED_PREFIXES = ("api/", ".well-known/", "docs", "openapi.json", "healthz", "readyz")


def mount_spa(app: FastAPI, static_dir: Path) -> bool:
    """Register the SPA routes last. Returns False (and registers nothing) when no build exists, e.g. in API-only runs."""
    index = static_dir / "index.html"
    if not index.is_file():
        return False
    root = static_dir.resolve()
    router = APIRouter(dependencies=[Depends(public)], include_in_schema=False)

    @router.get("/{path:path}")
    async def spa(path: str) -> Response:
        if path.startswith(_RESERVED_PREFIXES):
            raise HTTPException(status_code=404)
        candidate = (root / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(root):
            headers = _IMMUTABLE if path.startswith("assets/") else _NO_CACHE
            return FileResponse(candidate, headers=headers)
        return FileResponse(index, headers=_NO_CACHE)

    app.include_router(router)
    return True
