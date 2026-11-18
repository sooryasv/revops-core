"""RazorpayProvider over httpx (03 section 8.1). WROTE-NOT-RUN against the real API: no test-mode keys in the build
sandbox. Request shape for create-order was checked against Razorpay's integration guide on 2026-11-01; the
fetch-order-payments response shape is UNVERIFIED (see .build/UNVERIFIED.md U6)."""

from __future__ import annotations

import httpx

from app.core.logging import get_logger
from app.domain.errors import PaymentProviderError
from app.payments.provider import ProviderOrder, ProviderPayment
from app.payments.signatures import verify_checkout, verify_webhook

_log = get_logger("razorpay")
BASE_URL = "https://api.razorpay.com"


class RazorpayProvider:
    def __init__(
        self,
        *,
        key_id: str,
        key_secret: str,
        webhook_secret: str,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._key_id = key_id
        self._key_secret = key_secret
        self._webhook_secret = webhook_secret
        self._transport = transport
        self._timeout = timeout_seconds

    @property
    def key_id(self) -> str:
        return self._key_id

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=BASE_URL,
            auth=(self._key_id, self._key_secret),
            timeout=self._timeout,
            transport=self._transport,
        )

    async def create_order(
        self, amount_paise: int, receipt: str, notes: dict[str, str]
    ) -> ProviderOrder:
        body = {"amount": amount_paise, "currency": "INR", "receipt": receipt, "notes": notes}
        try:
            async with self._client() as client:
                response = await client.post("/v1/orders", json=body)
            response.raise_for_status()
            data = response.json()
            return ProviderOrder(
                id=str(data["id"]),
                amount_paise=int(data["amount"]),
                currency=str(data["currency"]),
                receipt=str(data.get("receipt", receipt)),
            )
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
            # Log the exception type only: HTTP errors can carry the request, which carries credentials.
            _log.warning("create_order_failed", exc_type=type(exc).__name__)
            raise PaymentProviderError("The payment provider could not create the order.") from exc

    async def fetch_order_payments(self, order_id: str) -> list[ProviderPayment]:
        try:
            async with self._client() as client:
                response = await client.get(f"/v1/orders/{order_id}/payments")
            response.raise_for_status()
            items = response.json()["items"]
            return [
                ProviderPayment(
                    id=str(p["id"]),
                    order_id=str(p.get("order_id", order_id)),
                    amount_paise=int(p["amount"]),
                    currency=str(p.get("currency", "INR")),
                    status=str(p["status"]),
                )
                for p in items
            ]
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
            _log.warning("fetch_order_payments_failed", exc_type=type(exc).__name__)
            raise PaymentProviderError("The payment provider could not list payments.") from exc

    def verify_checkout_signature(self, order_id: str, payment_id: str, signature: str) -> bool:
        return verify_checkout(self._key_secret, order_id, payment_id, signature)

    def verify_webhook_signature(self, raw_body: bytes, signature: str) -> bool:
        return verify_webhook(self._webhook_secret, raw_body, signature)
