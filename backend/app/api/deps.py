"""FastAPI dependencies: settings, services, session authentication and the `requires(...)` permission guard."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Request, Response

from app.config import Settings
from app.core.context import AuthContext, RequestMeta
from app.core.permissions import Permission
from app.core.permissions import requires as _requires
from app.core.request_id import get_request_id
from app.db.uow import UnitOfWorkFactory
from app.domain.errors import Unauthenticated
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


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_uow_factory(request: Request) -> UnitOfWorkFactory:
    factory: UnitOfWorkFactory = request.app.state.uow_factory
    return factory


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
UowFactoryDep = Annotated[UnitOfWorkFactory, Depends(get_uow_factory)]


def get_auth_service(request: Request) -> AuthService:
    service: AuthService = request.app.state.auth_service
    return service


def get_org_service(request: Request) -> OrgService:
    service: OrgService = request.app.state.org_service
    return service


def get_user_service(request: Request) -> UserService:
    service: UserService = request.app.state.user_service
    return service


def get_customer_service(request: Request) -> CustomerService:
    service: CustomerService = request.app.state.customer_service
    return service


def get_deal_service(request: Request) -> DealService:
    service: DealService = request.app.state.deal_service
    return service


CustomerServiceDep = Annotated[CustomerService, Depends(get_customer_service)]


def get_invoice_service(request: Request) -> InvoiceService:
    service: InvoiceService = request.app.state.invoice_service
    return service


def get_payment_service(request: Request) -> PaymentService:
    service: PaymentService = request.app.state.payment_service
    return service


def get_public_pay_service(request: Request) -> PublicPayService:
    service: PublicPayService = request.app.state.public_pay_service
    return service


def get_webhook_service(request: Request) -> WebhookService:
    service: WebhookService = request.app.state.webhook_service
    return service


def get_report_service(request: Request) -> ReportService:
    service: ReportService = request.app.state.report_service
    return service


ReportServiceDep = Annotated[ReportService, Depends(get_report_service)]
PaymentServiceDep = Annotated[PaymentService, Depends(get_payment_service)]
InvoiceServiceDep = Annotated[InvoiceService, Depends(get_invoice_service)]
DealServiceDep = Annotated[DealService, Depends(get_deal_service)]
AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
OrgServiceDep = Annotated[OrgService, Depends(get_org_service)]
UserServiceDep = Annotated[UserService, Depends(get_user_service)]


def client_ip(request: Request) -> str | None:
    # Behind Azure Container Apps the first X-Forwarded-For hop is the client; the proxy appends, so trust the
    # left-most entry only when the platform sets it. Documented limitation in docs/DECISIONS.md.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64] or None
    return request.client.host if request.client else None


def get_request_meta(request: Request) -> RequestMeta:
    return RequestMeta(ip=client_ip(request), request_id=get_request_id())


MetaDep = Annotated[RequestMeta, Depends(get_request_meta)]


def set_session_cookie(response: Response, settings: Settings, token: str, max_age: int) -> None:
    response.set_cookie(
        settings.cookie_name,
        token,
        max_age=max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        settings.cookie_name,
        path="/",
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )


async def authenticate(
    request: Request, response: Response, settings: SettingsDep, auth: AuthServiceDep
) -> AuthContext:
    token = request.cookies.get(settings.cookie_name)
    if not token:
        raise Unauthenticated("Authentication required.")
    result = await auth.verify_session(token)
    if result.reissued_token is not None and result.reissued_claims is not None:
        claims = result.reissued_claims
        max_age = int((claims.expires_at - claims.issued_at).total_seconds())
        set_session_cookie(response, settings, result.reissued_token, max_age)
    return result.ctx


def requires(permission: Permission) -> Callable[..., AuthContext]:
    return _requires(permission, authenticate)


# Any signed-in user (every role holds org.read); used by /auth/me and /auth/logout.
CurrentUser = Annotated[AuthContext, Depends(requires(Permission.ORG_READ))]
