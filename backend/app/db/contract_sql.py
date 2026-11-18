"""SQL for the reporting layer and the `ai` schema contract (02 section 3, 07 sections 1.2 and 1.4).

This module is the SINGLE SOURCE of that DDL. Migration 0003 executes it, and `export-contract` writes the very same text
to `contract/ai_views.sql`, so the contract package cannot drift from what the migrations create (a test pins this).
A later change to any view means: a new migration that embeds the new full text, and a bump of `contract/CONTRACT_VERSION`.

Rules every `ai` view follows:
* `WITH (security_barrier = true)`: caller-supplied predicates are never evaluated before the tenant filter.
* ends with `WHERE org_id = nullif(current_setting('app.current_org', true), '')::uuid`; an unset setting gives NULL and
  therefore zero rows (fail closed).
* "today" is `timezone('Asia/Kolkata', now())::date`, never CURRENT_DATE (the server runs in UTC; overdue would flip at
  05:30 IST). `now()` is deliberately unqualified so a test can shadow it through search_path and prove the IST boundary.
"""

from __future__ import annotations

TENANT = "nullif(current_setting('app.current_org', true), '')::uuid"
IST_TODAY = "timezone('Asia/Kolkata', now())::date"

# --------------------------------------------------------------------------------------- public: gold layer
REPORTING_SQL = """
CREATE MATERIALIZED VIEW public.mv_collections_daily AS
WITH billed AS (
    SELECT org_id, issue_date AS day, sum(total_paise)::bigint AS billed_paise, count(*)::int AS invoices_issued
      FROM public.invoices
     WHERE status = 'issued'
     GROUP BY org_id, issue_date
), collected AS (
    SELECT org_id, paid_on AS day, sum(amount_paise)::bigint AS collected_paise, count(*)::int AS payments_count
      FROM public.payments
     WHERE status = 'captured'
     GROUP BY org_id, paid_on
)
SELECT COALESCE(b.org_id, c.org_id) AS org_id,
       COALESCE(b.day, c.day) AS day,
       COALESCE(b.billed_paise, 0)::bigint AS billed_paise,
       COALESCE(c.collected_paise, 0)::bigint AS collected_paise,
       COALESCE(b.invoices_issued, 0)::int AS invoices_issued,
       COALESCE(c.payments_count, 0)::int AS payments_count
  FROM billed b
  FULL JOIN collected c ON c.org_id = b.org_id AND c.day = b.day
  WITH DATA;

CREATE UNIQUE INDEX uq_mv_collections_daily ON public.mv_collections_daily (org_id, day);

REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON public.mv_collections_daily FROM PUBLIC;

CREATE OR REPLACE FUNCTION public.refresh_collections_mv()
RETURNS void LANGUAGE sql SECURITY DEFINER
SET search_path = public, pg_temp
AS $$ REFRESH MATERIALIZED VIEW CONCURRENTLY public.mv_collections_daily; $$;

REVOKE ALL ON FUNCTION public.refresh_collections_mv() FROM PUBLIC;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'revops_app') THEN
    GRANT EXECUTE ON FUNCTION public.refresh_collections_mv() TO revops_app;
    GRANT SELECT ON public.mv_collections_daily TO revops_app;
    REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON public.mv_collections_daily FROM revops_app;
  END IF;
END $$;
"""

DROP_REPORTING_SQL = """
DROP FUNCTION IF EXISTS public.refresh_collections_mv();

DROP MATERIALIZED VIEW IF EXISTS public.mv_collections_daily;
"""

# --------------------------------------------------------------------------------------- ai schema
_AI_VIEWS_TEMPLATE = """
CREATE SCHEMA IF NOT EXISTS ai;

CREATE OR REPLACE VIEW ai.v_invoices WITH (security_barrier = true) AS
SELECT d.org_id, d.invoice_id, d.invoice_number, d.customer_id, d.customer_name, d.status, d.payment_status,
       d.issue_date, d.due_date, d.payment_terms_days, d.total_paise, d.amount_paid_paise, d.amount_due_paise,
       d.overpaid_paise,
       (d.days_overdue > 0) AS is_overdue,
       d.days_overdue,
       (CASE WHEN d.days_overdue = 0 THEN 'not_due'
             WHEN d.days_overdue <= 30 THEN '1-30'
             WHEN d.days_overdue <= 60 THEN '31-60'
             WHEN d.days_overdue <= 90 THEN '61-90'
             ELSE '90+' END)::text AS aging_bucket,
       d.statutory_limit_days,
       COALESCE(d.payment_terms_days > d.statutory_limit_days, false) AS terms_exceed_statutory
  FROM (
    SELECT i.org_id,
           i.id AS invoice_id,
           i.number AS invoice_number,
           i.customer_id,
           c.name AS customer_name,
           i.status,
           (CASE WHEN i.status <> 'issued' THEN NULL
                 WHEN i.amount_paid_paise <= 0 THEN 'unpaid'
                 WHEN i.amount_paid_paise < i.total_paise THEN 'partially_paid'
                 ELSE 'paid' END)::text AS payment_status,
           i.issue_date,
           i.due_date,
           i.payment_terms_days,
           i.total_paise,
           i.amount_paid_paise,
           greatest(i.total_paise - i.amount_paid_paise, 0)::bigint AS amount_due_paise,
           greatest(i.amount_paid_paise - i.total_paise, 0)::bigint AS overpaid_paise,
           (CASE WHEN i.status = 'issued' AND i.total_paise > i.amount_paid_paise AND i.due_date < t.today
                 THEN t.today - i.due_date ELSE 0 END)::int AS days_overdue,
           (CASE WHEN o.udyam_category IN ('micro', 'small')
                 THEN (CASE WHEN c.has_written_agreement THEN 45 ELSE 15 END) END)::int AS statutory_limit_days
      FROM public.invoices i
      JOIN public.customers c ON c.org_id = i.org_id AND c.id = i.customer_id
      JOIN public.organizations o ON o.id = i.org_id
     CROSS JOIN LATERAL (SELECT @IST_TODAY@ AS today) t
     WHERE i.org_id = @TENANT@
  ) d
 WHERE d.org_id = @TENANT@;

CREATE OR REPLACE VIEW ai.v_customers WITH (security_barrier = true) AS
SELECT c.org_id,
       c.id AS customer_id,
       c.name,
       c.state_code,
       c.has_written_agreement,
       c.default_terms_days,
       COALESCE(a.invoices_count, 0)::int AS invoices_count,
       COALESCE(a.total_billed_paise, 0)::bigint AS total_billed_paise,
       COALESCE(a.total_paid_paise, 0)::bigint AS total_paid_paise,
       COALESCE(a.outstanding_paise, 0)::bigint AS outstanding_paise,
       COALESCE(a.paid_invoices_count, 0)::int AS paid_invoices_count,
       COALESCE(a.late_paid_invoices_count, 0)::int AS late_paid_invoices_count,
       (CASE WHEN COALESCE(a.paid_invoices_count, 0) = 0 THEN 0
             ELSE round(a.late_paid_invoices_count * 10000.0 / a.paid_invoices_count) END)::int AS late_payment_rate_bp,
       round(a.avg_days_to_pay, 2)::numeric(8, 2) AS avg_days_to_pay,
       c.notes
  FROM public.customers c
  LEFT JOIN (
    SELECT i.org_id, i.customer_id,
           count(*) AS invoices_count,
           sum(i.total_paise) AS total_billed_paise,
           sum(i.amount_paid_paise) AS total_paid_paise,
           sum(greatest(i.total_paise - i.amount_paid_paise, 0)) AS outstanding_paise,
           count(*) FILTER (WHERE i.total_paise > 0 AND i.amount_paid_paise >= i.total_paise) AS paid_invoices_count,
           count(*) FILTER (WHERE i.total_paise > 0 AND i.amount_paid_paise >= i.total_paise
                              AND p.last_paid_on > i.due_date) AS late_paid_invoices_count,
           avg(p.last_paid_on - i.issue_date)
               FILTER (WHERE i.total_paise > 0 AND i.amount_paid_paise >= i.total_paise) AS avg_days_to_pay
      FROM public.invoices i
      LEFT JOIN (
        SELECT org_id, invoice_id, max(paid_on) AS last_paid_on
          FROM public.payments
         WHERE status = 'captured' AND org_id = @TENANT@
         GROUP BY org_id, invoice_id
      ) p ON p.org_id = i.org_id AND p.invoice_id = i.id
     WHERE i.status = 'issued' AND i.org_id = @TENANT@
     GROUP BY i.org_id, i.customer_id
  ) a ON a.org_id = c.org_id AND a.customer_id = c.id
 WHERE c.org_id = @TENANT@;

CREATE OR REPLACE VIEW ai.v_payments WITH (security_barrier = true) AS
SELECT p.org_id, p.id AS payment_id, p.invoice_id, p.provider, p.amount_paise, p.status, p.method,
       p.captured_at, p.paid_on
  FROM public.payments p
 WHERE p.org_id = @TENANT@;

CREATE OR REPLACE VIEW ai.v_deals WITH (security_barrier = true) AS
SELECT d.org_id, d.id AS deal_id, d.customer_id, c.name AS customer_name, d.title, d.stage,
       d.amount_estimate_paise, d.created_at, d.stage_changed_at, d.closed_at
  FROM public.deals d
  JOIN public.customers c ON c.org_id = d.org_id AND c.id = d.customer_id
 WHERE d.org_id = @TENANT@;

CREATE OR REPLACE VIEW ai.v_collections_daily WITH (security_barrier = true) AS
SELECT m.org_id, m.day, m.billed_paise, m.collected_paise, m.invoices_issued, m.payments_count
  FROM public.mv_collections_daily m
 WHERE m.org_id = @TENANT@;

CREATE OR REPLACE VIEW ai.v_receivables_summary WITH (security_barrier = true) AS
SELECT o.id AS org_id,
       t.today AS as_of_date,
       COALESCE(a.outstanding_paise, 0)::bigint AS outstanding_paise,
       COALESCE(a.overdue_paise, 0)::bigint AS overdue_paise,
       COALESCE(a.billed_last_30d_paise, 0)::bigint AS billed_last_30d_paise,
       (CASE WHEN COALESCE(a.billed_last_30d_paise, 0) = 0 THEN NULL
             ELSE least(round(a.outstanding_paise::numeric / a.billed_last_30d_paise * 30, 2), 999999.99)
        END)::numeric(8, 2) AS dso_30d
  FROM public.organizations o
 CROSS JOIN LATERAL (SELECT @IST_TODAY@ AS today) t
  LEFT JOIN LATERAL (
    SELECT sum(greatest(i.total_paise - i.amount_paid_paise, 0)) AS outstanding_paise,
           sum(greatest(i.total_paise - i.amount_paid_paise, 0)) FILTER (WHERE i.due_date < t.today) AS overdue_paise,
           sum(i.total_paise) FILTER (WHERE i.issue_date > t.today - 30) AS billed_last_30d_paise
      FROM public.invoices i
     WHERE i.org_id = o.id AND i.status = 'issued'
  ) a ON true
 WHERE o.id = @TENANT@;

CREATE OR REPLACE VIEW ai.v_webhook_health WITH (security_barrier = true) AS
SELECT w.org_id, w.provider, w.event_type, w.processing_status,
       (count(*) FILTER (WHERE w.received_at >= now() - interval '7 days'))::int AS event_count_7d,
       max(w.received_at) AS last_seen_at,
       (array_agg(w.last_error ORDER BY w.received_at DESC) FILTER (WHERE w.last_error IS NOT NULL))[1] AS last_error
  FROM public.webhook_events w
 WHERE w.org_id = @TENANT@
 GROUP BY w.org_id, w.provider, w.event_type, w.processing_status;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ai_reader') THEN
    GRANT USAGE ON SCHEMA ai TO ai_reader;
    GRANT SELECT ON ALL TABLES IN SCHEMA ai TO ai_reader;
  END IF;
END $$;
"""

# Constants only; nothing user-supplied ever reaches this text.
AI_VIEWS_SQL = _AI_VIEWS_TEMPLATE.replace("@TENANT@", TENANT).replace("@IST_TODAY@", IST_TODAY)

DROP_AI_VIEWS_SQL = """
DROP VIEW IF EXISTS ai.v_webhook_health, ai.v_receivables_summary, ai.v_collections_daily, ai.v_deals,
                    ai.v_payments, ai.v_customers, ai.v_invoices;
"""

AI_VIEW_NAMES = (
    "v_invoices",
    "v_customers",
    "v_payments",
    "v_deals",
    "v_collections_daily",
    "v_receivables_summary",
    "v_webhook_health",
)


def statements(sql: str) -> list[str]:
    """Split a script into single statements (asyncpg runs one command per prepared statement). Statements in this
    module are separated by a blank line and never contain one."""
    return [part.strip() for part in sql.split("\n\n") if part.strip()]
