# Local verification: the human's checklist

The build ran in a sandbox without Docker, without a browser against real Razorpay, and with PostgreSQL 16. This file lists what
was run there, what was not, and the checklist to close the gaps. Record the result of each step (and anything that differs
from the specification) in `.build/SPEC_ISSUES.md`.

## What the build actually ran (RAN)

Backend `ruff`, `ruff format --check`, `mypy --strict`, 594 pytest tests on a real PostgreSQL 16 with coverage gates; frontend `tsc`,
ESLint, 47 Vitest tests, production build, OpenAPI drift check; `npm audit` and `pip-audit`; 14 Playwright specs in Chromium against
FastAPI serving the built SPA with the fake provider; the 15 queries in `docs/sql_drills.sql`; a dump and restore drill; `export-contract`
and the contract load test.

## Not run (WROTE-NOT-RUN) and why

| Item | Why | Closed by step |
|---|---|---|
| `docker compose up --build`, the Dockerfile, `HEALTHCHECK` | no Docker daemon in the sandbox | 1 |
| Real Razorpay Checkout, webhooks, `RazorpayProvider` HTTP calls | no keys, the Razorpay site is not reachable from the sandbox shell | 3 to 5 |
| CSP domains Razorpay Checkout needs (U2) | docs do not list them | 3 (watch the browser console) |
| GitHub Actions, gitleaks, Trivy | not runnable here | push to GitHub, read the first CI run |
| PostgreSQL 17 (target version) | sandbox has 16 | 1 (compose uses `pgvector/pgvector:pg17`) |
| Azure deployment | needs your subscription | `infra/azure/README.md` |
| Lighthouse scores | no Lighthouse in the sandbox (axe scans did run) | 7 |

## Checklist

1. `docker compose up --build`; open http://localhost:8000; log in as each seeded role (`admin.a`, `finance.a`, `sales.a` at `@example.com`; password is `SEED_DEMO_PASSWORD`); confirm role differences (sales has no Users or Audit; finance has no Users).
2. Run the full backend and frontend suites locally and Playwright (see README "Tests"); note failures.
3. Create Razorpay **test** keys and a webhook (events `payment.authorized`, `payment.captured`, `payment.failed`, `order.paid`); expose the local app (tunnel) or use the deployed URL; set `PAYMENT_PROVIDER=razorpay`. Open the pay page with DevTools open and note any Content-Security-Policy violations (`app/core/security_headers.py`).
4. Pay one invoice with a test method; confirm the webhook arrives, the invoice turns paid **only after the webhook**, and the audit trail is correct. In the Razorpay dashboard **redeliver the same webhook** and confirm no double effect (the response is `duplicate`).
5. Open a second checkout for the same invoice (second browser) and confirm the ledger is not corrupted (`amount_paid` equals the sum of captured payments; `docs/sql_drills.sql` Q10 returns no rows).
6. Restore drill on the real database: dump, drop, restore, run `/readyz`, log the result in `docs/runbook.md`.
7. Lighthouse accessibility at least 90 on login, dashboard, invoice detail and the pay page.
8. Run `python -m app.cli export-contract`; load the package into an empty PostgreSQL and run the load test from `contract/README.md`.
9. Record Razorpay behaviours that differ from the specification (field paths, header names, empty `currency`, minimum amount, retry policy) in `.build/SPEC_ISSUES.md` and tell the reviewer. Items still open: `.build/UNVERIFIED.md` U1 to U8.

## Real Razorpay test-mode notes

Webhook handler expectations: header `X-Razorpay-Signature` (HMAC-SHA256 of the raw body) and `X-Razorpay-Event-Id`; a missing event id is a 422 (SPEC_ISSUES S15). Orders are created with `receipt = <invoice number>-<n>`; confirm Razorpay accepts the `/` character (assumption in BUILD_PLAN section 7).
