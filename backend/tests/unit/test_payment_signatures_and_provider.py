from __future__ import annotations

import base64
import json
import logging

import httpx
import pytest

from app.domain.errors import PaymentProviderError
from app.payments.events import parse_event
from app.payments.razorpay import RazorpayProvider
from app.payments.signatures import (
    checkout_signature,
    constant_time_equal,
    hmac_sha256_hex,
    verify_checkout,
    verify_webhook,
    webhook_signature,
)
from tests.payments_helpers import checkout_sig, make_event, sign


def test_hmac_matches_the_rfc_4231_test_vector() -> None:
    """RFC 4231, test case 2: published expected output, independent of any code in this repository."""
    got = hmac_sha256_hex(b"Jefe", b"what do ya want for nothing?")
    assert got == "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843"


def test_webhook_signature_matches_an_independent_computation() -> None:
    body = b'{"event":"payment.captured","payload":{}}'
    assert webhook_signature("whsec_abc", body) == sign(body, "whsec_abc")
    assert verify_webhook("whsec_abc", body, sign(body, "whsec_abc"))


def test_checkout_signature_uses_order_pipe_payment() -> None:
    assert checkout_signature("key_secret", "order_ABC", "pay_XYZ") == checkout_sig(
        "order_ABC", "pay_XYZ", "key_secret"
    )
    assert checkout_signature("key_secret", "order_ABC", "pay_XYZ") != checkout_signature(
        "key_secret", "pay_XYZ", "order_ABC"
    )
    assert verify_checkout(
        "key_secret", "order_ABC", "pay_XYZ", checkout_sig("order_ABC", "pay_XYZ", "key_secret")
    )


def test_verification_rejects_everything_else() -> None:
    body = b'{"a":1}'
    good = sign(body, "s")
    assert not verify_webhook("s", body + b" ", good)  # body changed by one byte
    assert not verify_webhook("other", body, good)  # wrong secret
    assert not verify_webhook("s", body, good[:-1] + ("0" if good[-1] != "0" else "1"))
    assert not verify_webhook("s", body, "")
    assert not verify_checkout(
        "s", "order_1", "pay_1", checkout_sig("order_2", "pay_1", "s")
    )  # server-known order id wins


def test_signature_comparison_tolerates_whitespace_and_non_ascii_without_raising() -> None:
    body = b"{}"
    assert verify_webhook("s", body, f"  {sign(body, 's')}\n")
    assert not verify_webhook("s", body, "Ünïcode-signature")
    assert not constant_time_equal("a", "ä")


# ----------------------------------------------------------------------------- Razorpay REST client (no network)


def _provider(handler: httpx.MockTransport) -> RazorpayProvider:
    return RazorpayProvider(
        key_id="rzp_test_id", key_secret="secret-value", webhook_secret="whsec", transport=handler
    )


async def test_create_order_sends_the_documented_request_and_parses_the_response() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["method"] = request.method
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "order_123",
                "amount": 118000,
                "currency": "INR",
                "receipt": "r-1",
                "status": "created",
            },
        )

    order = await _provider(httpx.MockTransport(handler)).create_order(
        118_000, "r-1", {"invoice_id": "i"}
    )
    assert (order.id, order.amount_paise, order.currency, order.receipt) == (
        "order_123",
        118_000,
        "INR",
        "r-1",
    )
    assert seen["url"] == "https://api.razorpay.com/v1/orders"
    assert seen["method"] == "POST"
    assert seen["auth"] == "Basic " + base64.b64encode(b"rzp_test_id:secret-value").decode()
    assert seen["body"] == {
        "amount": 118000,
        "currency": "INR",
        "receipt": "r-1",
        "notes": {"invoice_id": "i"},
    }


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, json={}),
        httpx.Response(400, json={"error": {}}),
        httpx.Response(200, json={"nope": 1}),
        httpx.Response(200, text="not json"),
    ],
)
async def test_create_order_failures_become_provider_errors_without_leaking_credentials(
    response: httpx.Response, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    with pytest.raises(PaymentProviderError) as caught:
        await _provider(httpx.MockTransport(lambda r: response)).create_order(100, "r", {})
    assert "secret-value" not in str(caught.value)
    assert "secret-value" not in caplog.text


async def test_network_error_becomes_a_provider_error() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    with pytest.raises(PaymentProviderError):
        await _provider(httpx.MockTransport(boom)).create_order(100, "r", {})


async def test_fetch_order_payments_parses_items() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/orders/order_1/payments"
        return httpx.Response(
            200,
            json={
                "entity": "collection",
                "count": 1,
                "items": [
                    {
                        "id": "pay_1",
                        "order_id": "order_1",
                        "amount": 500,
                        "currency": "INR",
                        "status": "captured",
                    }
                ],
            },
        )

    (payment,) = await _provider(httpx.MockTransport(handler)).fetch_order_payments("order_1")
    assert (payment.id, payment.amount_paise, payment.status) == ("pay_1", 500, "captured")


def test_provider_verifies_with_its_own_secrets() -> None:
    provider = _provider(httpx.MockTransport(lambda r: httpx.Response(200)))
    body = b"{}"
    assert provider.verify_webhook_signature(body, sign(body, "whsec"))
    assert provider.verify_checkout_signature(
        "order_1", "pay_1", checkout_sig("order_1", "pay_1", "secret-value")
    )
    assert not provider.verify_checkout_signature(
        "order_1", "pay_1", checkout_sig("order_1", "pay_1", "whsec")
    )
    assert provider.key_id == "rzp_test_id"


# ----------------------------------------------------------------------------- event parsing


def test_payment_events_are_parsed_by_documented_field_paths() -> None:
    for kind, status in (
        ("payment.authorized", "authorized"),
        ("payment.captured", "captured"),
        ("payment.failed", "failed"),
    ):
        e = parse_event(make_event(kind, "order_1", "pay_1", 5000, error_code="BAD_REQUEST_ERROR"))
        assert (
            e.handled,
            e.complete,
            e.payment_status,
            e.order_id,
            e.payment_id,
            e.amount_paise,
        ) == (True, True, status, "order_1", "pay_1", 5000)
    assert (
        parse_event(make_event("order.paid", "order_9", "pay_9", 100)).payment_status == "captured"
    )


def test_event_status_comes_from_the_event_type_not_the_payload_status() -> None:
    """A delayed `payment.authorized` delivery carries a payload status that may already say captured."""
    payload = make_event("payment.authorized", "order_1", "pay_1", 100)
    payload["payload"]["payment"]["entity"]["status"] = "captured"
    assert parse_event(payload).payment_status == "authorized"


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        "x",
        {},
        {"event": 5},
        {"event": "refund.created"},
        {"event": "payment.captured"},
        {
            "event": "payment.captured",
            "payload": {"payment": {"entity": {"id": "p", "order_id": "o", "amount": "100"}}},
        },
        {
            "event": "payment.captured",
            "payload": {"payment": {"entity": {"id": "p", "order_id": "o", "amount": True}}},
        },
    ],
)
def test_malformed_or_unhandled_events_never_raise_and_are_not_complete(payload: object) -> None:
    event = parse_event(payload)
    assert not (event.handled and event.complete)
