"""Application factory. `create_app` builds everything from Settings so tests can build isolated apps."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI

from app.api.errors import register_exception_handlers
from app.api.v1 import (
    auth,
    customers,
    deals,
    health,
    invoices,
    org,
    payments,
    public_pay,
    reports,
    users,
    webhooks,
    wellknown,
)
from app.config import Settings, get_settings
from app.core.clock import Clock, SystemClock
from app.core.jwt_service import JwtService, generate_dev_private_key_pem
from app.core.logging import configure_logging, get_logger
from app.core.middleware import (
    BodySizeLimitMiddleware,
    OriginCheckMiddleware,
    RequestContextMiddleware,
)
from app.core.passwords import PasswordService, make_hasher
from app.core.rate_limit import RateLimiter
from app.core.security_headers import SecurityHeadersMiddleware
from app.db.session import make_engine, make_sessionmaker
from app.db.uow import make_uow_factory
from app.payments.fake import FakeProvider
from app.payments.provider import PaymentProvider
from app.payments.razorpay import RazorpayProvider
from app.services.auth import AuthService
from app.services.customers import CustomerService
from app.services.deals import DealService
from app.services.invoices import InvoiceService
from app.services.org import OrgService
from app.services.payments import PaymentService
from app.services.public_pay import PublicPayService
from app.services.reports import ReportService
from app.services.users import UserService
from app.services.webhooks import WebhookService
from app.spa import mount_spa

MAX_BODY_BYTES = 1_000_000
# Signature-authenticated, cookie-free routes are exempt from the Origin check (03 section 6.2).
ORIGIN_EXEMPT_PREFIXES = ("/api/v1/webhooks/",)


def build_jwt_service(settings: Settings) -> JwtService:
    if settings.jwt_private_key_pem is not None:
        pem = settings.jwt_private_key_pem.get_secret_value()
    elif (
        settings.is_production
    ):  # also guarded in Settings; never generate a key silently in production
        raise RuntimeError("JWT_PRIVATE_KEY_PEM is required in production")
    else:
        pem = generate_dev_private_key_pem()
        get_logger().warning("jwt_ephemeral_dev_key", note="sessions end when the process restarts")
    return JwtService(
        private_key_pem=pem,
        key_id=settings.jwt_key_id,
        issuer=settings.jwt_issuer,
        previous_public_key_pem=settings.jwt_previous_public_key_pem,
        previous_key_id=settings.jwt_previous_key_id,
    )


def build_provider(settings: Settings) -> PaymentProvider:
    if settings.payment_provider == "razorpay":
        if not (
            settings.razorpay_key_id
            and settings.razorpay_key_secret
            and settings.razorpay_webhook_secret
        ):
            raise RuntimeError("Razorpay credentials are required when PAYMENT_PROVIDER=razorpay")
        return RazorpayProvider(
            key_id=settings.razorpay_key_id,
            key_secret=settings.razorpay_key_secret.get_secret_value(),
            webhook_secret=settings.razorpay_webhook_secret.get_secret_value(),
        )
    return FakeProvider(
        key_secret=settings.fake_key_secret, webhook_secret=settings.fake_webhook_secret
    )


def build_password_service(settings: Settings) -> PasswordService:
    return PasswordService(make_hasher(fast_for_tests=settings.env == "test"))


def create_app(settings: Settings | None = None, *, clock: Clock | None = None) -> FastAPI:
    """`clock` drives business dates (invoice issue and due dates, overdue, deal timestamps). Authentication always
    uses the real clock, because JWT libraries validate against wall time."""
    settings = settings or get_settings()
    configure_logging(settings.log_level, json_output=settings.env != "dev")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = make_engine(
            settings.database_url, statement_timeout_ms=settings.db_statement_timeout_ms
        )
        sessionmaker = make_sessionmaker(engine)
        app.state.engine = engine
        app.state.sessionmaker = sessionmaker
        uow_factory = make_uow_factory(sessionmaker)
        app.state.uow_factory = uow_factory
        passwords = build_password_service(settings)
        jwt_service = build_jwt_service(settings)
        business_clock: Clock = clock or SystemClock()
        limiter = RateLimiter()
        provider = build_provider(settings)
        app.state.provider = provider
        app.state.clock = business_clock
        app.state.jwt = jwt_service
        app.state.auth_service = AuthService(
            uow_factory=uow_factory,
            jwt_service=jwt_service,
            passwords=passwords,
            limiter=limiter,
            clock=SystemClock(),
        )
        app.state.customer_service = CustomerService(uow_factory)
        invoice_service = InvoiceService(uow_factory, business_clock, settings.public_base_url)
        app.state.invoice_service = invoice_service
        app.state.payment_service = PaymentService(uow_factory, business_clock)
        app.state.report_service = ReportService(uow_factory, business_clock)
        app.state.public_pay_service = PublicPayService(
            uow_factory, business_clock, provider, limiter
        )
        app.state.webhook_service = WebhookService(uow_factory, business_clock, provider)
        app.state.deal_service = DealService(uow_factory, business_clock, invoice_service)
        app.state.org_service = OrgService(uow_factory)
        app.state.user_service = UserService(uow_factory, passwords)
        get_logger().info("startup", env=settings.env)
        try:
            yield
        finally:
            await engine.dispose()

    docs_enabled = not settings.is_production
    app = FastAPI(
        title="RevOps Core",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )
    app.state.settings = settings

    # add_middleware wraps outward: the last one added runs first. Request id must be outermost so every
    # response, including rejections from the layers below, carries it.
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=MAX_BODY_BYTES)
    app.add_middleware(
        OriginCheckMiddleware,
        allowed_origins=settings.allowed_origins,
        exempt_prefixes=ORIGIN_EXEMPT_PREFIXES,
    )
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.is_production)
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(wellknown.router)
    api = APIRouter(prefix="/api/v1")
    api.include_router(auth.router)
    api.include_router(org.router)
    api.include_router(users.router)
    api.include_router(customers.router)
    api.include_router(deals.router)
    api.include_router(invoices.router)
    api.include_router(payments.router)
    api.include_router(public_pay.router)
    api.include_router(webhooks.router)
    api.include_router(reports.router)
    app.include_router(api)
    mount_spa(app, Path(settings.static_dir))  # last, so API and ops routes win
    return app
