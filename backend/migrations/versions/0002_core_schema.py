"""core schema: the 11 tables of 03 section 5 (frozen DDL; later model edits need a NEW revision)

Revision ID: 0002
Revises: 0001

The DDL below was generated once from the SQLAlchemy models and then reviewed by hand. It is kept as literal SQL so
this revision never changes when a model does.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATEMENTS = [
    """CREATE TABLE organizations (
	id UUID NOT NULL,
	name TEXT NOT NULL,
	legal_name TEXT NOT NULL,
	gstin TEXT,
	state_code TEXT NOT NULL,
	udyam_category TEXT DEFAULT 'none' NOT NULL,
	address TEXT DEFAULT '' NOT NULL,
	default_terms_days INTEGER DEFAULT '30' NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_organizations PRIMARY KEY (id),
	CONSTRAINT ck_organizations_state_code_len CHECK (char_length(state_code) = 2),
	CONSTRAINT ck_organizations_udyam_category CHECK (udyam_category IN ('micro','small','medium','none')),
	CONSTRAINT ck_organizations_default_terms CHECK (default_terms_days BETWEEN 0 AND 365)
)""",
    """CREATE TABLE customers (
	name TEXT NOT NULL,
	gstin TEXT,
	state_code TEXT NOT NULL,
	email TEXT,
	phone TEXT,
	billing_address TEXT DEFAULT '' NOT NULL,
	has_written_agreement BOOLEAN DEFAULT 'false' NOT NULL,
	default_terms_days INTEGER DEFAULT '30' NOT NULL,
	notes TEXT,
	is_active BOOLEAN DEFAULT 'true' NOT NULL,
	id UUID NOT NULL,
	org_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_customers PRIMARY KEY (id),
	CONSTRAINT uq_customers_org_id_id UNIQUE (org_id, id),
	CONSTRAINT ck_customers_state_code_len CHECK (char_length(state_code) = 2),
	CONSTRAINT ck_customers_default_terms CHECK (default_terms_days BETWEEN 0 AND 365),
	CONSTRAINT fk_customers_org_id_organizations FOREIGN KEY(org_id) REFERENCES organizations (id)
)""",
    """CREATE INDEX ix_customers_org_name ON customers (org_id, name)""",
    """CREATE UNIQUE INDEX uq_customers_org_gstin ON customers (org_id, gstin) WHERE gstin IS NOT NULL""",
    """CREATE TABLE invoice_sequences (
	org_id UUID NOT NULL,
	fy_label TEXT NOT NULL,
	last_value INTEGER DEFAULT '0' NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_invoice_sequences PRIMARY KEY (org_id, fy_label),
	CONSTRAINT ck_invoice_sequences_last_value_range CHECK (last_value BETWEEN 0 AND 999999),
	CONSTRAINT fk_invoice_sequences_org_id_organizations FOREIGN KEY(org_id) REFERENCES organizations (id)
)""",
    """CREATE TABLE users (
	email TEXT NOT NULL,
	full_name TEXT NOT NULL,
	password_hash TEXT NOT NULL,
	role TEXT NOT NULL,
	is_active BOOLEAN DEFAULT 'true' NOT NULL,
	token_version INTEGER DEFAULT '0' NOT NULL,
	last_login_at TIMESTAMP WITH TIME ZONE,
	id UUID NOT NULL,
	org_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_users PRIMARY KEY (id),
	CONSTRAINT uq_users_org_id_id UNIQUE (org_id, id),
	CONSTRAINT ck_users_role CHECK (role IN ('admin','finance','sales')),
	CONSTRAINT fk_users_org_id_organizations FOREIGN KEY(org_id) REFERENCES organizations (id)
)""",
    """CREATE UNIQUE INDEX uq_users_email_lower ON users (lower(email))""",
    """CREATE TABLE webhook_events (
	id UUID NOT NULL,
	org_id UUID,
	provider TEXT NOT NULL,
	provider_event_id TEXT NOT NULL,
	event_type TEXT NOT NULL,
	received_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	payload JSONB NOT NULL,
	raw_body TEXT NOT NULL,
	processing_status TEXT DEFAULT 'received' NOT NULL,
	attempts INTEGER DEFAULT '1' NOT NULL,
	processed_at TIMESTAMP WITH TIME ZONE,
	last_error TEXT,
	CONSTRAINT pk_webhook_events PRIMARY KEY (id),
	CONSTRAINT uq_webhook_events_provider_provider_event_id UNIQUE (provider, provider_event_id),
	CONSTRAINT ck_webhook_events_provider CHECK (provider IN ('razorpay')),
	CONSTRAINT ck_webhook_events_status CHECK (processing_status IN ('received','processed','ignored','quarantined','failed')),
	CONSTRAINT fk_webhook_events_org_id_organizations FOREIGN KEY(org_id) REFERENCES organizations (id)
)""",
    """CREATE INDEX ix_webhook_events_org_status_received ON webhook_events (org_id, processing_status, received_at)""",
    """CREATE TABLE audit_logs (
	id UUID NOT NULL,
	org_id UUID NOT NULL,
	actor_type TEXT NOT NULL,
	actor_user_id UUID,
	action TEXT NOT NULL,
	entity_type TEXT NOT NULL,
	entity_id UUID,
	before JSONB,
	after JSONB,
	request_id TEXT,
	ip TEXT,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_audit_logs PRIMARY KEY (id),
	CONSTRAINT fk_audit_logs_org_id_actor_user_id_users FOREIGN KEY(org_id, actor_user_id) REFERENCES users (org_id, id),
	CONSTRAINT ck_audit_logs_actor_type CHECK (actor_type IN ('user','system','webhook','public')),
	CONSTRAINT fk_audit_logs_org_id_organizations FOREIGN KEY(org_id) REFERENCES organizations (id)
)""",
    """CREATE INDEX ix_audit_logs_org_created ON audit_logs (org_id, created_at DESC)""",
    """CREATE TABLE deals (
	customer_id UUID NOT NULL,
	title TEXT NOT NULL,
	stage TEXT DEFAULT 'lead' NOT NULL,
	amount_estimate_paise BIGINT DEFAULT '0' NOT NULL,
	owner_user_id UUID NOT NULL,
	expected_close_date DATE,
	lost_reason TEXT,
	stage_changed_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	closed_at TIMESTAMP WITH TIME ZONE,
	id UUID NOT NULL,
	org_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_deals PRIMARY KEY (id),
	CONSTRAINT uq_deals_org_id_id UNIQUE (org_id, id),
	CONSTRAINT fk_deals_org_id_customer_id_customers FOREIGN KEY(org_id, customer_id) REFERENCES customers (org_id, id),
	CONSTRAINT fk_deals_org_id_owner_user_id_users FOREIGN KEY(org_id, owner_user_id) REFERENCES users (org_id, id),
	CONSTRAINT ck_deals_stage CHECK (stage IN ('lead','qualified','proposal','won','lost')),
	CONSTRAINT ck_deals_amount_non_negative CHECK (amount_estimate_paise >= 0),
	CONSTRAINT ck_deals_lost_needs_reason CHECK (stage <> 'lost' OR lost_reason IS NOT NULL),
	CONSTRAINT ck_deals_closed_matches_stage CHECK ((stage IN ('won','lost')) = (closed_at IS NOT NULL)),
	CONSTRAINT fk_deals_org_id_organizations FOREIGN KEY(org_id) REFERENCES organizations (id)
)""",
    """CREATE INDEX ix_deals_org_stage ON deals (org_id, stage)""",
    """CREATE TABLE invoices (
	customer_id UUID NOT NULL,
	deal_id UUID,
	number TEXT,
	status TEXT DEFAULT 'draft' NOT NULL,
	issue_date DATE,
	due_date DATE,
	payment_terms_days INTEGER NOT NULL,
	place_of_supply_state_code TEXT NOT NULL,
	supply_type TEXT,
	subtotal_paise BIGINT DEFAULT '0' NOT NULL,
	cgst_paise BIGINT DEFAULT '0' NOT NULL,
	sgst_paise BIGINT DEFAULT '0' NOT NULL,
	igst_paise BIGINT DEFAULT '0' NOT NULL,
	total_paise BIGINT DEFAULT '0' NOT NULL,
	amount_paid_paise BIGINT DEFAULT '0' NOT NULL,
	void_reason TEXT,
	pay_token_hash TEXT,
	pay_token_expires_at TIMESTAMP WITH TIME ZONE,
	version INTEGER DEFAULT '1' NOT NULL,
	currency TEXT DEFAULT 'INR' NOT NULL,
	id UUID NOT NULL,
	org_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_invoices PRIMARY KEY (id),
	CONSTRAINT uq_invoices_org_id_id UNIQUE (org_id, id),
	CONSTRAINT uq_invoices_org_id_number UNIQUE (org_id, number),
	CONSTRAINT uq_invoices_pay_token_hash UNIQUE (pay_token_hash),
	CONSTRAINT fk_invoices_org_id_customer_id_customers FOREIGN KEY(org_id, customer_id) REFERENCES customers (org_id, id),
	CONSTRAINT fk_invoices_org_id_deal_id_deals FOREIGN KEY(org_id, deal_id) REFERENCES deals (org_id, id),
	CONSTRAINT ck_invoices_status CHECK (status IN ('draft','issued','void')),
	CONSTRAINT ck_invoices_supply_type CHECK (supply_type IS NULL OR supply_type IN ('intra','inter')),
	CONSTRAINT ck_invoices_currency_inr CHECK (currency = 'INR'),
	CONSTRAINT ck_invoices_total_matches CHECK (total_paise = subtotal_paise + cgst_paise + sgst_paise + igst_paise),
	CONSTRAINT ck_invoices_igst_exclusive CHECK (igst_paise = 0 OR (cgst_paise = 0 AND sgst_paise = 0)),
	CONSTRAINT ck_invoices_non_negative CHECK (subtotal_paise >= 0 AND cgst_paise >= 0 AND sgst_paise >= 0 AND igst_paise >= 0 AND amount_paid_paise >= 0),
	CONSTRAINT ck_invoices_number_when_issued CHECK (status = 'draft' OR number IS NOT NULL),
	CONSTRAINT ck_invoices_number_len CHECK (number IS NULL OR char_length(number) <= 16),
	CONSTRAINT ck_invoices_issued_fields CHECK (status = 'draft' OR (issue_date IS NOT NULL AND due_date IS NOT NULL AND supply_type IS NOT NULL)),
	CONSTRAINT ck_invoices_void_needs_reason CHECK (status <> 'void' OR void_reason IS NOT NULL),
	CONSTRAINT ck_invoices_terms_range CHECK (payment_terms_days BETWEEN 0 AND 365),
	CONSTRAINT ck_invoices_pos_len CHECK (char_length(place_of_supply_state_code) = 2),
	CONSTRAINT fk_invoices_org_id_organizations FOREIGN KEY(org_id) REFERENCES organizations (id)
)""",
    """CREATE INDEX ix_invoices_org_created ON invoices (org_id, created_at DESC, id DESC)""",
    """CREATE INDEX ix_invoices_org_customer ON invoices (org_id, customer_id)""",
    """CREATE INDEX ix_invoices_org_status_due ON invoices (org_id, status, due_date)""",
    """CREATE TABLE invoice_items (
	id UUID NOT NULL,
	org_id UUID NOT NULL,
	invoice_id UUID NOT NULL,
	line_no INTEGER NOT NULL,
	description TEXT NOT NULL,
	hsn_sac TEXT,
	quantity INTEGER NOT NULL,
	unit_price_paise BIGINT NOT NULL,
	gst_rate_bp INTEGER NOT NULL,
	taxable_paise BIGINT NOT NULL,
	cgst_paise BIGINT NOT NULL,
	sgst_paise BIGINT NOT NULL,
	igst_paise BIGINT NOT NULL,
	line_total_paise BIGINT NOT NULL,
	CONSTRAINT pk_invoice_items PRIMARY KEY (id),
	CONSTRAINT uq_invoice_items_invoice_id_line_no UNIQUE (invoice_id, line_no),
	CONSTRAINT fk_invoice_items_org_id_invoice_id_invoices FOREIGN KEY(org_id, invoice_id) REFERENCES invoices (org_id, id) ON DELETE CASCADE,
	CONSTRAINT ck_invoice_items_quantity_positive CHECK (quantity > 0),
	CONSTRAINT ck_invoice_items_price_non_negative CHECK (unit_price_paise >= 0),
	CONSTRAINT ck_invoice_items_gst_rate CHECK (gst_rate_bp IN (0,500,1800,4000)),
	CONSTRAINT ck_invoice_items_igst_exclusive CHECK (igst_paise = 0 OR (cgst_paise = 0 AND sgst_paise = 0)),
	CONSTRAINT ck_invoice_items_total_matches CHECK (line_total_paise = taxable_paise + cgst_paise + sgst_paise + igst_paise),
	CONSTRAINT fk_invoice_items_org_id_organizations FOREIGN KEY(org_id) REFERENCES organizations (id)
)""",
    """CREATE INDEX ix_invoice_items_org_invoice ON invoice_items (org_id, invoice_id)""",
    """CREATE TABLE payment_orders (
	invoice_id UUID NOT NULL,
	provider TEXT DEFAULT 'razorpay' NOT NULL,
	provider_order_id TEXT NOT NULL,
	receipt TEXT NOT NULL,
	amount_paise BIGINT NOT NULL,
	status TEXT DEFAULT 'created' NOT NULL,
	id UUID NOT NULL,
	org_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_payment_orders PRIMARY KEY (id),
	CONSTRAINT uq_payment_orders_org_id_id UNIQUE (org_id, id),
	CONSTRAINT uq_payment_orders_provider_provider_order_id UNIQUE (provider, provider_order_id),
	CONSTRAINT uq_payment_orders_receipt UNIQUE (receipt),
	CONSTRAINT fk_payment_orders_org_id_invoice_id_invoices FOREIGN KEY(org_id, invoice_id) REFERENCES invoices (org_id, id),
	CONSTRAINT ck_payment_orders_provider CHECK (provider IN ('razorpay')),
	CONSTRAINT ck_payment_orders_receipt_len CHECK (char_length(receipt) <= 40),
	CONSTRAINT ck_payment_orders_amount_positive CHECK (amount_paise > 0),
	CONSTRAINT ck_payment_orders_status CHECK (status IN ('created','paid','superseded')),
	CONSTRAINT fk_payment_orders_org_id_organizations FOREIGN KEY(org_id) REFERENCES organizations (id)
)""",
    """CREATE UNIQUE INDEX uq_payment_orders_open_per_invoice ON payment_orders (invoice_id) WHERE status = 'created'""",
    """CREATE TABLE payments (
	invoice_id UUID NOT NULL,
	payment_order_id UUID,
	provider TEXT NOT NULL,
	provider_payment_id TEXT NOT NULL,
	amount_paise BIGINT NOT NULL,
	status TEXT NOT NULL,
	method TEXT,
	captured_at TIMESTAMP WITH TIME ZONE,
	paid_on DATE,
	failure_code TEXT,
	recorded_by_user_id UUID,
	reference TEXT,
	id UUID NOT NULL,
	org_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_payments PRIMARY KEY (id),
	CONSTRAINT uq_payments_org_id_id UNIQUE (org_id, id),
	CONSTRAINT uq_payments_provider_provider_payment_id UNIQUE (provider, provider_payment_id),
	CONSTRAINT fk_payments_org_id_invoice_id_invoices FOREIGN KEY(org_id, invoice_id) REFERENCES invoices (org_id, id),
	CONSTRAINT fk_payments_org_id_payment_order_id_payment_orders FOREIGN KEY(org_id, payment_order_id) REFERENCES payment_orders (org_id, id),
	CONSTRAINT fk_payments_org_id_recorded_by_user_id_users FOREIGN KEY(org_id, recorded_by_user_id) REFERENCES users (org_id, id),
	CONSTRAINT ck_payments_provider CHECK (provider IN ('razorpay','manual')),
	CONSTRAINT ck_payments_amount_positive CHECK (amount_paise > 0),
	CONSTRAINT ck_payments_status CHECK (status IN ('authorized','captured','failed')),
	CONSTRAINT ck_payments_captured_fields CHECK (status <> 'captured' OR (captured_at IS NOT NULL AND paid_on IS NOT NULL)),
	CONSTRAINT fk_payments_org_id_organizations FOREIGN KEY(org_id) REFERENCES organizations (id)
)""",
    """CREATE INDEX ix_payments_org_invoice ON payments (org_id, invoice_id)""",
    """CREATE INDEX ix_payments_org_paid_on ON payments (org_id, paid_on)""",
]

TABLES_REVERSE = [
    "payments",
    "payment_orders",
    "invoice_items",
    "invoices",
    "deals",
    "audit_logs",
    "webhook_events",
    "users",
    "invoice_sequences",
    "customers",
    "organizations",
]

# Grants are tolerant of a database that has no application roles (a bare superuser dev database);
# when roles.sql was run they take effect. 07 section 1.3.
GRANTS = """
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'revops_app') THEN
    -- Append-only audit trail: the runtime role may only INSERT and SELECT.
    REVOKE UPDATE, DELETE, TRUNCATE ON public.audit_logs FROM revops_app;
    -- /readyz reads the revision; the app must never write to it.
    REVOKE ALL ON public.alembic_version FROM revops_app;
    GRANT SELECT ON public.alembic_version TO revops_app;
  END IF;
END $$;
"""


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)
    op.execute(GRANTS)


def downgrade() -> None:
    for table in TABLES_REVERSE:
        op.execute(f"DROP TABLE IF EXISTS public.{table} CASCADE")
