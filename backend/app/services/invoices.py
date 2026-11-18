"""Invoice drafting, issuing (gap-free numbering), voiding and pay-links (03 sections 4.3, 4.4, 8.2)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.core.clock import Clock
from app.core.context import AuthContext, RequestMeta
from app.core.paytoken import PAY_TOKEN_DAYS, generate_pay_token
from app.db.models.invoice import Invoice, InvoiceItem
from app.db.uow import UnitOfWork, UnitOfWorkFactory
from app.domain import deal_rules, invoice_rules
from app.domain.errors import Conflict, FieldError, InvalidState, ValidationFailed
from app.domain.fiscal_year import fy_label_for
from app.domain.gst import InvoiceTotals, LineInput, compute_invoice
from app.domain.gstin import validate_state_code
from app.domain.invoice_number import format_invoice_number
from app.domain.statutory import statutory_limit_days, terms_exceed_statutory
from app.repositories.audit import record_audit
from app.repositories.customers import CustomerRepository
from app.repositories.deals import DealRepository
from app.repositories.invoices import InvoiceRepository, InvoiceRow
from app.repositories.organizations import OrganizationRepository
from app.schemas.invoices import InvoiceCreate, InvoicePreviewRequest, InvoiceReplace, ItemIn

DEFAULT_DEAL_GST_RATE_BP = (
    1800  # a draft from a deal starts at 18%; the user edits it before issuing
)


@dataclass(frozen=True, slots=True)
class LineSpec:
    description: str
    hsn_sac: str | None
    quantity: int
    unit_price_paise: int
    gst_rate_bp: int


@dataclass(frozen=True, slots=True)
class InvoiceView:
    invoice: Invoice
    customer_name: str
    derived: dict[str, Any]
    items: list[InvoiceItem] | None = None


def _specs(items: list[ItemIn]) -> list[LineSpec]:
    return [
        LineSpec(i.description, i.hsn_sac, i.quantity, i.unit_price_paise, i.gst_rate_bp)
        for i in items
    ]


def _totals(supplier_state: str, pos: str, specs: list[LineSpec]) -> InvoiceTotals:
    return compute_invoice(
        supplier_state,
        pos,
        [LineInput(s.quantity, s.unit_price_paise, s.gst_rate_bp) for s in specs],
    )


def _item_rows(specs: list[LineSpec], totals: InvoiceTotals) -> list[dict[str, Any]]:
    return [
        {
            "line_no": n,
            "description": s.description,
            "hsn_sac": s.hsn_sac,
            "quantity": s.quantity,
            "unit_price_paise": s.unit_price_paise,
            "gst_rate_bp": s.gst_rate_bp,
            "taxable_paise": t.taxable_paise,
            "cgst_paise": t.cgst_paise,
            "sgst_paise": t.sgst_paise,
            "igst_paise": t.igst_paise,
            "line_total_paise": t.line_total_paise,
        }
        for n, (s, t) in enumerate(zip(specs, totals.lines, strict=True), start=1)
    ]


def _apply_totals(invoice: Invoice, totals: InvoiceTotals) -> None:
    invoice.subtotal_paise = totals.subtotal_paise
    invoice.cgst_paise = totals.cgst_paise
    invoice.sgst_paise = totals.sgst_paise
    invoice.igst_paise = totals.igst_paise
    invoice.total_paise = totals.total_paise


def _audit_snapshot(invoice: Invoice) -> dict[str, Any]:
    return {
        "number": invoice.number,
        "status": invoice.status,
        "customer_id": str(invoice.customer_id),
        "total_paise": invoice.total_paise,
        "issue_date": invoice.issue_date.isoformat() if invoice.issue_date else None,
        "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
        "version": invoice.version,
    }


def derive(
    invoice: Invoice, *, has_agreement: bool, udyam_category: str, today: date, now: datetime
) -> dict[str, Any]:
    due = invoice_rules.amount_due(invoice.total_paise, invoice.amount_paid_paise)
    limit = statutory_limit_days(udyam_category, has_agreement)
    return {
        "payment_status": invoice_rules.payment_status(
            invoice.total_paise, invoice.amount_paid_paise
        ),
        "amount_due_paise": due,
        "overpaid_paise": invoice_rules.overpaid(invoice.total_paise, invoice.amount_paid_paise),
        "is_overdue": invoice_rules.is_overdue(invoice.status, invoice.due_date, due, today=today),
        "days_overdue": invoice_rules.days_overdue(
            invoice.status, invoice.due_date, due, today=today
        ),
        "aging_bucket": invoice_rules.aging_bucket(
            invoice.status, invoice.due_date, due, today=today
        ),
        "statutory_limit_days": limit,
        "terms_exceed_statutory": terms_exceed_statutory(limit, invoice.payment_terms_days),
        "pay_link_active": bool(
            invoice.pay_token_hash
            and invoice.pay_token_expires_at
            and invoice.pay_token_expires_at > now
        ),
    }


class InvoiceService:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock, public_base_url: str) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._base_url = public_base_url.rstrip("/")

    # ------------------------------------------------------------------ views
    async def _view(
        self,
        uow: UnitOfWork,
        ctx: AuthContext,
        row: InvoiceRow,
        *,
        with_items: bool,
        udyam: str | None = None,
    ) -> InvoiceView:
        repo = InvoiceRepository(uow.session, ctx.org_id)
        if udyam is None:
            udyam = (await OrganizationRepository(uow.session, ctx.org_id).get()).udyam_category
        derived = derive(
            row.invoice,
            has_agreement=row.has_written_agreement,
            udyam_category=udyam,
            today=self._clock.today_ist(),
            now=self._clock.now_utc(),
        )
        items = await repo.items(row.invoice.id) if with_items else None
        return InvoiceView(row.invoice, row.customer_name, derived, items)

    async def _view_of(
        self, uow: UnitOfWork, ctx: AuthContext, invoice_id: uuid.UUID
    ) -> InvoiceView:
        row = await InvoiceRepository(uow.session, ctx.org_id).find_row(invoice_id)
        assert (
            row is not None
        )  # the caller just loaded or wrote this invoice in the same transaction
        return await self._view(uow, ctx, row, with_items=True)

    # ------------------------------------------------------------------ reads
    async def get(self, ctx: AuthContext, invoice_id: uuid.UUID) -> InvoiceView:
        async with self._uow_factory() as uow:
            await InvoiceRepository(uow.session, ctx.org_id).get(
                invoice_id
            )  # 404 for foreign or missing
            return await self._view_of(uow, ctx, invoice_id)

    async def search(
        self,
        ctx: AuthContext,
        *,
        status: str | None,
        payment_status: str | None,
        overdue: bool | None,
        customer_id: uuid.UUID | None,
        q: str | None,
        issue_from: date | None,
        issue_to: date | None,
        sort: str,
        limit: int,
        offset: int,
    ) -> tuple[list[InvoiceView], int]:
        async with self._uow_factory() as uow:
            rows, total = await InvoiceRepository(uow.session, ctx.org_id).search(
                status=status,
                payment_status=payment_status,
                overdue=overdue,
                customer_id=customer_id,
                q=q.strip() if q else None,
                issue_from=issue_from,
                issue_to=issue_to,
                sort=sort,
                today=self._clock.today_ist(),
                limit=limit,
                offset=offset,
            )
            udyam = (await OrganizationRepository(uow.session, ctx.org_id).get()).udyam_category
            return [
                await self._view(uow, ctx, r, with_items=False, udyam=udyam) for r in rows
            ], total

    async def preview(self, ctx: AuthContext, data: InvoicePreviewRequest) -> InvoiceTotals:
        async with self._uow_factory() as uow:
            org = await OrganizationRepository(uow.session, ctx.org_id).get()
            pos = data.place_of_supply_state_code
            if data.customer_id is not None:
                customer = await CustomerRepository(uow.session, ctx.org_id).get(data.customer_id)
                pos = pos or customer.state_code
            if pos is None:
                raise ValidationFailed(
                    "place of supply or customer is required",
                    errors=[
                        FieldError("place_of_supply_state_code", "required without a customer")
                    ],
                )
            return _totals(org.state_code, validate_state_code(pos), _specs(data.items))

    # ------------------------------------------------------------------ drafts
    async def _create_draft_in(
        self,
        uow: UnitOfWork,
        ctx: AuthContext,
        *,
        customer_id: uuid.UUID,
        deal_id: uuid.UUID | None,
        terms: int | None,
        pos: str | None,
        specs: list[LineSpec],
        meta: RequestMeta,
    ) -> uuid.UUID:
        customer = await CustomerRepository(uow.session, ctx.org_id).get(customer_id)
        if not customer.is_active:
            raise InvalidState("This customer is inactive.")
        if deal_id is not None:
            deal = await DealRepository(uow.session, ctx.org_id).get(deal_id)
            if deal.customer_id != customer.id:
                raise ValidationFailed(
                    "the deal belongs to a different customer",
                    errors=[FieldError("deal_id", "mismatch")],
                )
        place = validate_state_code(pos or customer.state_code)
        org = await OrganizationRepository(uow.session, ctx.org_id).get()
        totals = _totals(org.state_code, place, specs)
        repo = InvoiceRepository(uow.session, ctx.org_id)
        invoice = repo.add(
            customer_id=customer.id,
            deal_id=deal_id,
            status="draft",
            payment_terms_days=customer.default_terms_days if terms is None else terms,
            place_of_supply_state_code=place,
            version=1,
        )
        _apply_totals(invoice, totals)
        await uow.session.flush()
        await repo.replace_items(invoice.id, _item_rows(specs, totals))
        await uow.session.refresh(invoice)
        record_audit(
            uow.session, org_id=ctx.org_id, action="invoice.create", entity_type="invoice",
            entity_id=invoice.id, actor=ctx, after=_audit_snapshot(invoice), meta=meta,
        )  # fmt: skip
        return invoice.id

    async def create_draft(
        self, ctx: AuthContext, data: InvoiceCreate, meta: RequestMeta
    ) -> InvoiceView:
        async with self._uow_factory() as uow:
            invoice_id = await self._create_draft_in(
                uow, ctx, customer_id=data.customer_id, deal_id=data.deal_id,
                terms=data.payment_terms_days, pos=data.place_of_supply_state_code,
                specs=_specs(data.items), meta=meta,
            )  # fmt: skip
            return await self._view_of(uow, ctx, invoice_id)

    async def create_draft_from_deal(
        self, ctx: AuthContext, deal_id: uuid.UUID, meta: RequestMeta
    ) -> InvoiceView:

        async with self._uow_factory() as uow:
            deal = await DealRepository(uow.session, ctx.org_id).get(deal_id)
            deal_rules.assert_can_invoice(deal.stage)
            if (
                await InvoiceRepository(uow.session, ctx.org_id).active_for_deal(deal.id)
                is not None
            ):
                raise Conflict("This deal already has an invoice.")
            spec = LineSpec(
                deal.title[:500], None, 1, deal.amount_estimate_paise, DEFAULT_DEAL_GST_RATE_BP
            )
            invoice_id = await self._create_draft_in(
                uow, ctx, customer_id=deal.customer_id, deal_id=deal.id, terms=None, pos=None,
                specs=[spec], meta=meta,
            )  # fmt: skip
            return await self._view_of(uow, ctx, invoice_id)

    async def replace_draft(
        self, ctx: AuthContext, invoice_id: uuid.UUID, data: InvoiceReplace, meta: RequestMeta
    ) -> InvoiceView:
        async with self._uow_factory() as uow:
            repo = InvoiceRepository(uow.session, ctx.org_id)
            invoice = await repo.get(invoice_id, for_update=True)
            invoice_rules.assert_editable(invoice.status)
            if data.version != invoice.version:
                raise Conflict("This invoice was changed by someone else. Reload and try again.")
            before = _audit_snapshot(invoice)
            place = validate_state_code(data.place_of_supply_state_code)
            org = await OrganizationRepository(uow.session, ctx.org_id).get()
            specs = _specs(data.items)
            totals = _totals(org.state_code, place, specs)
            invoice.payment_terms_days = data.payment_terms_days
            invoice.place_of_supply_state_code = place
            _apply_totals(invoice, totals)
            invoice.version += 1
            await uow.session.flush()
            await repo.replace_items(invoice.id, _item_rows(specs, totals))
            await uow.session.refresh(invoice)
            record_audit(
                uow.session, org_id=ctx.org_id, action="invoice.update", entity_type="invoice",
                entity_id=invoice.id, actor=ctx, before=before, after=_audit_snapshot(invoice), meta=meta,
            )  # fmt: skip
            return await self._view_of(uow, ctx, invoice.id)

    # ------------------------------------------------------------------ issue and void
    async def issue(
        self, ctx: AuthContext, invoice_id: uuid.UUID, meta: RequestMeta
    ) -> InvoiceView:
        """Idempotent by state: issuing an issued invoice returns it unchanged.

        The number comes from the per-(org, FY) counter inside this same transaction, as the last step before the
        row is written, so any failure rolls the counter back with everything else (no burned numbers, no gaps)."""
        async with self._uow_factory() as uow:
            repo = InvoiceRepository(uow.session, ctx.org_id)
            invoice = await repo.get(invoice_id, for_update=True)
            if invoice.status == "issued":
                return await self._view_of(uow, ctx, invoice.id)
            items = await repo.items(invoice.id)
            invoice_rules.assert_issuable(
                invoice.status, item_count=len(items), total_paise=invoice.total_paise
            )
            org = await OrganizationRepository(uow.session, ctx.org_id).get()
            specs = [
                LineSpec(i.description, i.hsn_sac, i.quantity, i.unit_price_paise, i.gst_rate_bp)
                for i in items
            ]
            totals = _totals(
                org.state_code, invoice.place_of_supply_state_code, specs
            )  # authoritative at issue time
            invoice_rules.assert_issuable(
                "draft", item_count=len(items), total_paise=totals.total_paise
            )
            before = _audit_snapshot(invoice)
            issue_date = self._clock.today_ist()
            due_date = invoice_rules.compute_due_date(issue_date, invoice.payment_terms_days)
            await repo.replace_items(invoice.id, _item_rows(specs, totals))
            _apply_totals(invoice, totals)
            fy = fy_label_for(issue_date)
            number = format_invoice_number(
                fy, await repo.next_number_value(fy)
            )  # last statement that can fail
            invoice.number = number
            invoice.status = "issued"
            invoice.issue_date = issue_date
            invoice.due_date = due_date
            invoice.supply_type = totals.supply_type
            invoice.version += 1
            await uow.session.flush()
            await uow.session.refresh(invoice)
            record_audit(
                uow.session, org_id=ctx.org_id, action="invoice.issue", entity_type="invoice",
                entity_id=invoice.id, actor=ctx, before=before, after=_audit_snapshot(invoice), meta=meta,
            )  # fmt: skip
            return await self._view_of(uow, ctx, invoice.id)

    async def void(
        self, ctx: AuthContext, invoice_id: uuid.UUID, reason: str, meta: RequestMeta
    ) -> InvoiceView:
        async with self._uow_factory() as uow:
            repo = InvoiceRepository(uow.session, ctx.org_id)
            invoice = await repo.get(invoice_id, for_update=True)
            if invoice.status == "void":  # idempotent by state
                return await self._view_of(uow, ctx, invoice.id)
            cleaned = invoice_rules.validate_void(
                invoice.status, paid_paise=invoice.amount_paid_paise, reason=reason
            )
            before = _audit_snapshot(invoice)
            invoice.status = "void"
            invoice.void_reason = cleaned
            invoice.pay_token_hash = None  # a void invoice must not stay payable
            invoice.pay_token_expires_at = None
            invoice.version += 1
            await uow.session.flush()
            await uow.session.refresh(invoice)
            record_audit(
                uow.session, org_id=ctx.org_id, action="invoice.void", entity_type="invoice",
                entity_id=invoice.id, actor=ctx, before=before, after={**_audit_snapshot(invoice), "reason": cleaned},
                meta=meta,
            )  # fmt: skip
            return await self._view_of(uow, ctx, invoice.id)

    # ------------------------------------------------------------------ pay link
    async def create_pay_link(
        self, ctx: AuthContext, invoice_id: uuid.UUID, meta: RequestMeta
    ) -> tuple[str, datetime]:
        """Returns the full URL once. Only the SHA-256 hash is stored; regenerating replaces it (the old link dies)."""
        async with self._uow_factory() as uow:
            invoice = await InvoiceRepository(uow.session, ctx.org_id).get(
                invoice_id, for_update=True
            )
            if invoice.status != "issued":
                raise InvalidState("a pay link can only be created for an issued invoice")
            if invoice_rules.amount_due(invoice.total_paise, invoice.amount_paid_paise) <= 0:
                raise InvalidState("this invoice has nothing left to pay")
            token, token_hash = generate_pay_token()
            expires = self._clock.now_utc().astimezone(UTC) + timedelta(days=PAY_TOKEN_DAYS)
            invoice.pay_token_hash = token_hash
            invoice.pay_token_expires_at = expires
            await uow.session.flush()
            record_audit(
                uow.session, org_id=ctx.org_id, action="invoice.pay_link", entity_type="invoice",
                entity_id=invoice.id, actor=ctx, after={"expires_at": expires.isoformat()}, meta=meta,
            )  # fmt: skip
            return f"{self._base_url}/pay/{token}", expires
