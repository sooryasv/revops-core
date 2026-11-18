"""Importing this package registers every ORM model on Base.metadata."""

from app.db.models.audit import AuditLog
from app.db.models.customer import Customer
from app.db.models.deal import Deal
from app.db.models.invoice import Invoice, InvoiceItem, InvoiceSequence
from app.db.models.organization import Organization
from app.db.models.payment import Payment, PaymentOrder
from app.db.models.user import User
from app.db.models.webhook import WebhookEvent

__all__ = [
    "AuditLog", "Customer", "Deal", "Invoice", "InvoiceItem", "InvoiceSequence",
    "Organization", "Payment", "PaymentOrder", "User", "WebhookEvent",
]  # fmt: skip
