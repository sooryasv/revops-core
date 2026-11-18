"""HMAC-SHA256 signing and timing-safe comparison, written out by hand so every step can be explained (03 section 8.1).

Checkout signature (verified against Razorpay's integration guide on 2026-11-01): HMAC-SHA256, hex digest, key = API
key secret, message = `order_id + "|" + razorpay_payment_id`, using the order id STORED ON OUR SERVER.
Webhook signature: HMAC-SHA256 hex of the RAW request body with the webhook secret (header X-Razorpay-Signature).
"""

from __future__ import annotations

import hashlib
import hmac


def hmac_sha256_hex(key: bytes, message: bytes) -> str:
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def constant_time_equal(left: str, right: str) -> bool:
    """Compare without leaking where the first difference is. Bytes, so non-ASCII input cannot raise."""
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def webhook_signature(secret: str, raw_body: bytes) -> str:
    return hmac_sha256_hex(secret.encode("utf-8"), raw_body)


def checkout_signature(secret: str, order_id: str, payment_id: str) -> str:
    return hmac_sha256_hex(secret.encode("utf-8"), f"{order_id}|{payment_id}".encode())


def verify_webhook(secret: str, raw_body: bytes, signature: str) -> bool:
    return constant_time_equal(webhook_signature(secret, raw_body), signature.strip())


def verify_checkout(secret: str, order_id: str, payment_id: str, signature: str) -> bool:
    return constant_time_equal(checkout_signature(secret, order_id, payment_id), signature.strip())
