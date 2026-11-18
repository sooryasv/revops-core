"""The payment-provider boundary (03 section 8.1). Services depend on this protocol, never on Razorpay directly."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

# The value stored in `provider` columns. The fake provider also stores 'razorpay': the CHECK constraint allows only
# that value, and the fake imitates Razorpay's wire format (SPEC note in .build/PROGRESS.md, M2).
PROVIDER_NAME = "razorpay"


@dataclass(frozen=True, slots=True)
class ProviderOrder:
    id: str
    amount_paise: int
    currency: str
    receipt: str


@dataclass(frozen=True, slots=True)
class ProviderPayment:
    id: str
    order_id: str
    amount_paise: int
    currency: str
    status: str


class PaymentProvider(Protocol):
    @property
    def key_id(self) -> str:
        """Public key the browser needs to open Checkout. Never the secret."""
        ...

    async def create_order(
        self, amount_paise: int, receipt: str, notes: dict[str, str]
    ) -> ProviderOrder: ...

    async def fetch_order_payments(self, order_id: str) -> list[ProviderPayment]: ...

    def verify_checkout_signature(self, order_id: str, payment_id: str, signature: str) -> bool: ...

    def verify_webhook_signature(self, raw_body: bytes, signature: str) -> bool: ...
