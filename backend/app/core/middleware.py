"""Pure-ASGI middleware: request id and access log.

Pure ASGI (not BaseHTTPMiddleware) so streaming bodies, contextvars and exceptions behave predictably.
"""

from __future__ import annotations

import re
import time
import uuid

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import get_logger
from app.core.problem import problem
from app.core.request_id import request_id_var

REQUEST_ID_HEADER = "x-request-id"
# Accept only boring ids so a caller cannot inject newlines or huge strings into logs and audit rows.
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")

_log = get_logger("access")


def _incoming_request_id(scope: Scope) -> str:
    for name, value in scope["headers"]:
        if name == REQUEST_ID_HEADER.encode():
            candidate: str = value.decode("latin-1")
            if _SAFE_REQUEST_ID.match(candidate):
                return candidate
    return uuid.uuid4().hex


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _incoming_request_id(scope)
        token = request_id_var.set(request_id)
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started = time.perf_counter()
        status_holder = {"status": 500}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                headers = list(message.get("headers", []))
                headers.append((REQUEST_ID_HEADER.encode(), request_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            route = scope.get("route")
            _log.info(
                "request",
                method=scope["method"],
                route=getattr(route, "path", None) or "unmatched",
                status=status_holder["status"],
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
            )
            request_id_var.reset(token)
            structlog.contextvars.clear_contextvars()


# ----------------------------------------------------------------------------- origin check and body limit

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class OriginCheckMiddleware:
    """CSRF defence next to SameSite=Lax (03 section 6.2): when a browser sends Origin on an unsafe request, it must
    be one we allow. No Origin header (curl, servers, the agent) is let through; they carry no ambient cookie risk.
    The webhook route authenticates by signature, not by cookie, and is exempt."""

    def __init__(
        self, app: ASGIApp, *, allowed_origins: list[str], exempt_prefixes: tuple[str, ...]
    ) -> None:
        self.app = app
        self._allowed = frozenset(allowed_origins)
        self._exempt = exempt_prefixes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] == "http"
            and scope["method"] in UNSAFE_METHODS
            and not scope["path"].startswith(self._exempt)
        ):
            for name, value in scope["headers"]:
                if name == b"origin" and value.decode("latin-1") not in self._allowed:
                    response = problem(
                        403, "forbidden", "Cross-origin request rejected.", instance=scope["path"]
                    )
                    await response(scope, receive, send)
                    return
        await self.app(scope, receive, send)


class BodySizeLimitMiddleware:
    """Reject bodies over `max_bytes` (1 MB, 03 section 6.5): by Content-Length up front, and by counting as it streams.

    Streaming overflow cannot be signalled by raising from `receive`: FastAPI's body parsing catches that and answers
    400. Instead the wrapped receive reports a disconnect, anything the app then tries to send is discarded, and this
    middleware sends the 413 itself."""

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self._max = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        reject = problem(413, "validation_error", "Request body too large.", instance=scope["path"])
        for name, value in scope["headers"]:
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = 0
                if declared > self._max:
                    await reject(scope, receive, send)
                    return

        seen = 0
        exceeded = False

        async def counting_receive() -> Message:
            nonlocal seen, exceeded
            if exceeded:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self._max:
                    exceeded = True
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message: Message) -> None:
            if not exceeded:
                await send(message)

        try:
            await self.app(scope, counting_receive, guarded_send)
        except Exception:
            if not exceeded:
                raise
        if exceeded:
            await reject(scope, receive, send)
