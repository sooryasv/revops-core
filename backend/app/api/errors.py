"""RFC 9457 problem+json mapping (02 section 1). One place turns exceptions into HTTP responses.

Never echoes request input (it may contain passwords) and never includes stack traces or SQL (03 section 6.5).
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger
from app.core.problem import problem
from app.domain import errors as e

STATUS_BY_CODE: dict[str, int] = {
    e.ValidationFailed.code: 422,
    e.Unauthenticated.code: 401,
    e.Forbidden.code: 403,
    e.NotFound.code: 404,
    e.Conflict.code: 409,
    e.InvalidState.code: 409,
    e.RateLimited.code: 429,
    e.WebhookSignatureInvalid.code: 400,
    e.PaymentProviderError.code: 502,
}

_log = get_logger("errors")


async def _domain_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, e.DomainError)
    status = STATUS_BY_CODE.get(exc.code, 400)
    headers = {"Retry-After": str(exc.retry_after)} if isinstance(exc, e.RateLimited) else None
    return problem(
        status,
        exc.code,
        exc.message,
        errors=[{"field": fe.field, "message": fe.message} for fe in exc.errors],
        instance=request.url.path,
        headers=headers,
    )


async def _request_validation_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # Deliberately drop pydantic's `input` and `ctx`: they can echo secrets back to the caller.
    errors = [
        {
            "field": ".".join(str(part) for part in err["loc"] if part != "body"),
            "message": str(err["msg"]),
        }
        for err in exc.errors()
    ]
    return problem(
        422,
        "validation_error",
        "Request validation failed.",
        errors=errors,
        instance=request.url.path,
    )


async def _http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code = {
        401: "unauthenticated",
        403: "forbidden",
        404: "not_found",
        405: "method_not_allowed",
        413: "validation_error",  # 03 lists no dedicated code; see SPEC_ISSUES S5
        429: "rate_limited",
    }.get(exc.status_code, "validation_error" if exc.status_code < 500 else "internal_error")
    detail = "Resource not found." if exc.status_code == 404 else str(exc.detail)
    headers = dict(exc.headers) if exc.headers else None
    return problem(exc.status_code, code, detail, instance=request.url.path, headers=headers)


async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    # The type is logged, never the message: exception text can carry SQL parameters or secrets.
    _log.error(
        "unhandled_exception",
        exc_type=type(exc).__name__,
        path=request.url.path,
    )
    return problem(
        500, "internal_error", "An unexpected error occurred.", instance=request.url.path
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(e.DomainError, _domain_error_handler)
    app.add_exception_handler(RequestValidationError, _request_validation_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(Exception, _unhandled_handler)
