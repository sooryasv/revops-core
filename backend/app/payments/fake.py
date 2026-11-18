"""FakeProvider: tests, E2E and local runs without Razorpay keys. Same signature scheme as the real provider."""

from __future__ import annotations

import uuid

from app.payments.provider import ProviderOrder, ProviderPayment
from app.payments.signatures import verify_checkout, verify_webhook


class FakeProvider:
    def __init__(
        self, *, key_secret: str, webhook_secret: str, key_id: str = "rzp_test_fake"
    ) -> None:
        self._key_id = key_id
        self._key_secret = key_secret
        self._webhook_secret = webhook_secret
        self.created_orders: list[ProviderOrder] = []
        self.fail_next_create = False
        self.payments_by_order: dict[str, list[ProviderPayment]] = {}

    @property
    def key_id(self) -> str:
        return self._key_id

    async def create_order(
        self, amount_paise: int, receipt: str, notes: dict[str, str]
    ) -> ProviderOrder:
        from app.domain.errors import PaymentProviderError

        if self.fail_next_create:
            self.fail_next_create = False
            raise PaymentProviderError("The payment provider is unavailable.")
        order = ProviderOrder(f"order_fake_{uuid.uuid4().hex[:14]}", amount_paise, "INR", receipt)
        self.created_orders.append(order)
        return order

    async def fetch_order_payments(self, order_id: str) -> list[ProviderPayment]:
        return list(self.payments_by_order.get(order_id, []))

    def verify_checkout_signature(self, order_id: str, payment_id: str, signature: str) -> bool:
        return verify_checkout(self._key_secret, order_id, payment_id, signature)

    def verify_webhook_signature(self, raw_body: bytes, signature: str) -> bool:
        return verify_webhook(self._webhook_secret, raw_body, signature)
