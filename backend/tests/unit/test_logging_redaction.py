from app.core.logging import REDACTED, redact_processor


def run(event: dict[str, object]) -> dict[str, object]:
    return dict(redact_processor(None, "info", dict(event)))


def test_sensitive_keys_are_redacted() -> None:
    out = run(
        {
            "event": "login",
            "password": "hunter2",
            "Authorization": "Bearer abc",
            "set-cookie": "revops_session=abc",
            "razorpay_signature": "deadbeef",
            "webhook_secret": "s",
            "access_token": "t",
        }
    )
    assert out["event"] == "login"
    for key in (
        "password",
        "Authorization",
        "set-cookie",
        "razorpay_signature",
        "webhook_secret",
        "access_token",
    ):
        assert out[key] == REDACTED


def test_nested_structures_are_redacted() -> None:
    out = run(
        {
            "event": "x",
            "headers": {"cookie": "a", "accept": "json"},
            "items": [{"password": "p", "ok": 1}],
        }
    )
    assert out["headers"] == {"cookie": REDACTED, "accept": "json"}
    assert out["items"] == [{"password": REDACTED, "ok": 1}]


def test_event_text_is_never_touched() -> None:
    assert run({"event": "secret rotation done"})["event"] == "secret rotation done"


def test_harmless_keys_pass_through() -> None:
    assert run({"event": "e", "route": "/x", "status": 200})["route"] == "/x"
