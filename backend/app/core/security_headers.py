"""Security headers on every response (03 section 6.5)."""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

# U2 (partly verified in M8): the script host is documented; frame/connect domains are not and must be checked in a browser.
RAZORPAY_SCRIPT = "https://checkout.razorpay.com"
RAZORPAY_FRAME = "https://api.razorpay.com https://checkout.razorpay.com"
RAZORPAY_CONNECT = "https://api.razorpay.com https://lumberjack.razorpay.com"


def content_security_policy() -> str:
    return "; ".join(
        [
            "default-src 'self'",
            f"script-src 'self' {RAZORPAY_SCRIPT}",
            f"frame-src {RAZORPAY_FRAME}",
            f"connect-src 'self' {RAZORPAY_CONNECT}",
            "img-src 'self' data: https:",
            "style-src 'self' 'unsafe-inline'",
            "frame-ancestors 'none'",  # the pay page must not be framed
            "base-uri 'self'",
            "form-action 'self'",
            "object-src 'none'",
        ]
    )


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, hsts: bool) -> None:
        self.app = app
        self._hsts = hsts
        self._csp = content_security_policy()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        is_api = scope["path"].startswith("/api/")

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                extra: list[tuple[bytes, bytes]] = [
                    (b"content-security-policy", self._csp.encode()),
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"strict-origin-when-cross-origin"),
                    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
                ]
                if self._hsts:
                    extra.append(
                        (b"strict-transport-security", b"max-age=31536000; includeSubDomains")
                    )
                if is_api:
                    extra.append((b"cache-control", b"no-store"))
                message = {**message, "headers": [*headers, *extra]}
            await send(message)

        await self.app(scope, receive, send_wrapper)
