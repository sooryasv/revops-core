from __future__ import annotations

import uuid
from typing import Any

from app.core.context import AuthContext, RequestMeta
from app.db.models.customer import Customer
from app.db.uow import UnitOfWorkFactory
from app.domain.gstin import validate_gstin, validate_state_code
from app.repositories.audit import record_audit
from app.repositories.customers import CustomerRepository
from app.repositories.organizations import OrganizationRepository
from app.schemas.customers import CustomerCreate, CustomerPatch

_AUDITED = (
    "name", "gstin", "state_code", "email", "phone", "billing_address",
    "has_written_agreement", "default_terms_days", "is_active",
)  # fmt: skip


def _snapshot(customer: Customer) -> dict[str, Any]:
    return {field: getattr(customer, field) for field in _AUDITED}


def _clean_gstin(gstin: str | None, state_code: str) -> str | None:
    if gstin is None or not gstin.strip():
        return None
    return validate_gstin(gstin.strip().upper(), state_code)


class CustomerService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def search(
        self, ctx: AuthContext, *, q: str | None, is_active: bool | None, limit: int, offset: int
    ) -> tuple[list[Customer], int]:
        async with self._uow_factory() as uow:
            return await CustomerRepository(uow.session, ctx.org_id).search(
                q=q.strip() if q else None, is_active=is_active, limit=limit, offset=offset
            )

    async def get(self, ctx: AuthContext, customer_id: uuid.UUID) -> Customer:
        async with self._uow_factory() as uow:
            return await CustomerRepository(uow.session, ctx.org_id).get(customer_id)

    async def create(self, ctx: AuthContext, data: CustomerCreate, meta: RequestMeta) -> Customer:
        state = validate_state_code(data.state_code)
        gstin = _clean_gstin(data.gstin, state)
        async with self._uow_factory() as uow:
            terms = data.default_terms_days
            if terms is None:
                terms = (
                    await OrganizationRepository(uow.session, ctx.org_id).get()
                ).default_terms_days
            customer = await CustomerRepository(uow.session, ctx.org_id).create(
                name=data.name,
                gstin=gstin,
                state_code=state,
                email=str(data.email).lower() if data.email else None,
                phone=data.phone,
                billing_address=data.billing_address,
                has_written_agreement=data.has_written_agreement,
                default_terms_days=terms,
                notes=data.notes,
            )
            await uow.session.refresh(customer)
            record_audit(
                uow.session, org_id=ctx.org_id, action="customer.create", entity_type="customer",
                entity_id=customer.id, actor=ctx, after=_snapshot(customer), meta=meta,
            )  # fmt: skip
            return customer

    async def update(
        self, ctx: AuthContext, customer_id: uuid.UUID, patch: CustomerPatch, meta: RequestMeta
    ) -> Customer:
        changes = patch.model_dump(exclude_unset=True)
        async with self._uow_factory() as uow:
            repo = CustomerRepository(uow.session, ctx.org_id)
            customer = await repo.get(customer_id, for_update=True)
            before = _snapshot(customer)
            state = validate_state_code(changes.get("state_code") or customer.state_code)
            gstin = _clean_gstin(changes["gstin"], state) if "gstin" in changes else customer.gstin
            if gstin is not None and "gstin" not in changes:
                validate_gstin(gstin, state)  # a state change must still match an existing GSTIN
            for key in (
                "name",
                "billing_address",
                "has_written_agreement",
                "default_terms_days",
                "is_active",
            ):
                if key in changes and changes[key] is not None:
                    setattr(customer, key, changes[key])
            for key in ("phone", "notes"):
                if key in changes:
                    setattr(customer, key, changes[key])
            if "email" in changes:
                customer.email = str(changes["email"]).lower() if changes["email"] else None
            customer.state_code = state
            customer.gstin = gstin
            await repo.flush_changes()
            await uow.session.refresh(customer)
            record_audit(
                uow.session, org_id=ctx.org_id, action="customer.update", entity_type="customer",
                entity_id=customer.id, actor=ctx, before=before, after=_snapshot(customer), meta=meta,
            )  # fmt: skip
            return customer
