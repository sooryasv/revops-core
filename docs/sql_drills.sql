-- sql_drills.sql: 15 practice queries against this schema (PostgreSQL 16/17).
-- Load the demo data first (python -m app.cli seed-demo), then run with a superuser or owner connection:
--     psql -d revops -f docs/sql_drills.sql
-- All money is integer paise. Every query is tenant-scoped with :'org' (set below), as the application's repositories do.
-- Try to write each query yourself from its question before reading the answer.

SELECT id AS org FROM organizations WHERE name LIKE 'Kovai%' LIMIT 1 \gset
\set today 'timezone(''Asia/Kolkata'', now())::date'

-- Q1 (join, group by): How much does each customer still owe on issued invoices? Biggest first.
SELECT c.name, count(*) AS open_invoices, sum(i.total_paise - i.amount_paid_paise) AS due_paise
FROM invoices i
JOIN customers c ON c.org_id = i.org_id AND c.id = i.customer_id
WHERE i.org_id = :'org' AND i.status = 'issued' AND i.total_paise > i.amount_paid_paise
GROUP BY c.id, c.name
ORDER BY due_paise DESC
LIMIT 10;

-- Q2 (date maths): Which invoices are overdue today (IST), and by how many days? Due today is NOT overdue.
SELECT i.number, i.due_date, (:today - i.due_date) AS days_overdue, i.total_paise - i.amount_paid_paise AS due_paise
FROM invoices i
WHERE i.org_id = :'org' AND i.status = 'issued' AND i.total_paise > i.amount_paid_paise AND i.due_date < :today
ORDER BY days_overdue DESC, i.number
LIMIT 15;

-- Q3 (CASE, aggregation): Receivables aging buckets: not due, 1-30, 31-60, 61-90, 90+ days overdue.
SELECT CASE
         WHEN :today <= i.due_date THEN 'not_due'
         WHEN :today - i.due_date <= 30 THEN '1-30'
         WHEN :today - i.due_date <= 60 THEN '31-60'
         WHEN :today - i.due_date <= 90 THEN '61-90'
         ELSE '90+'
       END AS bucket,
       count(*) AS invoices, sum(i.total_paise - i.amount_paid_paise) AS due_paise
FROM invoices i
WHERE i.org_id = :'org' AND i.status = 'issued' AND i.total_paise > i.amount_paid_paise
GROUP BY 1
ORDER BY min(:today - i.due_date);

-- Q4 (window, running total): Collections per day over the last 30 days, with a running total.
WITH daily AS (
  SELECT p.paid_on AS day, sum(p.amount_paise) AS collected
  FROM payments p
  WHERE p.org_id = :'org' AND p.status = 'captured' AND p.paid_on > :today - 30
  GROUP BY p.paid_on
)
SELECT day, collected, sum(collected) OVER (ORDER BY day) AS running_total
FROM daily
ORDER BY day;

-- Q5 (LAG): Billed per month and the change versus the previous month, in percent (one decimal).
WITH monthly AS (
  SELECT date_trunc('month', i.issue_date)::date AS month, sum(i.total_paise) AS billed
  FROM invoices i
  WHERE i.org_id = :'org' AND i.status = 'issued'
  GROUP BY 1
)
SELECT month, billed,
       lag(billed) OVER (ORDER BY month) AS previous,
       round(100.0 * (billed - lag(billed) OVER (ORDER BY month)) / NULLIF(lag(billed) OVER (ORDER BY month), 0), 1) AS change_pct
FROM monthly
ORDER BY month;

-- Q6 (ROW_NUMBER dedupe): The provider redelivers events. Show the latest delivery row per provider_event_id, for events
-- that arrived more than once. (The application stores one row per event id; this drill uses attempts as the stand-in.)
SELECT provider_event_id, event_type, processing_status, attempts, received_at
FROM (
  SELECT w.*, row_number() OVER (PARTITION BY w.provider_event_id ORDER BY w.attempts DESC, w.received_at DESC) AS rn
  FROM webhook_events w
  WHERE w.org_id = :'org'
) ranked
WHERE rn = 1 AND attempts > 1
ORDER BY attempts DESC, received_at DESC
LIMIT 10;

-- Q7 (ratio KPI): DSO for the last 30 days = outstanding / billed in the period * 30. Null when nothing was billed.
SELECT round(
         (SELECT sum(total_paise - amount_paid_paise) FROM invoices WHERE org_id = :'org' AND status = 'issued' AND total_paise > amount_paid_paise)::numeric
         * 30 / NULLIF((SELECT sum(total_paise) FROM invoices WHERE org_id = :'org' AND status = 'issued' AND issue_date > :today - 30), 0),
         2) AS dso_30d;

-- Q8 (DENSE_RANK): Rank customers by total billed; ties share a rank. Top 5 ranks only.
SELECT rank_no, name, billed
FROM (
  SELECT c.name, sum(i.total_paise) AS billed, dense_rank() OVER (ORDER BY sum(i.total_paise) DESC) AS rank_no
  FROM invoices i JOIN customers c ON c.org_id = i.org_id AND c.id = i.customer_id
  WHERE i.org_id = :'org' AND i.status = 'issued'
  GROUP BY c.id, c.name
) t
WHERE rank_no <= 5
ORDER BY rank_no, name;

-- Q9 (average of a difference): On average, how many days after issue does each customer pay? Slowest payers first.
SELECT c.name, round(avg(p.paid_on - i.issue_date), 1) AS avg_days_to_pay, count(*) AS payments
FROM payments p
JOIN invoices i ON i.org_id = p.org_id AND i.id = p.invoice_id
JOIN customers c ON c.org_id = i.org_id AND c.id = i.customer_id
WHERE p.org_id = :'org' AND p.status = 'captured'
GROUP BY c.id, c.name
HAVING count(*) >= 3
ORDER BY avg_days_to_pay DESC
LIMIT 10;

-- Q10 (integrity check): The ledger invariant: amount_paid_paise must equal the sum of captured payments. Expect ZERO rows.
SELECT i.number, i.amount_paid_paise, coalesce(sum(p.amount_paise) FILTER (WHERE p.status = 'captured'), 0) AS captured
FROM invoices i
LEFT JOIN payments p ON p.org_id = i.org_id AND p.invoice_id = i.id
WHERE i.org_id = :'org'
GROUP BY i.id, i.number, i.amount_paid_paise
HAVING i.amount_paid_paise <> coalesce(sum(p.amount_paise) FILTER (WHERE p.status = 'captured'), 0);

-- Q11 (LAG for gaps): Invoice numbers must be consecutive per financial year. Find any gap (expect zero rows).
WITH numbered AS (
  SELECT split_part(number, '/', 2) AS fy, split_part(number, '/', 3)::int AS serial FROM invoices
  WHERE org_id = :'org' AND number IS NOT NULL
), with_previous AS (
  SELECT fy, serial, coalesce(lag(serial) OVER (PARTITION BY fy ORDER BY serial), 0) AS previous FROM numbered
)
SELECT fy, serial, previous FROM with_previous WHERE serial - previous <> 1 ORDER BY fy, serial;

-- Q12 (FILTER): The deal funnel: count and value by stage, and the share of closed deals that were won.
SELECT count(*) FILTER (WHERE stage = 'lead') AS leads,
       count(*) FILTER (WHERE stage = 'qualified') AS qualified,
       count(*) FILTER (WHERE stage = 'proposal') AS proposals,
       count(*) FILTER (WHERE stage = 'won') AS won,
       count(*) FILTER (WHERE stage = 'lost') AS lost,
       sum(amount_estimate_paise) FILTER (WHERE stage IN ('lead', 'qualified', 'proposal')) AS open_value_paise,
       round(100.0 * count(*) FILTER (WHERE stage = 'won') / NULLIF(count(*) FILTER (WHERE stage IN ('won', 'lost')), 0), 1) AS win_rate_pct
FROM deals
WHERE org_id = :'org';

-- Q13 (anti-join): Which active customers have had no invoice issued in the last 90 days?
SELECT c.name, max(i.issue_date) AS last_invoice
FROM customers c
LEFT JOIN invoices i ON i.org_id = c.org_id AND i.customer_id = c.id AND i.status = 'issued'
WHERE c.org_id = :'org' AND c.is_active
GROUP BY c.id, c.name
HAVING max(i.issue_date) IS NULL OR max(i.issue_date) < :today - 90
ORDER BY last_invoice NULLS FIRST
LIMIT 10;

-- Q14 (cohorts): Group customers by the month of their first issued invoice and show how many each month brought in.
WITH first_invoice AS (
  SELECT customer_id, min(issue_date) AS first_date FROM invoices
  WHERE org_id = :'org' AND status = 'issued' GROUP BY customer_id
)
SELECT date_trunc('month', first_date)::date AS cohort_month, count(*) AS new_customers
FROM first_invoice
GROUP BY 1
ORDER BY 1;

-- Q15 (percentile): For fully paid invoices, how late was payment? Median and 90th percentile of (last payment date - due date).
WITH paid AS (
  SELECT i.id, max(p.paid_on) - i.due_date AS days_late
  FROM invoices i JOIN payments p ON p.org_id = i.org_id AND p.invoice_id = i.id AND p.status = 'captured'
  WHERE i.org_id = :'org' AND i.status = 'issued' AND i.amount_paid_paise >= i.total_paise
  GROUP BY i.id, i.due_date
)
SELECT count(*) AS paid_invoices,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY days_late) AS median_days_late,
       percentile_cont(0.9) WITHIN GROUP (ORDER BY days_late) AS p90_days_late
FROM paid;
