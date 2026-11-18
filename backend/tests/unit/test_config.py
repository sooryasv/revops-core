import pytest
from pydantic import ValidationError

from app.config import Settings


def make(**overrides: object) -> Settings:
    base: dict[str, object] = {"database_url": "postgresql+asyncpg://u:p@localhost/db"}
    return Settings(_env_file=None, **{**base, **overrides})  # type: ignore[arg-type]


def test_defaults_are_safe_for_dev() -> None:
    s = make()
    assert s.env == "dev"
    assert s.cookie_secure is False
    assert s.payment_provider == "fake"
    assert s.is_production is False


def test_allowed_origins_accepts_comma_separated_string() -> None:
    s = make(allowed_origins="https://a.example, https://b.example ,")
    assert s.allowed_origins == ["https://a.example", "https://b.example"]


def test_production_requires_jwt_key() -> None:
    with pytest.raises(ValidationError, match="JWT_PRIVATE_KEY_PEM"):
        make(env="production", allowed_origins="https://app.example")


def test_production_rejects_http_origins() -> None:
    with pytest.raises(ValidationError, match="https"):
        make(env="production", jwt_private_key_pem="x", allowed_origins="http://app.example")


def test_production_razorpay_requires_all_secrets() -> None:
    with pytest.raises(ValidationError, match="RAZORPAY"):
        make(
            env="production",
            jwt_private_key_pem="x",
            allowed_origins="https://app.example",
            payment_provider="razorpay",
        )


def test_production_valid_configuration_sets_secure_cookies() -> None:
    s = make(
        env="production",
        jwt_private_key_pem="x",
        allowed_origins="https://app.example",
        payment_provider="razorpay",
        razorpay_key_id="rzp_live_x",
        razorpay_key_secret="s",
        razorpay_webhook_secret="w",
    )
    assert s.cookie_secure is True


def test_production_rejects_the_fake_payment_provider() -> None:
    with pytest.raises(ValidationError, match="PAYMENT_PROVIDER=fake"):
        make(env="production", jwt_private_key_pem="x", allowed_origins="https://app.example")


def test_secrets_do_not_appear_in_repr() -> None:
    s = make(jwt_private_key_pem="super-secret-pem")
    assert "super-secret-pem" not in repr(s)
