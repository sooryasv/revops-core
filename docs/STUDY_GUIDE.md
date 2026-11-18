# Study guide

How to learn this codebase well enough to explain and rebuild it. Paths are relative to `backend/app/` unless they start with `frontend/`.
Tiers (from the specification): **A** = rebuild from memory, **B** = explain with the code open (about 10 minutes), **C** = know it exists and why.

For every module: read the functions in the order given, trace the data flow with a pen, answer the five questions out loud without
looking, then do the two break-it exercises (change the code, predict which test fails, run it, see if you were right).

## 1. Domain (tier A): the pure rules

**Purpose.** Business rules with no I/O: money, GST, GSTIN, fiscal year, invoice numbers, statutory limits, state machines.
**Read in order.** `domain/money.py` (`require_int`, `round_half_up_div`) → `domain/gst.py` (`compute_line`, `compute_invoice`) →
`domain/invoice_rules.py` (`payment_status`, `is_overdue`, `aging_bucket`) → `domain/fiscal_year.py` (`fy_label_for`) → `domain/statutory.py`.
**Data flow.** Services call these with plain ints and dates and persist the results; nothing here touches the database or the clock.
**Explain-back.**
1. Why is every amount an integer number of paise, and where is the only place rounding happens?
2. How does `compute_line` choose CGST+SGST versus IGST, and how is an odd paisa split between CGST and SGST?
3. Why is "due today" not overdue, and which clock supplies "today"?
4. What are the first and last days of FY 2026-27, and what invoice number follows `INV/26-27/000009`?
5. Why do the property tests assert `total == subtotal + taxes` for random input?
**Break it.** (a) Change `round_half_up_div` to round half down: which unit and property tests fail? (b) Make `is_overdue` use `>=`: which IST-boundary tests fail?
**Rebuild drill: the GST calculator.** From a blank file: `LineInput(quantity, unit_price_paise, gst_rate_bp)`, per-line `taxable = qty * price`,
`tax = round_half_up_div(taxable * rate_bp, 10000)`, split into CGST/SGST (intra) or IGST (inter), totals as sums. Then write three Hypothesis properties.

## 2. Tenancy (tier A)

**Purpose.** No request can read or write another organisation's rows.
**Read in order.** `db/models/mixins.py` (`TenantMixin`) → `repositories/base.py` (`TenantRepository`, `system_scope`) →
`repositories/customers.py` (a small subclass) → `migrations/versions/0002_*.py` (composite foreign keys) → `tests/security/test_leak_suite.py`.
**Data flow.** Router → service gets `AuthContext(org_id, role)` from the verified token → service builds `Repository(session, ctx.org_id)` →
every `select`/`get`/`add` is scoped by `org_id`; a foreign id is "not found" (404).
**Explain-back.**
1. Where is `org_id` applied, and why is it impossible to forget in a service?
2. What do composite foreign keys add on top of repository scoping?
3. Why 404 and not 403 for another tenant's id?
4. What is `system_scope`, who may call it, and which test stops a new caller sneaking in?
5. How do the `ai` views stay tenant-safe without a repository?
**Break it.** (a) Remove the `org_id` filter in `TenantRepository.select`: which leak tests fail (they should be many)? (b) Drop one composite FK in a migration copy: which integration test notices?
**Rebuild drill: the tenant guard.** Write a generic `TenantRepository[T]` with `select()`, `find()`, `get()` (raises NotFound), `add()`, `page()`; then a leak test that creates data in two orgs and asserts every route returns 404 for the other org's ids.

## 3. Authentication and authorisation (tier B)

**Purpose.** Who is calling, and may they?
**Read in order.** `core/passwords.py` → `core/jwt_service.py` (`issue`, `decode`, `jwks`) → `services/auth.py` (`login`, `verify_session`, `logout`) →
`core/permissions.py` (`Permission`, `ROLE_PERMISSIONS`, `requires`, `public`) → `core/middleware.py` (`OriginCheckMiddleware`) → `tests/security/test_rbac_route_table.py`.
**Data flow.** Login verifies the password (same cost and message for every failure), issues an RS256 JWT in an HttpOnly cookie; every request
decodes it, checks `token_version` against the user row, and builds `AuthContext`; routes declare `requires(Permission.X)` or `public()`.
**Explain-back.** 1. Why RS256 plus JWKS? 2. What stops CSRF here (list both layers)? 3. How does logout-everywhere work without a token table?
4. What does the route-table test guarantee, and what would a new unprotected route do to it? 5. Why do login failures look identical?
**Break it.** (a) Remove `requires` from one route: which test catches it? (b) Accept `alg: none` in `decode`: which test fails?

## 4. Invoicing (tier B)

**Purpose.** Draft, edit, issue, void; gap-free numbering; pay links.
**Read in order.** `services/invoices.py` (`create_draft`, `replace_draft`, `issue`, `void`, `create_pay_link`) → `repositories/invoices.py`
(`next_number_value`) → `core/paytoken.py` → `tests/concurrency/test_invoice_numbering.py`.
**Data flow.** `issue` locks the invoice and the per-org/per-FY sequence row, assigns the number, stamps IST issue and due dates; one transaction.
**Explain-back.** 1. Why lock the sequence row instead of using a database sequence? 2. What does `version` protect on a draft? 3. What happens to the number of a voided invoice?
4. Why store only the hash of the pay token? 5. What does `terms_exceed_statutory` depend on?
**Break it.** (a) Remove the sequence lock: does the concurrency test fail every time? (b) Let `void` ignore captured payments: which test fails?

## 5. Payments and webhooks (tier A)

**Purpose.** Money is received only when a verified, deduplicated webhook says so.
**Read in order.** `payments/signatures.py` → `payments/events.py` (`ParsedEvent`) → `services/webhooks.py` (`handle`, `_apply`) →
`repositories/webhooks.py` (`claim_event`) → `domain/payment_rules.py` → `services/public_pay.py` (`create_order`, `confirm`) → `tests/api/test_webhooks.py`.
**Data flow.** Raw body → verify HMAC → parse → claim event id (`ON CONFLICT DO NOTHING`) → find our order → lock invoice → check amount/currency →
upsert payment (monotonic status) → recompute `amount_paid = SUM(captured)` → audit → mark event processed → 200.
**Explain-back.** 1. Why sign the raw bytes, not the parsed JSON? 2. What does the unique event id buy you under concurrent delivery? 3. Why derive instead of increment?
4. What is quarantined and why still answer 200? 5. What does `/confirm` do and why does it never mark paid?
**Break it.** (a) Make the claim re-process any status: six tests fail (this was the B10 proof). (b) Compare signatures with `==`: no test fails, so why is it still wrong, and what would you add?
**Rebuild drill: the webhook handler.** Write `handle(raw_body, signature, event_id)` returning `processed | duplicate | quarantined | ignored`, then the tests: duplicate delivery, two concurrent deliveries, amount mismatch, bad signature, out-of-order events.

## 6. Reporting and the agent contract (tier B)

**Purpose.** Numbers the business trusts, and a safe read surface for the agent.
**Read in order.** `repositories/reports.py` (the SQL) → `services/reports.py` (`dso`, `summary`) → `db/contract_sql.py` (views, refresh function) →
`seed/demo.py` → `tests/reporting/truth.py` (independent ground truth) → `tests/reporting/test_ai_views.py`.
**Data flow.** SQL aggregates → service shapes → API; the `ai` views read the same tables under `app.current_org`.
**Explain-back.** 1. Why compute test expectations in plain Python from raw rows? 2. What does `security_barrier` do? 3. Why `nullif(current_setting(..., true), '')`?
4. Why is the first materialized view refresh not `CONCURRENTLY`? 5. How is "today" computed in IST in SQL, and how did the test prove it?
**Break it.** (a) Replace the IST date with `CURRENT_DATE`: six reporting tests fail. (b) Remove the tenant filter from one `ai` view: seven fail.

## 7. Frontend (tier B)

**Purpose.** A thin, accessible client over the typed API.
**Read in order.** `frontend/src/api/client.ts` → `frontend/src/auth/AuthContext.tsx` → `frontend/src/lib/format.ts` →
`frontend/src/pages/InvoiceEditorPage.tsx` (server-side preview) → `frontend/src/pages/PayPage.tsx` → `frontend/src/test/app.test.tsx`.
**Data flow.** TanStack Query caches server state; URL holds filters; forms validate with zod then the server validates again; 401 anywhere clears the session.
**Explain-back.** 1. Where is GST computed in the UI? (nowhere: why?) 2. Why no token in storage? 3. When is the Razorpay script loaded?
4. What does the drift check compare? 5. Why are role checks in the UI only a convenience?
**Break it.** (a) Load Razorpay in `main.tsx`: which test fails? (b) Edit `schema.d.ts` by hand: what does `npm run api:check` do?

## 8. CI, delivery and operations (tier C)

**Purpose.** Prove it on every push and ship it safely.
**Read in order.** `.github/workflows/ci.yml` → `Dockerfile` → `infra/docker/entrypoint.sh` → `.github/workflows/deploy.yml` → `docs/runbook.md`.
**Explain-back.** 1. Which gates fail the build? 2. Why migrate with the owner role before rollout? 3. Why is the container non-root? 4. What does `/readyz` check?
5. Which parts have never been run, and how would you run them?
**Break it.** (a) Lower a coverage gate then raise it above reality: which job fails? (b) Remove `STATIC_DIR` from the image: what does `/` return?

## Final rebuild drills (do these on separate days)

1. **GST calculator** from a blank file, with three Hypothesis properties (45 minutes).
2. **Tenant guard** and a two-organisation leak test for one route (60 minutes).
3. **Webhook handler** with duplicate, concurrent and mismatch tests (90 minutes).
