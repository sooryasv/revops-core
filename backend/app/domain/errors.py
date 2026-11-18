"""Domain exceptions. Pure Python; the API layer maps each `code` to an HTTP status (api/errors.py)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FieldError:
    field: str
    message: str


class DomainError(Exception):
    """Base class. `code` is the stable machine-readable identifier clients may switch on."""

    code = "domain_error"

    def __init__(self, message: str, *, errors: list[FieldError] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.errors: list[FieldError] = errors or []


class ValidationFailed(DomainError):
    code = "validation_error"


class Unauthenticated(DomainError):
    code = "unauthenticated"


class Forbidden(DomainError):
    code = "forbidden"


class NotFound(DomainError):
    """Also used for cross-tenant access: a miss and a foreign row must be indistinguishable (03 section 6.4)."""

    code = "not_found"


class Conflict(DomainError):
    code = "conflict"


class InvalidState(DomainError):
    code = "invalid_state"


class RateLimited(DomainError):
    code = "rate_limited"

    def __init__(self, message: str, *, retry_after: int = 60) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class WebhookSignatureInvalid(DomainError):
    code = "webhook_signature_invalid"


class PaymentProviderError(DomainError):
    code = "payment_provider_error"
