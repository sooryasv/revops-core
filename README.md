# RevOps Core

**A multi-tenant lead-to-cash web application for small Indian suppliers that sell on credit.** It carries a sale from a deal in
the pipeline, to a GST-correct invoice, to an online payment collected through Razorpay, to a ledger that stays correct when
payment webhooks are retried.

This is a **portfolio and learning project**, built to show engineering judgement (correctness, security, testing, delivery),
not a startup. It is Project 1 of two; Project 2 (a read-only AI agent) consumes the `contract/` package this repository exports.

## What it does

| Area | Highlights |
|---|---|
| Sales pipeline | Customers, deals (`lead → qualified → proposal → won`, or `lost` with a reason), a won deal becomes a draft invoice |
| GST invoicing | Integer-paise maths, rates 0/5/18/40 %, CGST+SGST vs IGST by place of supply, gap-free numbers per financial year (`INV/26-27/000001`), void without reuse, server-side preview in the editor |
| MSME rules | Udyam category and written-agreement flag drive the 15/45-day statutory limit warning |
| Payments | Public pay link, Razorpay Checkout, **signed and idempotent webhooks are the only thing that marks money received**, derived balances, quarantine for amount mismatches, manual payments |
| Reporting | Outstanding, overdue, aging buckets, DSO, collections trend, pipeline, audit log; one materialized view |
| Multi-tenancy | `org_id` on every table, composite foreign keys, one repository base class, a leak-test suite over every tenant route |
| Agent contract | Seven read-only `ai.*` views behind a fail-closed tenant setting, three database roles, deterministic seed, OpenAPI |

## Architecture

```mermaid
flowchart LR
  Browser[React SPA] -- "same-origin cookie (RS256 JWT)" --> API
  Payer[Customer browser] -- "/pay/token" --> API
  Razorpay[Razorpay] -- "signed webhook" --> API
  subgraph One deployable
    API[FastAPI: api -> services -> repositories] --> Static[Built SPA files]
  end
  API -- "revops_app (DML)" --> PG[(PostgreSQL)]
  Migrations[alembic, revops_owner] --> PG
  Agent[Project 2 agent] -- "ai_reader: SELECT on ai views only" --> PG
```

Layers are enforced by tests: `api → services → repositories → db`, a pure `domain`, and no SQL in services. More in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md); the reasoning behind each choice is in [docs/DECISIONS.md](docs/DECISIONS.md).

## Run it locally

You need either Docker, or PostgreSQL 16+ (17 is the target), Python 3.12+ with [uv](https://docs.astral.sh/uv/), and Node 22.

### Option A: Docker Compose (one command)

> Written but not run in the build sandbox (no Docker daemon there). Follow [docs/LOCAL_VERIFICATION.md](docs/LOCAL_VERIFICATION.md) and report anything that differs.

```bash
# Bash
cp .env.example .env            # all values are fake dev placeholders
docker compose up --build       # app on http://localhost:8000, demo data seeded
```

```powershell
# PowerShell
Copy-Item .env.example .env
docker compose up --build
```

### Option B: without Docker (this path was run during the build)

```bash
# 1. Database: create it and the three roles (needs a PostgreSQL superuser)
createdb revops
psql -d revops -v ON_ERROR_STOP=1 -v dbname=revops -v owner_password=owner-pw -v app_password=app-pw \
     -v ai_password=ai-pw -f contract/roles.sql

# 2. Backend
cd backend
export MIGRATION_DATABASE_URL=postgresql+asyncpg://revops_owner:owner-pw@localhost:5432/revops
export DATABASE_URL=postgresql+asyncpg://revops_app:app-pw@localhost:5432/revops
export SEED_DEMO_PASSWORD='choose-a-demo-password'
uv sync
uv run alembic upgrade head
uv run python -m app.cli seed-demo           # two demo organisations, 6 users, ~280 invoices
# 3. Frontend (build once; FastAPI serves it) ...
(cd ../frontend && npm ci && npm run build)
export STATIC_DIR="$PWD/../frontend/dist"
uv run uvicorn app.main:create_app --factory --port 8000     # http://localhost:8000
```

```powershell
# PowerShell equivalents for the environment variables and commands
$env:MIGRATION_DATABASE_URL = "postgresql+asyncpg://revops_owner:owner-pw@localhost:5432/revops"
$env:DATABASE_URL           = "postgresql+asyncpg://revops_app:app-pw@localhost:5432/revops"
$env:SEED_DEMO_PASSWORD     = "choose-a-demo-password"
$env:STATIC_DIR             = (Resolve-Path ..\frontend\dist).Path
uv run uvicorn app.main:create_app --factory --port 8000
```

For frontend development run `npm run dev` in `frontend/` (port 5173, proxies `/api` to port 8000) and add `http://localhost:5173`
to `ALLOWED_ORIGINS`. Seeded logins are `admin.a@example.com`, `finance.a@example.com`, `sales.a@example.com` (organisation A, "Kovai
Precision Components") and the same with `.b` (organisation B, "Tiruppur Knit Exports"); the password is your `SEED_DEMO_PASSWORD`.

### Tests

```bash
cd backend  && uv run pytest --cov=app           # needs a PostgreSQL superuser: set TEST_ADMIN_URL
cd frontend && npm test && npm run typecheck && npm run lint
cd e2e      && npm ci && npx playwright install chromium && npx playwright test   # needs the SPA built; set E2E_ADMIN_URL for a non-default PostgreSQL
```

## Razorpay test-mode setup

Real payments need **test-mode** keys; the default `PAYMENT_PROVIDER=fake` needs none and is what the demo and E2E use.

1. Create a Razorpay account and switch the dashboard to **Test Mode**. Generate a key pair (Account & Settings → API Keys).
2. In Account & Settings → Webhooks add `https://<your-public-host>/api/v1/webhooks/razorpay` (use a tunnel for local runs), pick a
   secret, and tick `payment.authorized`, `payment.captured`, `payment.failed`, `order.paid`.
3. Set `PAYMENT_PROVIDER=razorpay`, `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET` and restart.
4. Pay an invoice from its pay link with a Razorpay **test** method. **MUST-VERIFY:** take the current test card and UPI values from
   Razorpay's own "test card details" documentation; they are not reproduced here because they were not verified (UNVERIFIED U3).
5. The invoice turns paid only when the webhook arrives. Redeliver the same webhook from the dashboard: nothing changes.

## Three-minute demo script

1. **Sign in** as `finance.a@example.com`. The dashboard shows outstanding, overdue, DSO, aging bars, top overdue customers (30 s).
2. **Tenant isolation:** open `/customers`, search `Zephyr`: no result. Sign in as `admin.b@example.com` and the same search finds it (30 s).
3. **Lead to cash:** as `admin.a` create a customer, a deal, move it to *won*, press *Create invoice*, watch the GST preview change as
   you edit the quantity, save, *Issue*: the number `INV/26-27/00000N` appears (60 s).
4. **Pay:** *Payment link* → copy the link once → open it in a private window. Pay with the test method. The page says "confirming"
   until the signed webhook lands, then "paid". Replaying the webhook from the dashboard changes nothing (45 s).
5. **Audit:** `/audit` (admin) shows who did what, with before/after (15 s).

## Quality

| Gate | Result at hand-over (what was actually run) |
|---|---|
| Backend tests | 594 passed (unit, property, integration on real PostgreSQL, API, security, concurrency, contract) |
| Coverage | 94 % overall; domain 98 % (gate 95); services 98 % (gate 85) |
| Static checks | `ruff` and `ruff format` clean; `mypy --strict` clean (107 files); `tsc`, ESLint clean |
| Frontend | 47 Vitest tests; production build; typed client regenerated from OpenAPI with a drift check |
| End to end | 14 Playwright specs passed against the real stack (3 flows, role checks, forged webhook, axe scans on login, dashboard, invoice detail, pay page) |
| Dependency audits | `pip-audit` and `npm audit --omit=dev` report nothing |
| Critical tests proven able to fail | tenant leak, RBAC, signatures, webhook idempotency, IST boundary, drift check, E2E forged webhook (see `.build/PROGRESS.md`) |
| Not run here | Docker build, Trivy, gitleaks, GitHub Actions, real Razorpay, PostgreSQL 17 (see [docs/LOCAL_VERIFICATION.md](docs/LOCAL_VERIFICATION.md)) |

Money is an integer number of paise everywhere (a test fails on `float` in money paths). Every claim of "verified" in the build
notes ([.build/](.build/)) is labelled RAN, WROTE-NOT-RUN or ASSUMED.

## How this was built

Honest account: this repository was built with an AI coding agent (Claude) working under human direction. The human wrote the
scope and the specification (`00`–`07`), set the quality gates, reviewed each milestone, and is responsible for understanding and
defending the result. The agent wrote the code and tests milestone by milestone (M0–M10), proved the critical tests can fail by
breaking the code once, and logged every spec conflict and unverified fact. The study material
([docs/STUDY_GUIDE.md](docs/STUDY_GUIDE.md), [docs/INTERVIEW_PREP.md](docs/INTERVIEW_PREP.md)) exists so the human can rebuild the
critical parts (tenant guard, webhook handler, GST calculator) from memory.

## Limitations

- **Test mode only.** One Razorpay account for the whole deployment (no per-tenant merchant accounts); no refunds, no payouts.
- GST scope is deliberately small: no e-invoicing (IRN), no credit notes, no reverse charge, no cess, rates 0/5/18/40 % only.
- In-process rate limiting (resets on restart, not shared across replicas); offset pagination (deep pages get slower).
- The customer pickers in the deal and invoice forms list the first 100 active customers.
- Not yet verified against live services: see `.build/UNVERIFIED.md` (Razorpay CSP domains, test values, live API calls, Azure steps).

## Prior art and my own notes

_Placeholder for the human: what you looked at before building this (other invoicing and RevOps tools), what you copied in spirit, and
what you chose differently._

## Layout

```
backend/    FastAPI app (app/), Alembic migrations, tests
frontend/   React SPA (Vite, TanStack Query, react-hook-form + zod), generated API client
e2e/        Playwright specs and the stack launcher
contract/   Project 1 -> Project 2 package: roles, ai views, schema snapshot, seed, manifest, openapi
docs/       DECISIONS, ARCHITECTURE, INTERVIEW_PREP, STUDY_GUIDE, runbook, sql_drills, LOCAL_VERIFICATION
infra/      Docker init scripts, Azure deployment guide
.build/     Build plan, progress reports, verified/unverified facts, spec issues, backlog
```
