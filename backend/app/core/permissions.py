"""RBAC: the permission matrix of 03 section 6.3 and the `requires` route dependency."""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from typing import Annotated, Any

from fastapi import Depends

from app.core.context import AuthContext, Role
from app.domain.errors import Forbidden


class Permission(StrEnum):
    ORG_READ = "org.read"
    ORG_WRITE = "org.write"
    USERS_MANAGE = "users.manage"
    CUSTOMERS_READ = "customers.read"
    CUSTOMERS_WRITE = "customers.write"
    DEALS_READ = "deals.read"
    DEALS_WRITE = "deals.write"
    INVOICES_READ = "invoices.read"
    INVOICES_DRAFT = "invoices.draft"
    INVOICES_ISSUE = "invoices.issue"
    INVOICES_VOID = "invoices.void"
    INVOICES_PAYLINK = "invoices.paylink"
    PAYMENTS_READ = "payments.read"
    PAYMENTS_MANUAL = "payments.manual"
    REPORTS_FINANCE = "reports.finance"
    REPORTS_PIPELINE = "reports.pipeline"
    AUDIT_READ = "audit.read"
    ADMIN_JOBS = "admin.jobs"


P = Permission
_FINANCE = {
    P.ORG_READ, P.CUSTOMERS_READ, P.CUSTOMERS_WRITE, P.DEALS_READ, P.INVOICES_READ, P.INVOICES_DRAFT,
    P.INVOICES_ISSUE, P.INVOICES_VOID, P.INVOICES_PAYLINK, P.PAYMENTS_READ, P.PAYMENTS_MANUAL,
    P.REPORTS_FINANCE, P.REPORTS_PIPELINE,
}  # fmt: skip
_SALES = {
    P.ORG_READ, P.CUSTOMERS_READ, P.CUSTOMERS_WRITE, P.DEALS_READ, P.DEALS_WRITE, P.INVOICES_READ,
    P.INVOICES_DRAFT, P.PAYMENTS_READ, P.REPORTS_PIPELINE,
}  # fmt: skip

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    "admin": frozenset(Permission),
    "finance": frozenset(_FINANCE),
    "sales": frozenset(_SALES),
}


def public() -> None:
    """Marker dependency: the route is deliberately reachable without a session.

    The RBAC route-table test fails for any route that has neither `requires(...)` nor this marker.
    """


public.__public__ = True  # type: ignore[attr-defined]
Public = Depends(public)


def requires(
    permission: Permission, auth_dependency: Callable[..., Any]
) -> Callable[..., AuthContext]:
    """Dependency factory: authenticate, then check the caller's role grants `permission`."""

    def dependency(ctx: AuthContext) -> AuthContext:
        if permission not in ROLE_PERMISSIONS[ctx.role]:
            raise Forbidden("You do not have permission to do this.")
        return ctx

    # With `from __future__ import annotations` a closure variable cannot appear inside an annotation string (FastAPI
    # evaluates it against module globals), so the real, resolved annotation is attached here.
    dependency.__annotations__["ctx"] = Annotated[AuthContext, Depends(auth_dependency)]
    dependency.__permission__ = permission  # type: ignore[attr-defined]
    return dependency
