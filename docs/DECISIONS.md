# Decisions

One short section per decision: what was chosen, why, what it costs. Numbers refer to `03` section 14. Conflicts between the
specifications and how each was resolved are in `.build/SPEC_ISSUES.md`.

## 001 Single deployable
FastAPI serves `/api/v1/*` and the built React app from one container (`app/spa.py`, Dockerfile stage 2 copies `dist`).
*Why:* same origin means a plain cookie session, no CORS surface, and a strict Content-Security-Policy; one thing to deploy and
monitor. *Cost:* the frontend cannot scale or deploy independently; acceptable at this size.

## 002 Money as integer paise
Every stored and transmitted amount is a `bigint` number of paise; GST is computed per line with integer half-up division
(`domain/money.py`, `domain/gst.py`); the UI only formats (`lib/format.ts`) and parses typed rupees into paise.
*Why:* floats and decimals drift, and an invoice total must equal the sum of its parts to the paisa. A test scans money paths for
`float`. *Cost:* every boundary converts; Razorpay already speaks paise, which helps.

## 003 Tenancy by repository scoping, composite foreign keys and leak tests
Every tenant table carries `org_id`; child rows reference `(org_id, id)`, so a row cannot point at another tenant's parent even
with a bug. `TenantRepository` is the only way services touch tenant tables, always filtering by the caller's `org_id`;
`system_scope(reason=...)` is the audited escape hatch and an architecture test pins its call sites. A foreign id returns 404, not
403, so existence is not leaked. A leak suite exercises every tenant route as organisation B against organisation A's ids.
*Why not Postgres row-level security?* It would be a second mechanism to reason about and debug; the repository plus composite keys
plus tests gives the same protection at the layer the developers read. The `ai` views use a transaction-local setting that fails
closed (decision 007). *Cost:* a new query that bypasses the repository is possible; the architecture test and the leak-suite
route-coverage test exist to catch that.

## 004 The webhook is the source of truth; derive balances, never increment
The browser callback only verifies a signature and says "pending". A signed webhook, deduplicated by event id
(`INSERT ... ON CONFLICT`), under a row lock on the invoice, creates or advances a payment; `amount_paid` is the **sum of captured
payments**, recomputed, never `+=`. Amount or currency mismatches are quarantined (stored, audited, not applied). Payment status
only moves forward (`merge_payment_status`). *Why:* provider retries, duplicates, reordering and concurrent deliveries are normal.
*Cost:* the payer waits for the webhook (the pay page polls for up to 60 s).

## 005 Razorpay chosen; Paddle excluded
This product collects money for goods sold between Indian businesses; Razorpay is an Indian payment gateway with INR and UPI. Paddle
is a merchant of record for software sales and does not fit. See PRD decision D3 in `01`.

## 006 Authentication: RS256 cookie JWT plus JWKS, Origin check, no refresh-token table
A 60-minute session JWT in an HttpOnly, SameSite=Lax cookie (Secure in production), reissued when under 30 minutes remain, with a
12-hour absolute limit; `token_version` on the user makes logout-everywhere and deactivation immediate. RS256 (asymmetric) lets the
future agent service verify tokens from `/.well-known/jwks.json` without being able to mint them. Unsafe requests that carry an
`Origin` must match the allowlist (CSRF defence next to SameSite). **The spec said EdDSA in `03` and RS256 in `07`; `07` wins**
(SPEC_ISSUES S1). Login is rate limited per IP and per email. *Cost:* in-memory limits (decision 009).

## 007 Reporting as views, plus one materialized view; the `ai` contract
Reports are SQL, not a pipeline: plain queries for live numbers and one materialized view (`mv_collections_daily`, refreshed by a
`SECURITY DEFINER` function the app role may call). Seven `ai.v_*` views are the only thing the agent role can read; each is
`security_barrier` and filters `org_id = nullif(current_setting('app.current_org', true), '')::uuid`, so an unset setting returns
no rows. Business dates are IST. *Cost:* the materialized view is stale until refreshed (the dashboard says so).

## 008 Offset pagination
`limit`/`offset` with a total. Simple, bookmarkable, adequate for tens of thousands of rows per tenant. Deep pages cost more and
rows can shift between pages; keyset pagination is in the backlog if volume demands it.

## 009 In-process rate limits
The `limits` library with an in-memory moving window behind a small wrapper (`core/rate_limit.py`): login (10/min per IP, 5/min per
email) and the public pay endpoints (30/min per IP). *Limitation:* limits reset on restart and are per replica; with more than one
replica move them to Redis or the platform edge. Documented in the runbook.

## 010 Derived payment status and overdue
`payment_status` (unpaid, partially paid, paid) and `is_overdue` are computed from totals, captured payments and the IST business
date, not stored, so they cannot disagree with the ledger. Overdue means issued, balance due, and `today_ist > due_date`; due today
is not overdue (proved at the 23:00 and 00:00 IST boundary).

## 011 GST scope simplifications
Rates 0, 5, 18, 40 % (data, easy to change); intra-state is CGST+SGST, inter-state IGST, decided by supplier state versus place of
supply; per-line half-up rounding; invoice numbers gap-free per organisation per financial year (a counter row locked on issue);
voiding keeps the number. Not covered: e-invoicing, credit notes, reverse charge, cess, composition scheme. The GST state-code
list is from a specialist page (UNVERIFIED U7).

## 012 Test mode only; no PCI scope
Card and UPI details only ever go to Razorpay's hosted Checkout in the payer's browser; our servers see an order id, a payment id and
a signature. Production refuses the fake provider; Razorpay keys are expected in test mode for this project. Limitation: one
merchant account for the deployment.

## 013 Optimistic concurrency on draft invoices
`PUT /invoices/{id}` carries the `version` the editor loaded; a stale version is a 409 and the draft is locked `FOR UPDATE` while
replaced. Issue takes the sequence lock, so concurrent issues get distinct, consecutive numbers (tested concurrently).

## 014 Frontend is a convenience layer, not an authority
Role-based hiding and the deal-stage menu mirror server rules to save clicks; the API enforces every one (RBAC route-table test,
403 matrix test). GST totals in the editor come from `POST /invoices/preview`. The typed client is generated from OpenAPI and a CI
job fails on drift.

## 015 Dependency versions: latest stable at least 30 days old, with a security exception
Implemented with `uv exclude-newer` and `npm --before`; security fixes for advisories found by `pip-audit`/`npm audit` may be newer
(SPEC_ISSUES S23).
