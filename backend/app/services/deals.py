from __future__ import annotations

import uuid
from typing import Any

from app.core.clock import Clock
from app.core.context import AuthContext, RequestMeta
from app.db.models.deal import Deal
from app.db.uow import UnitOfWorkFactory
from app.domain import deal_rules
from app.domain.errors import InvalidState
from app.repositories.audit import record_audit
from app.repositories.customers import CustomerRepository
from app.repositories.deals import DealRepository
from app.repositories.users import UserRepository
from app.schemas.deals import DealCreate, DealPatch, DealTransition
from app.services.invoices import InvoiceService, InvoiceView

_AUDITED = (
    "customer_id",
    "title",
    "stage",
    "amount_estimate_paise",
    "owner_user_id",
    "expected_close_date",
    "lost_reason",
)


def _jsonable(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _snapshot(deal: Deal) -> dict[str, Any]:
    return {field: _jsonable(getattr(deal, field)) for field in _AUDITED}


class DealService:
    def __init__(
        self, uow_factory: UnitOfWorkFactory, clock: Clock, invoices: InvoiceService
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._invoices = invoices

    async def search(
        self,
        ctx: AuthContext,
        *,
        stage: str | None,
        customer_id: uuid.UUID | None,
        limit: int,
        offset: int,
    ) -> tuple[list[Deal], int]:
        async with self._uow_factory() as uow:
            return await DealRepository(uow.session, ctx.org_id).search(
                stage=stage, customer_id=customer_id, limit=limit, offset=offset
            )

    async def get(self, ctx: AuthContext, deal_id: uuid.UUID) -> Deal:
        async with self._uow_factory() as uow:
            return await DealRepository(uow.session, ctx.org_id).get(deal_id)

    async def create(self, ctx: AuthContext, data: DealCreate, meta: RequestMeta) -> Deal:
        async with self._uow_factory() as uow:
            # Referenced ids are looked up through tenant-scoped repositories: a foreign id is simply "not found".
            await CustomerRepository(uow.session, ctx.org_id).get(data.customer_id)
            owner_id = data.owner_user_id or ctx.user_id
            await UserRepository(uow.session, ctx.org_id).require_active(owner_id)
            deal = DealRepository(uow.session, ctx.org_id).add(
                customer_id=data.customer_id,
                title=data.title,
                stage="lead",
                amount_estimate_paise=data.amount_estimate_paise,
                owner_user_id=owner_id,
                expected_close_date=data.expected_close_date,
                stage_changed_at=self._clock.now_utc(),
            )
            await uow.session.flush()
            await uow.session.refresh(deal)
            record_audit(
                uow.session, org_id=ctx.org_id, action="deal.create", entity_type="deal",
                entity_id=deal.id, actor=ctx, after=_snapshot(deal), meta=meta,
            )  # fmt: skip
            return deal

    async def update(
        self, ctx: AuthContext, deal_id: uuid.UUID, patch: DealPatch, meta: RequestMeta
    ) -> Deal:
        changes = patch.model_dump(exclude_unset=True)
        async with self._uow_factory() as uow:
            deal = await DealRepository(uow.session, ctx.org_id).get(deal_id, for_update=True)
            if deal_rules.is_terminal(deal.stage):
                raise InvalidState(f"a {deal.stage} deal can no longer be edited")
            before = _snapshot(deal)
            if changes.get("owner_user_id") is not None:
                await UserRepository(uow.session, ctx.org_id).require_active(
                    changes["owner_user_id"]
                )
                deal.owner_user_id = changes["owner_user_id"]
            for key in ("title", "amount_estimate_paise"):
                if changes.get(key) is not None:
                    setattr(deal, key, changes[key])
            if "expected_close_date" in changes:
                deal.expected_close_date = changes["expected_close_date"]
            await uow.session.flush()
            await uow.session.refresh(deal)
            record_audit(
                uow.session, org_id=ctx.org_id, action="deal.update", entity_type="deal",
                entity_id=deal.id, actor=ctx, before=before, after=_snapshot(deal), meta=meta,
            )  # fmt: skip
            return deal

    async def transition(
        self, ctx: AuthContext, deal_id: uuid.UUID, data: DealTransition, meta: RequestMeta
    ) -> Deal:
        async with self._uow_factory() as uow:
            deal = await DealRepository(uow.session, ctx.org_id).get(deal_id, for_update=True)
            before = _snapshot(deal)
            reason = deal_rules.validate_transition(deal.stage, data.stage, data.lost_reason)
            now = self._clock.now_utc()
            deal.stage = data.stage
            deal.stage_changed_at = now
            deal.lost_reason = reason
            deal.closed_at = now if deal_rules.is_terminal(data.stage) else None
            await uow.session.flush()
            await uow.session.refresh(deal)
            record_audit(
                uow.session, org_id=ctx.org_id, action="deal.transition", entity_type="deal",
                entity_id=deal.id, actor=ctx, before=before, after=_snapshot(deal), meta=meta,
            )  # fmt: skip
            return deal

    async def create_invoice(
        self, ctx: AuthContext, deal_id: uuid.UUID, meta: RequestMeta
    ) -> InvoiceView:
        """A won deal becomes a draft invoice (one active invoice per deal); the user edits and issues it."""
        return await self._invoices.create_draft_from_deal(ctx, deal_id, meta)
