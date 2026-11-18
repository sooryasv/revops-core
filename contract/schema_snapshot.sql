--
-- PostgreSQL database dump
--

\restrict 9d29WiFXH1pDFYXmb14sJEvO6PTliLwdXSVOGzWAjHi4nVMOgYSRbeasiutvKBn

-- Dumped from database version 16.15 (Ubuntu 16.15-0ubuntu0.24.04.1)
-- Dumped by pg_dump version 16.15 (Ubuntu 16.15-0ubuntu0.24.04.1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'SQL_ASCII';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: ai; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA IF NOT EXISTS ai;


--
-- Name: public; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA IF NOT EXISTS public;


--
-- Name: SCHEMA public; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA public IS 'standard public schema';


--
-- Name: refresh_collections_mv(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.refresh_collections_mv() RETURNS void
    LANGUAGE sql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$ REFRESH MATERIALIZED VIEW CONCURRENTLY public.mv_collections_daily; $$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: invoices; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.invoices (
    customer_id uuid NOT NULL,
    deal_id uuid,
    number text,
    status text DEFAULT 'draft'::text NOT NULL,
    issue_date date,
    due_date date,
    payment_terms_days integer NOT NULL,
    place_of_supply_state_code text NOT NULL,
    supply_type text,
    subtotal_paise bigint DEFAULT '0'::bigint NOT NULL,
    cgst_paise bigint DEFAULT '0'::bigint NOT NULL,
    sgst_paise bigint DEFAULT '0'::bigint NOT NULL,
    igst_paise bigint DEFAULT '0'::bigint NOT NULL,
    total_paise bigint DEFAULT '0'::bigint NOT NULL,
    amount_paid_paise bigint DEFAULT '0'::bigint NOT NULL,
    void_reason text,
    pay_token_hash text,
    pay_token_expires_at timestamp with time zone,
    version integer DEFAULT 1 NOT NULL,
    currency text DEFAULT 'INR'::text NOT NULL,
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_invoices_currency_inr CHECK ((currency = 'INR'::text)),
    CONSTRAINT ck_invoices_igst_exclusive CHECK (((igst_paise = 0) OR ((cgst_paise = 0) AND (sgst_paise = 0)))),
    CONSTRAINT ck_invoices_issued_fields CHECK (((status = 'draft'::text) OR ((issue_date IS NOT NULL) AND (due_date IS NOT NULL) AND (supply_type IS NOT NULL)))),
    CONSTRAINT ck_invoices_non_negative CHECK (((subtotal_paise >= 0) AND (cgst_paise >= 0) AND (sgst_paise >= 0) AND (igst_paise >= 0) AND (amount_paid_paise >= 0))),
    CONSTRAINT ck_invoices_number_len CHECK (((number IS NULL) OR (char_length(number) <= 16))),
    CONSTRAINT ck_invoices_number_when_issued CHECK (((status = 'draft'::text) OR (number IS NOT NULL))),
    CONSTRAINT ck_invoices_pos_len CHECK ((char_length(place_of_supply_state_code) = 2)),
    CONSTRAINT ck_invoices_status CHECK ((status = ANY (ARRAY['draft'::text, 'issued'::text, 'void'::text]))),
    CONSTRAINT ck_invoices_supply_type CHECK (((supply_type IS NULL) OR (supply_type = ANY (ARRAY['intra'::text, 'inter'::text])))),
    CONSTRAINT ck_invoices_terms_range CHECK (((payment_terms_days >= 0) AND (payment_terms_days <= 365))),
    CONSTRAINT ck_invoices_total_matches CHECK ((total_paise = (((subtotal_paise + cgst_paise) + sgst_paise) + igst_paise))),
    CONSTRAINT ck_invoices_void_needs_reason CHECK (((status <> 'void'::text) OR (void_reason IS NOT NULL)))
);


--
-- Name: payments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.payments (
    invoice_id uuid NOT NULL,
    payment_order_id uuid,
    provider text NOT NULL,
    provider_payment_id text NOT NULL,
    amount_paise bigint NOT NULL,
    status text NOT NULL,
    method text,
    captured_at timestamp with time zone,
    paid_on date,
    failure_code text,
    recorded_by_user_id uuid,
    reference text,
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_payments_amount_positive CHECK ((amount_paise > 0)),
    CONSTRAINT ck_payments_captured_fields CHECK (((status <> 'captured'::text) OR ((captured_at IS NOT NULL) AND (paid_on IS NOT NULL)))),
    CONSTRAINT ck_payments_provider CHECK ((provider = ANY (ARRAY['razorpay'::text, 'manual'::text]))),
    CONSTRAINT ck_payments_status CHECK ((status = ANY (ARRAY['authorized'::text, 'captured'::text, 'failed'::text])))
);


--
-- Name: mv_collections_daily; Type: MATERIALIZED VIEW; Schema: public; Owner: -
--

CREATE MATERIALIZED VIEW public.mv_collections_daily AS
 WITH billed AS (
         SELECT invoices.org_id,
            invoices.issue_date AS day,
            (sum(invoices.total_paise))::bigint AS billed_paise,
            (count(*))::integer AS invoices_issued
           FROM public.invoices
          WHERE (invoices.status = 'issued'::text)
          GROUP BY invoices.org_id, invoices.issue_date
        ), collected AS (
         SELECT payments.org_id,
            payments.paid_on AS day,
            (sum(payments.amount_paise))::bigint AS collected_paise,
            (count(*))::integer AS payments_count
           FROM public.payments
          WHERE (payments.status = 'captured'::text)
          GROUP BY payments.org_id, payments.paid_on
        )
 SELECT COALESCE(b.org_id, c.org_id) AS org_id,
    COALESCE(b.day, c.day) AS day,
    COALESCE(b.billed_paise, (0)::bigint) AS billed_paise,
    COALESCE(c.collected_paise, (0)::bigint) AS collected_paise,
    COALESCE(b.invoices_issued, 0) AS invoices_issued,
    COALESCE(c.payments_count, 0) AS payments_count
   FROM (billed b
     FULL JOIN collected c ON (((c.org_id = b.org_id) AND (c.day = b.day))))
  WITH NO DATA;


--
-- Name: v_collections_daily; Type: VIEW; Schema: ai; Owner: -
--

CREATE VIEW ai.v_collections_daily WITH (security_barrier='true') AS
 SELECT org_id,
    day,
    billed_paise,
    collected_paise,
    invoices_issued,
    payments_count
   FROM public.mv_collections_daily m
  WHERE (org_id = (NULLIF(current_setting('app.current_org'::text, true), ''::text))::uuid);


--
-- Name: customers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.customers (
    name text NOT NULL,
    gstin text,
    state_code text NOT NULL,
    email text,
    phone text,
    billing_address text DEFAULT ''::text NOT NULL,
    has_written_agreement boolean DEFAULT false NOT NULL,
    default_terms_days integer DEFAULT 30 NOT NULL,
    notes text,
    is_active boolean DEFAULT true NOT NULL,
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_customers_default_terms CHECK (((default_terms_days >= 0) AND (default_terms_days <= 365))),
    CONSTRAINT ck_customers_state_code_len CHECK ((char_length(state_code) = 2))
);


--
-- Name: v_customers; Type: VIEW; Schema: ai; Owner: -
--

CREATE VIEW ai.v_customers WITH (security_barrier='true') AS
 SELECT c.org_id,
    c.id AS customer_id,
    c.name,
    c.state_code,
    c.has_written_agreement,
    c.default_terms_days,
    (COALESCE(a.invoices_count, (0)::bigint))::integer AS invoices_count,
    (COALESCE(a.total_billed_paise, (0)::numeric))::bigint AS total_billed_paise,
    (COALESCE(a.total_paid_paise, (0)::numeric))::bigint AS total_paid_paise,
    (COALESCE(a.outstanding_paise, (0)::numeric))::bigint AS outstanding_paise,
    (COALESCE(a.paid_invoices_count, (0)::bigint))::integer AS paid_invoices_count,
    (COALESCE(a.late_paid_invoices_count, (0)::bigint))::integer AS late_paid_invoices_count,
    (
        CASE
            WHEN (COALESCE(a.paid_invoices_count, (0)::bigint) = 0) THEN (0)::numeric
            ELSE round((((a.late_paid_invoices_count)::numeric * 10000.0) / (a.paid_invoices_count)::numeric))
        END)::integer AS late_payment_rate_bp,
    (round(a.avg_days_to_pay, 2))::numeric(8,2) AS avg_days_to_pay,
    c.notes
   FROM (public.customers c
     LEFT JOIN ( SELECT i.org_id,
            i.customer_id,
            count(*) AS invoices_count,
            sum(i.total_paise) AS total_billed_paise,
            sum(i.amount_paid_paise) AS total_paid_paise,
            sum(GREATEST((i.total_paise - i.amount_paid_paise), (0)::bigint)) AS outstanding_paise,
            count(*) FILTER (WHERE ((i.total_paise > 0) AND (i.amount_paid_paise >= i.total_paise))) AS paid_invoices_count,
            count(*) FILTER (WHERE ((i.total_paise > 0) AND (i.amount_paid_paise >= i.total_paise) AND (p.last_paid_on > i.due_date))) AS late_paid_invoices_count,
            avg((p.last_paid_on - i.issue_date)) FILTER (WHERE ((i.total_paise > 0) AND (i.amount_paid_paise >= i.total_paise))) AS avg_days_to_pay
           FROM (public.invoices i
             LEFT JOIN ( SELECT payments.org_id,
                    payments.invoice_id,
                    max(payments.paid_on) AS last_paid_on
                   FROM public.payments
                  WHERE ((payments.status = 'captured'::text) AND (payments.org_id = (NULLIF(current_setting('app.current_org'::text, true), ''::text))::uuid))
                  GROUP BY payments.org_id, payments.invoice_id) p ON (((p.org_id = i.org_id) AND (p.invoice_id = i.id))))
          WHERE ((i.status = 'issued'::text) AND (i.org_id = (NULLIF(current_setting('app.current_org'::text, true), ''::text))::uuid))
          GROUP BY i.org_id, i.customer_id) a ON (((a.org_id = c.org_id) AND (a.customer_id = c.id))))
  WHERE (c.org_id = (NULLIF(current_setting('app.current_org'::text, true), ''::text))::uuid);


--
-- Name: deals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deals (
    customer_id uuid NOT NULL,
    title text NOT NULL,
    stage text DEFAULT 'lead'::text NOT NULL,
    amount_estimate_paise bigint DEFAULT '0'::bigint NOT NULL,
    owner_user_id uuid NOT NULL,
    expected_close_date date,
    lost_reason text,
    stage_changed_at timestamp with time zone DEFAULT now() NOT NULL,
    closed_at timestamp with time zone,
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_deals_amount_non_negative CHECK ((amount_estimate_paise >= 0)),
    CONSTRAINT ck_deals_closed_matches_stage CHECK (((stage = ANY (ARRAY['won'::text, 'lost'::text])) = (closed_at IS NOT NULL))),
    CONSTRAINT ck_deals_lost_needs_reason CHECK (((stage <> 'lost'::text) OR (lost_reason IS NOT NULL))),
    CONSTRAINT ck_deals_stage CHECK ((stage = ANY (ARRAY['lead'::text, 'qualified'::text, 'proposal'::text, 'won'::text, 'lost'::text])))
);


--
-- Name: v_deals; Type: VIEW; Schema: ai; Owner: -
--

CREATE VIEW ai.v_deals WITH (security_barrier='true') AS
 SELECT d.org_id,
    d.id AS deal_id,
    d.customer_id,
    c.name AS customer_name,
    d.title,
    d.stage,
    d.amount_estimate_paise,
    d.created_at,
    d.stage_changed_at,
    d.closed_at
   FROM (public.deals d
     JOIN public.customers c ON (((c.org_id = d.org_id) AND (c.id = d.customer_id))))
  WHERE (d.org_id = (NULLIF(current_setting('app.current_org'::text, true), ''::text))::uuid);


--
-- Name: organizations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.organizations (
    id uuid NOT NULL,
    name text NOT NULL,
    legal_name text NOT NULL,
    gstin text,
    state_code text NOT NULL,
    udyam_category text DEFAULT 'none'::text NOT NULL,
    address text DEFAULT ''::text NOT NULL,
    default_terms_days integer DEFAULT 30 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_organizations_default_terms CHECK (((default_terms_days >= 0) AND (default_terms_days <= 365))),
    CONSTRAINT ck_organizations_state_code_len CHECK ((char_length(state_code) = 2)),
    CONSTRAINT ck_organizations_udyam_category CHECK ((udyam_category = ANY (ARRAY['micro'::text, 'small'::text, 'medium'::text, 'none'::text])))
);


--
-- Name: v_invoices; Type: VIEW; Schema: ai; Owner: -
--

CREATE VIEW ai.v_invoices WITH (security_barrier='true') AS
 SELECT org_id,
    invoice_id,
    invoice_number,
    customer_id,
    customer_name,
    status,
    payment_status,
    issue_date,
    due_date,
    payment_terms_days,
    total_paise,
    amount_paid_paise,
    amount_due_paise,
    overpaid_paise,
    (days_overdue > 0) AS is_overdue,
    days_overdue,
        CASE
            WHEN (days_overdue = 0) THEN 'not_due'::text
            WHEN (days_overdue <= 30) THEN '1-30'::text
            WHEN (days_overdue <= 60) THEN '31-60'::text
            WHEN (days_overdue <= 90) THEN '61-90'::text
            ELSE '90+'::text
        END AS aging_bucket,
    statutory_limit_days,
    COALESCE((payment_terms_days > statutory_limit_days), false) AS terms_exceed_statutory
   FROM ( SELECT i.org_id,
            i.id AS invoice_id,
            i.number AS invoice_number,
            i.customer_id,
            c.name AS customer_name,
            i.status,
                CASE
                    WHEN (i.status <> 'issued'::text) THEN NULL::text
                    WHEN (i.amount_paid_paise <= 0) THEN 'unpaid'::text
                    WHEN (i.amount_paid_paise < i.total_paise) THEN 'partially_paid'::text
                    ELSE 'paid'::text
                END AS payment_status,
            i.issue_date,
            i.due_date,
            i.payment_terms_days,
            i.total_paise,
            i.amount_paid_paise,
            GREATEST((i.total_paise - i.amount_paid_paise), (0)::bigint) AS amount_due_paise,
            GREATEST((i.amount_paid_paise - i.total_paise), (0)::bigint) AS overpaid_paise,
                CASE
                    WHEN ((i.status = 'issued'::text) AND (i.total_paise > i.amount_paid_paise) AND (i.due_date < t.today)) THEN (t.today - i.due_date)
                    ELSE 0
                END AS days_overdue,
                CASE
                    WHEN (o.udyam_category = ANY (ARRAY['micro'::text, 'small'::text])) THEN
                    CASE
                        WHEN c.has_written_agreement THEN 45
                        ELSE 15
                    END
                    ELSE NULL::integer
                END AS statutory_limit_days
           FROM (((public.invoices i
             JOIN public.customers c ON (((c.org_id = i.org_id) AND (c.id = i.customer_id))))
             JOIN public.organizations o ON ((o.id = i.org_id)))
             CROSS JOIN LATERAL ( SELECT (timezone('Asia/Kolkata'::text, now()))::date AS today) t)
          WHERE (i.org_id = (NULLIF(current_setting('app.current_org'::text, true), ''::text))::uuid)) d
  WHERE (org_id = (NULLIF(current_setting('app.current_org'::text, true), ''::text))::uuid);


--
-- Name: v_payments; Type: VIEW; Schema: ai; Owner: -
--

CREATE VIEW ai.v_payments WITH (security_barrier='true') AS
 SELECT org_id,
    id AS payment_id,
    invoice_id,
    provider,
    amount_paise,
    status,
    method,
    captured_at,
    paid_on
   FROM public.payments p
  WHERE (org_id = (NULLIF(current_setting('app.current_org'::text, true), ''::text))::uuid);


--
-- Name: v_receivables_summary; Type: VIEW; Schema: ai; Owner: -
--

CREATE VIEW ai.v_receivables_summary WITH (security_barrier='true') AS
 SELECT o.id AS org_id,
    t.today AS as_of_date,
    (COALESCE(a.outstanding_paise, (0)::numeric))::bigint AS outstanding_paise,
    (COALESCE(a.overdue_paise, (0)::numeric))::bigint AS overdue_paise,
    (COALESCE(a.billed_last_30d_paise, (0)::numeric))::bigint AS billed_last_30d_paise,
    (
        CASE
            WHEN (COALESCE(a.billed_last_30d_paise, (0)::numeric) = (0)::numeric) THEN NULL::numeric
            ELSE LEAST(round(((a.outstanding_paise / a.billed_last_30d_paise) * (30)::numeric), 2), 999999.99)
        END)::numeric(8,2) AS dso_30d
   FROM ((public.organizations o
     CROSS JOIN LATERAL ( SELECT (timezone('Asia/Kolkata'::text, now()))::date AS today) t)
     LEFT JOIN LATERAL ( SELECT sum(GREATEST((i.total_paise - i.amount_paid_paise), (0)::bigint)) AS outstanding_paise,
            sum(GREATEST((i.total_paise - i.amount_paid_paise), (0)::bigint)) FILTER (WHERE (i.due_date < t.today)) AS overdue_paise,
            sum(i.total_paise) FILTER (WHERE (i.issue_date > (t.today - 30))) AS billed_last_30d_paise
           FROM public.invoices i
          WHERE ((i.org_id = o.id) AND (i.status = 'issued'::text))) a ON (true))
  WHERE (o.id = (NULLIF(current_setting('app.current_org'::text, true), ''::text))::uuid);


--
-- Name: webhook_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.webhook_events (
    id uuid NOT NULL,
    org_id uuid,
    provider text NOT NULL,
    provider_event_id text NOT NULL,
    event_type text NOT NULL,
    received_at timestamp with time zone DEFAULT now() NOT NULL,
    payload jsonb NOT NULL,
    raw_body text NOT NULL,
    processing_status text DEFAULT 'received'::text NOT NULL,
    attempts integer DEFAULT 1 NOT NULL,
    processed_at timestamp with time zone,
    last_error text,
    CONSTRAINT ck_webhook_events_provider CHECK ((provider = 'razorpay'::text)),
    CONSTRAINT ck_webhook_events_status CHECK ((processing_status = ANY (ARRAY['received'::text, 'processed'::text, 'ignored'::text, 'quarantined'::text, 'failed'::text])))
);


--
-- Name: v_webhook_health; Type: VIEW; Schema: ai; Owner: -
--

CREATE VIEW ai.v_webhook_health WITH (security_barrier='true') AS
 SELECT org_id,
    provider,
    event_type,
    processing_status,
    (count(*) FILTER (WHERE (received_at >= (now() - '7 days'::interval))))::integer AS event_count_7d,
    max(received_at) AS last_seen_at,
    (array_agg(last_error ORDER BY received_at DESC) FILTER (WHERE (last_error IS NOT NULL)))[1] AS last_error
   FROM public.webhook_events w
  WHERE (org_id = (NULLIF(current_setting('app.current_org'::text, true), ''::text))::uuid)
  GROUP BY org_id, provider, event_type, processing_status;


--
-- Name: alembic_version; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);


--
-- Name: audit_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_logs (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    actor_type text NOT NULL,
    actor_user_id uuid,
    action text NOT NULL,
    entity_type text NOT NULL,
    entity_id uuid,
    before jsonb,
    after jsonb,
    request_id text,
    ip text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_audit_logs_actor_type CHECK ((actor_type = ANY (ARRAY['user'::text, 'system'::text, 'webhook'::text, 'public'::text])))
);


--
-- Name: invoice_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.invoice_items (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    invoice_id uuid NOT NULL,
    line_no integer NOT NULL,
    description text NOT NULL,
    hsn_sac text,
    quantity integer NOT NULL,
    unit_price_paise bigint NOT NULL,
    gst_rate_bp integer NOT NULL,
    taxable_paise bigint NOT NULL,
    cgst_paise bigint NOT NULL,
    sgst_paise bigint NOT NULL,
    igst_paise bigint NOT NULL,
    line_total_paise bigint NOT NULL,
    CONSTRAINT ck_invoice_items_gst_rate CHECK ((gst_rate_bp = ANY (ARRAY[0, 500, 1800, 4000]))),
    CONSTRAINT ck_invoice_items_igst_exclusive CHECK (((igst_paise = 0) OR ((cgst_paise = 0) AND (sgst_paise = 0)))),
    CONSTRAINT ck_invoice_items_price_non_negative CHECK ((unit_price_paise >= 0)),
    CONSTRAINT ck_invoice_items_quantity_positive CHECK ((quantity > 0)),
    CONSTRAINT ck_invoice_items_total_matches CHECK ((line_total_paise = (((taxable_paise + cgst_paise) + sgst_paise) + igst_paise)))
);


--
-- Name: invoice_sequences; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.invoice_sequences (
    org_id uuid NOT NULL,
    fy_label text NOT NULL,
    last_value integer DEFAULT 0 NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_invoice_sequences_last_value_range CHECK (((last_value >= 0) AND (last_value <= 999999)))
);


--
-- Name: payment_orders; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.payment_orders (
    invoice_id uuid NOT NULL,
    provider text DEFAULT 'razorpay'::text NOT NULL,
    provider_order_id text NOT NULL,
    receipt text NOT NULL,
    amount_paise bigint NOT NULL,
    status text DEFAULT 'created'::text NOT NULL,
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_payment_orders_amount_positive CHECK ((amount_paise > 0)),
    CONSTRAINT ck_payment_orders_provider CHECK ((provider = 'razorpay'::text)),
    CONSTRAINT ck_payment_orders_receipt_len CHECK ((char_length(receipt) <= 40)),
    CONSTRAINT ck_payment_orders_status CHECK ((status = ANY (ARRAY['created'::text, 'paid'::text, 'superseded'::text])))
);


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    email text NOT NULL,
    full_name text NOT NULL,
    password_hash text NOT NULL,
    role text NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    token_version integer DEFAULT 0 NOT NULL,
    last_login_at timestamp with time zone,
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_users_role CHECK ((role = ANY (ARRAY['admin'::text, 'finance'::text, 'sales'::text])))
);


--
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);


--
-- Name: audit_logs pk_audit_logs; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_logs
    ADD CONSTRAINT pk_audit_logs PRIMARY KEY (id);


--
-- Name: customers pk_customers; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customers
    ADD CONSTRAINT pk_customers PRIMARY KEY (id);


--
-- Name: deals pk_deals; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deals
    ADD CONSTRAINT pk_deals PRIMARY KEY (id);


--
-- Name: invoice_items pk_invoice_items; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoice_items
    ADD CONSTRAINT pk_invoice_items PRIMARY KEY (id);


--
-- Name: invoice_sequences pk_invoice_sequences; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoice_sequences
    ADD CONSTRAINT pk_invoice_sequences PRIMARY KEY (org_id, fy_label);


--
-- Name: invoices pk_invoices; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoices
    ADD CONSTRAINT pk_invoices PRIMARY KEY (id);


--
-- Name: organizations pk_organizations; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.organizations
    ADD CONSTRAINT pk_organizations PRIMARY KEY (id);


--
-- Name: payment_orders pk_payment_orders; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payment_orders
    ADD CONSTRAINT pk_payment_orders PRIMARY KEY (id);


--
-- Name: payments pk_payments; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payments
    ADD CONSTRAINT pk_payments PRIMARY KEY (id);


--
-- Name: users pk_users; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT pk_users PRIMARY KEY (id);


--
-- Name: webhook_events pk_webhook_events; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.webhook_events
    ADD CONSTRAINT pk_webhook_events PRIMARY KEY (id);


--
-- Name: customers uq_customers_org_id_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customers
    ADD CONSTRAINT uq_customers_org_id_id UNIQUE (org_id, id);


--
-- Name: deals uq_deals_org_id_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deals
    ADD CONSTRAINT uq_deals_org_id_id UNIQUE (org_id, id);


--
-- Name: invoice_items uq_invoice_items_invoice_id_line_no; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoice_items
    ADD CONSTRAINT uq_invoice_items_invoice_id_line_no UNIQUE (invoice_id, line_no);


--
-- Name: invoices uq_invoices_org_id_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoices
    ADD CONSTRAINT uq_invoices_org_id_id UNIQUE (org_id, id);


--
-- Name: invoices uq_invoices_org_id_number; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoices
    ADD CONSTRAINT uq_invoices_org_id_number UNIQUE (org_id, number);


--
-- Name: invoices uq_invoices_pay_token_hash; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoices
    ADD CONSTRAINT uq_invoices_pay_token_hash UNIQUE (pay_token_hash);


--
-- Name: payment_orders uq_payment_orders_org_id_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payment_orders
    ADD CONSTRAINT uq_payment_orders_org_id_id UNIQUE (org_id, id);


--
-- Name: payment_orders uq_payment_orders_provider_provider_order_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payment_orders
    ADD CONSTRAINT uq_payment_orders_provider_provider_order_id UNIQUE (provider, provider_order_id);


--
-- Name: payment_orders uq_payment_orders_receipt; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payment_orders
    ADD CONSTRAINT uq_payment_orders_receipt UNIQUE (receipt);


--
-- Name: payments uq_payments_org_id_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payments
    ADD CONSTRAINT uq_payments_org_id_id UNIQUE (org_id, id);


--
-- Name: payments uq_payments_provider_provider_payment_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payments
    ADD CONSTRAINT uq_payments_provider_provider_payment_id UNIQUE (provider, provider_payment_id);


--
-- Name: users uq_users_org_id_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT uq_users_org_id_id UNIQUE (org_id, id);


--
-- Name: webhook_events uq_webhook_events_provider_provider_event_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.webhook_events
    ADD CONSTRAINT uq_webhook_events_provider_provider_event_id UNIQUE (provider, provider_event_id);


--
-- Name: ix_audit_logs_org_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_logs_org_created ON public.audit_logs USING btree (org_id, created_at DESC);


--
-- Name: ix_customers_org_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_customers_org_name ON public.customers USING btree (org_id, name);


--
-- Name: ix_deals_org_stage; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_deals_org_stage ON public.deals USING btree (org_id, stage);


--
-- Name: ix_invoice_items_org_invoice; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_invoice_items_org_invoice ON public.invoice_items USING btree (org_id, invoice_id);


--
-- Name: ix_invoices_org_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_invoices_org_created ON public.invoices USING btree (org_id, created_at DESC, id DESC);


--
-- Name: ix_invoices_org_customer; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_invoices_org_customer ON public.invoices USING btree (org_id, customer_id);


--
-- Name: ix_invoices_org_status_due; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_invoices_org_status_due ON public.invoices USING btree (org_id, status, due_date);


--
-- Name: ix_payments_org_invoice; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_payments_org_invoice ON public.payments USING btree (org_id, invoice_id);


--
-- Name: ix_payments_org_paid_on; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_payments_org_paid_on ON public.payments USING btree (org_id, paid_on);


--
-- Name: ix_webhook_events_org_status_received; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_webhook_events_org_status_received ON public.webhook_events USING btree (org_id, processing_status, received_at);


--
-- Name: uq_customers_org_gstin; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_customers_org_gstin ON public.customers USING btree (org_id, gstin) WHERE (gstin IS NOT NULL);


--
-- Name: uq_mv_collections_daily; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_mv_collections_daily ON public.mv_collections_daily USING btree (org_id, day);


--
-- Name: uq_payment_orders_open_per_invoice; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_payment_orders_open_per_invoice ON public.payment_orders USING btree (invoice_id) WHERE (status = 'created'::text);


--
-- Name: uq_users_email_lower; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_users_email_lower ON public.users USING btree (lower(email));


--
-- Name: audit_logs fk_audit_logs_org_id_actor_user_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_logs
    ADD CONSTRAINT fk_audit_logs_org_id_actor_user_id_users FOREIGN KEY (org_id, actor_user_id) REFERENCES public.users(org_id, id);


--
-- Name: audit_logs fk_audit_logs_org_id_organizations; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_logs
    ADD CONSTRAINT fk_audit_logs_org_id_organizations FOREIGN KEY (org_id) REFERENCES public.organizations(id);


--
-- Name: customers fk_customers_org_id_organizations; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customers
    ADD CONSTRAINT fk_customers_org_id_organizations FOREIGN KEY (org_id) REFERENCES public.organizations(id);


--
-- Name: deals fk_deals_org_id_customer_id_customers; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deals
    ADD CONSTRAINT fk_deals_org_id_customer_id_customers FOREIGN KEY (org_id, customer_id) REFERENCES public.customers(org_id, id);


--
-- Name: deals fk_deals_org_id_organizations; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deals
    ADD CONSTRAINT fk_deals_org_id_organizations FOREIGN KEY (org_id) REFERENCES public.organizations(id);


--
-- Name: deals fk_deals_org_id_owner_user_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deals
    ADD CONSTRAINT fk_deals_org_id_owner_user_id_users FOREIGN KEY (org_id, owner_user_id) REFERENCES public.users(org_id, id);


--
-- Name: invoice_items fk_invoice_items_org_id_invoice_id_invoices; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoice_items
    ADD CONSTRAINT fk_invoice_items_org_id_invoice_id_invoices FOREIGN KEY (org_id, invoice_id) REFERENCES public.invoices(org_id, id) ON DELETE CASCADE;


--
-- Name: invoice_items fk_invoice_items_org_id_organizations; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoice_items
    ADD CONSTRAINT fk_invoice_items_org_id_organizations FOREIGN KEY (org_id) REFERENCES public.organizations(id);


--
-- Name: invoice_sequences fk_invoice_sequences_org_id_organizations; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoice_sequences
    ADD CONSTRAINT fk_invoice_sequences_org_id_organizations FOREIGN KEY (org_id) REFERENCES public.organizations(id);


--
-- Name: invoices fk_invoices_org_id_customer_id_customers; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoices
    ADD CONSTRAINT fk_invoices_org_id_customer_id_customers FOREIGN KEY (org_id, customer_id) REFERENCES public.customers(org_id, id);


--
-- Name: invoices fk_invoices_org_id_deal_id_deals; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoices
    ADD CONSTRAINT fk_invoices_org_id_deal_id_deals FOREIGN KEY (org_id, deal_id) REFERENCES public.deals(org_id, id);


--
-- Name: invoices fk_invoices_org_id_organizations; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoices
    ADD CONSTRAINT fk_invoices_org_id_organizations FOREIGN KEY (org_id) REFERENCES public.organizations(id);


--
-- Name: payment_orders fk_payment_orders_org_id_invoice_id_invoices; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payment_orders
    ADD CONSTRAINT fk_payment_orders_org_id_invoice_id_invoices FOREIGN KEY (org_id, invoice_id) REFERENCES public.invoices(org_id, id);


--
-- Name: payment_orders fk_payment_orders_org_id_organizations; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payment_orders
    ADD CONSTRAINT fk_payment_orders_org_id_organizations FOREIGN KEY (org_id) REFERENCES public.organizations(id);


--
-- Name: payments fk_payments_org_id_invoice_id_invoices; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payments
    ADD CONSTRAINT fk_payments_org_id_invoice_id_invoices FOREIGN KEY (org_id, invoice_id) REFERENCES public.invoices(org_id, id);


--
-- Name: payments fk_payments_org_id_organizations; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payments
    ADD CONSTRAINT fk_payments_org_id_organizations FOREIGN KEY (org_id) REFERENCES public.organizations(id);


--
-- Name: payments fk_payments_org_id_payment_order_id_payment_orders; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payments
    ADD CONSTRAINT fk_payments_org_id_payment_order_id_payment_orders FOREIGN KEY (org_id, payment_order_id) REFERENCES public.payment_orders(org_id, id);


--
-- Name: payments fk_payments_org_id_recorded_by_user_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payments
    ADD CONSTRAINT fk_payments_org_id_recorded_by_user_id_users FOREIGN KEY (org_id, recorded_by_user_id) REFERENCES public.users(org_id, id);


--
-- Name: users fk_users_org_id_organizations; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT fk_users_org_id_organizations FOREIGN KEY (org_id) REFERENCES public.organizations(id);


--
-- Name: webhook_events fk_webhook_events_org_id_organizations; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.webhook_events
    ADD CONSTRAINT fk_webhook_events_org_id_organizations FOREIGN KEY (org_id) REFERENCES public.organizations(id);


--
-- Name: SCHEMA ai; Type: ACL; Schema: -; Owner: -
--

GRANT USAGE ON SCHEMA ai TO ai_reader;


--
-- Name: SCHEMA public; Type: ACL; Schema: -; Owner: -
--

REVOKE USAGE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO revops_app;


--
-- Name: FUNCTION refresh_collections_mv(); Type: ACL; Schema: public; Owner: -
--

REVOKE ALL ON FUNCTION public.refresh_collections_mv() FROM PUBLIC;
GRANT ALL ON FUNCTION public.refresh_collections_mv() TO revops_app;


--
-- Name: TABLE invoices; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.invoices TO revops_app;


--
-- Name: TABLE payments; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.payments TO revops_app;


--
-- Name: TABLE mv_collections_daily; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT ON TABLE public.mv_collections_daily TO revops_app;


--
-- Name: TABLE v_collections_daily; Type: ACL; Schema: ai; Owner: -
--

GRANT SELECT ON TABLE ai.v_collections_daily TO ai_reader;


--
-- Name: TABLE customers; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.customers TO revops_app;


--
-- Name: TABLE v_customers; Type: ACL; Schema: ai; Owner: -
--

GRANT SELECT ON TABLE ai.v_customers TO ai_reader;


--
-- Name: TABLE deals; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.deals TO revops_app;


--
-- Name: TABLE v_deals; Type: ACL; Schema: ai; Owner: -
--

GRANT SELECT ON TABLE ai.v_deals TO ai_reader;


--
-- Name: TABLE organizations; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.organizations TO revops_app;


--
-- Name: TABLE v_invoices; Type: ACL; Schema: ai; Owner: -
--

GRANT SELECT ON TABLE ai.v_invoices TO ai_reader;


--
-- Name: TABLE v_payments; Type: ACL; Schema: ai; Owner: -
--

GRANT SELECT ON TABLE ai.v_payments TO ai_reader;


--
-- Name: TABLE v_receivables_summary; Type: ACL; Schema: ai; Owner: -
--

GRANT SELECT ON TABLE ai.v_receivables_summary TO ai_reader;


--
-- Name: TABLE webhook_events; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.webhook_events TO revops_app;


--
-- Name: TABLE v_webhook_health; Type: ACL; Schema: ai; Owner: -
--

GRANT SELECT ON TABLE ai.v_webhook_health TO ai_reader;


--
-- Name: TABLE alembic_version; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT ON TABLE public.alembic_version TO revops_app;


--
-- Name: TABLE audit_logs; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT ON TABLE public.audit_logs TO revops_app;


--
-- Name: TABLE invoice_items; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.invoice_items TO revops_app;


--
-- Name: TABLE invoice_sequences; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.invoice_sequences TO revops_app;


--
-- Name: TABLE payment_orders; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.payment_orders TO revops_app;


--
-- Name: TABLE users; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.users TO revops_app;


--
-- Name: DEFAULT PRIVILEGES FOR TABLES; Type: DEFAULT ACL; Schema: ai; Owner: -
--

ALTER DEFAULT PRIVILEGES FOR ROLE revops_owner IN SCHEMA ai GRANT SELECT ON TABLES TO ai_reader;


--
-- Name: DEFAULT PRIVILEGES FOR SEQUENCES; Type: DEFAULT ACL; Schema: public; Owner: -
--

ALTER DEFAULT PRIVILEGES FOR ROLE revops_owner IN SCHEMA public GRANT SELECT,USAGE ON SEQUENCES TO revops_app;


--
-- Name: DEFAULT PRIVILEGES FOR TABLES; Type: DEFAULT ACL; Schema: public; Owner: -
--

ALTER DEFAULT PRIVILEGES FOR ROLE revops_owner IN SCHEMA public GRANT SELECT,INSERT,DELETE,UPDATE ON TABLES TO revops_app;


--
-- PostgreSQL database dump complete
--

\unrestrict 9d29WiFXH1pDFYXmb14sJEvO6PTliLwdXSVOGzWAjHi4nVMOgYSRbeasiutvKBn

