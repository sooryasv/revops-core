"""RFC 9457 problem+json response builder. Lives in core so both middleware and the API error handlers can use it."""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse

from app.core.request_id import get_request_id

PROBLEM_CONTENT_TYPE = "application/problem+json"

TITLE_BY_STATUS = {
    400: "Bad request",
    401: "Authentication required",
    403: "Forbidden",
    404: "Not found",
    405: "Method not allowed",
    409: "Conflict",
    413: "Payload too large",
    422: "Validation failed",
    429: "Too many requests",
    500: "Internal server error",
    502: "Payment provider error",
}


def problem(
    status: int,
    code: str,
    detail: str,
    *,
    errors: list[dict[str, str]] | None = None,
    instance: str | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": f"urn:revops:problem:{code}",
        "title": TITLE_BY_STATUS.get(status, "Error"),
        "status": status,
        "detail": detail,
        "code": code,
        "errors": errors or [],
    }
    if instance:
        body["instance"] = instance
    request_id = get_request_id()
    if request_id:
        body["request_id"] = request_id
    return JSONResponse(body, status_code=status, media_type=PROBLEM_CONTENT_TYPE, headers=headers)
