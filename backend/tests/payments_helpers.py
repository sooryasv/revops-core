"""Shared arrangement for payment tests. Signatures are computed here with hashlib/hmac directly, independently of
app.payments.signatures, so a bug in the application's signing code cannot hide behind itself."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings
from tests.conftest import ClientFactory, World, login_as

_DEFAULTS = Settings.model_fields
WEBHOOK_SECRET: str = _DEFAULTS["fake_webhook_secret"].default
KEY_SECRET: str = _DEFAULTS["fake_key_secret"].default

I = "/api/v1/invoices"  # noqa: E741
WEBHOOK = "/api/v1/webhooks/razorpay"


def sign(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def checkout_sig(order_id: str, payment_id: str, secret: str = KEY_SECRET) -> str:
    return hmac.new(
        secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256
    ).hexdigest()


def make_event(
    kind: str, order_id: str, payment_id: str, amount: int, currency: str = "INR", **extra: Any
) -> dict[str, Any]:
    """Payload shaped like Razorpay's documented events (V4, V5)."""
    payment = {
        "id": payment_id,
        "order_id": order_id,
        "amount": amount,
        "currency": currency,
        "status": "captured",
        "method": "upi",
        **extra,
    }
    body: dict[str, Any] = {
        "entity": "event", "account_id": "acc_test", "event": kind, "contains": ["payment"],
        "payload": {"payment": {"entity": payment}}, "created_at": 1_790_000_000,
    }  # fmt: skip
    if kind == "order.paid":
        body["contains"] = ["payment", "order"]
        body["payload"]["order"] = {
            "entity": {"id": order_id, "amount": amount, "amount_paid": amount, "status": "paid"}
        }
    return body


async def send(
    client: httpx.AsyncClient,
    payload: dict[str, Any] | None = None,
    *,
    event_id: str | None = None,
    secret: str = WEBHOOK_SECRET,
    raw: bytes | None = None,
    signature: str | None = "auto",
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    body = raw if raw is not None else json.dumps(payload).encode()
    hdrs = {
        "content-type": "application/json",
        "x-razorpay-event-id": event_id or f"evt_{uuid.uuid4().hex}",
        **(headers or {}),
    }
    if event_id == "":
        hdrs.pop("x-razorpay-event-id")
    if signature == "auto":
        hdrs["x-razorpay-signature"] = sign(body, secret)
    elif signature is not None:
        hdrs["x-razorpay-signature"] = signature
    return await client.post(WEBHOOK, content=body, headers=hdrs)


@dataclass
class Setup:
    fin: httpx.AsyncClient
    anon: httpx.AsyncClient
    invoice: dict[str, Any]
    token: str
    order_id: str
    total: int

    @property
    def invoice_id(self) -> str:
        return str(self.invoice["id"])

    async def reload(self) -> dict[str, Any]:
        return dict((await self.fin.get(f"{I}/{self.invoice_id}")).json())

    def event(
        self, kind: str, payment_id: str, amount: int | None = None, **kw: Any
    ) -> dict[str, Any]:
        return make_event(
            kind, self.order_id, payment_id, self.total if amount is None else amount, **kw
        )


def anon_client(make_client: ClientFactory) -> httpx.AsyncClient:
    return make_client(
        headers={"x-forwarded-for": f"198.51.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250}"}
    )


async def prepare(
    world: World,
    make_client: ClientFactory,
    *,
    price: int = 100_000,
    rate: int = 1800,
    with_order: bool = True,
) -> Setup:
    fin = await login_as(
        make_client,
        world.a["finance"],
        forwarded_for=f"10.9.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250}",
    )
    cust = (
        await fin.post(
            "/api/v1/customers", json={"name": f"Payer {uuid.uuid4().hex[:6]}", "state_code": "33"}
        )
    ).json()["id"]
    item = {"description": "Service", "quantity": 1, "unit_price_paise": price, "gst_rate_bp": rate}
    draft = (await fin.post(I, json={"customer_id": cust, "items": [item]})).json()
    invoice = (await fin.post(f"{I}/{draft['id']}/issue")).json()
    link = (await fin.post(f"{I}/{invoice['id']}/pay-link")).json()["url"]
    token = link.rsplit("/pay/", 1)[1]
    anon = anon_client(make_client)
    order_id = ""
    if with_order:
        r = await anon.post(f"/api/v1/public/pay/{token}/order")
        assert r.status_code == 200, r.text
        order_id = r.json()["order_id"]
    return Setup(fin, anon, invoice, token, order_id, invoice["total_paise"])
