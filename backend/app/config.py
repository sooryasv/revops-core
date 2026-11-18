"""Application settings. Environment variables only; nothing is read from code defaults that look like secrets."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    env: Literal["dev", "test", "production"] = "dev"
    log_level: str = "INFO"

    # Runtime connection (role revops_app) and migration connection (role revops_owner). See 07 section 1.3.
    database_url: str
    migration_database_url: str | None = None
    db_statement_timeout_ms: int = Field(default=15_000, ge=100, le=120_000)

    # Browser origins allowed to make unsafe requests (03 section 6.2). Comma separated in the environment.
    allowed_origins: Annotated[list[str], NoDecode] = [
        "http://localhost:8000",
        "http://localhost:5173",
    ]

    # --- authentication (02 section 4)
    jwt_issuer: str = "revops-core"
    jwt_private_key_pem: SecretStr | None = None
    jwt_key_id: str = "dev-1"
    jwt_previous_public_key_pem: str | None = None
    jwt_previous_key_id: str | None = None
    cookie_name: str = "revops_session"

    # --- payments
    payment_provider: Literal["razorpay", "fake"] = "fake"
    razorpay_key_id: str | None = None
    razorpay_key_secret: SecretStr | None = None
    razorpay_webhook_secret: SecretStr | None = None
    public_base_url: str = "http://localhost:8000"
    # Only used when PAYMENT_PROVIDER=fake (dev, tests, E2E). Production refuses the fake provider.
    fake_key_secret: str = "fake-key-secret-dev-only"  # noqa: S105
    fake_webhook_secret: str = "fake-webhook-secret-dev-only"  # noqa: S105

    # --- operations
    run_migrations: bool = False
    # Built SPA (frontend/dist copied here by the Dockerfile). Absent in API-only runs; then no SPA routes are mounted.
    static_dir: str = "app/static"

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def cookie_secure(self) -> bool:
        # Browsers refuse Secure cookies over plain http, so dev and test turn it off. Production never does.
        return self.env == "production"

    @model_validator(mode="before")
    @classmethod
    def _split_origins(cls, data: object) -> object:
        if isinstance(data, dict):
            raw = data.get("allowed_origins", data.get("ALLOWED_ORIGINS"))
            if isinstance(raw, str):
                items = [part.strip() for part in raw.split(",") if part.strip()]
                data = {**data, "allowed_origins": items}
                data.pop("ALLOWED_ORIGINS", None)
        return data

    @model_validator(mode="after")
    def _production_guards(self) -> Self:
        if not self.is_production:
            return self
        problems: list[str] = []
        if self.jwt_private_key_pem is None:
            problems.append(
                "JWT_PRIVATE_KEY_PEM is required in production (keys are never generated silently)"
            )
        if self.payment_provider == "fake":
            problems.append("PAYMENT_PROVIDER=fake is not allowed in production")
        if self.payment_provider == "razorpay" and not (
            self.razorpay_key_id and self.razorpay_key_secret and self.razorpay_webhook_secret
        ):
            problems.append(
                "RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET and RAZORPAY_WEBHOOK_SECRET are required"
            )
        if any(origin.startswith("http://") for origin in self.allowed_origins):
            problems.append("ALLOWED_ORIGINS must be https in production")
        if problems:
            raise ValueError("; ".join(problems))
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
