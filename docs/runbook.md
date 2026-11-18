# Runbook

Operational notes for whoever runs this. Items marked **RAN** were executed in the build sandbox (PostgreSQL 16, no Docker);
everything about Azure and Docker is **WROTE-NOT-RUN**.

## Processes and configuration

| Setting | Purpose |
|---|---|
| `DATABASE_URL` | runtime connection, role `revops_app` (DML only; `audit_logs` is insert and select) |
| `MIGRATION_DATABASE_URL` | role `revops_owner`: Alembic, `seed-demo`, `export-contract`. Keep it out of the runtime environment where you can |
| `ENV` | `dev`, `test`, `production`. Production refuses the fake payment provider, requires `JWT_PRIVATE_KEY_PEM`, sets Secure cookies and HSTS, hides `/docs` |
| `JWT_PRIVATE_KEY_PEM`, `JWT_KEY_ID` | RS256 signing key. Without a key, dev generates an ephemeral one and sessions end on restart |
| `ALLOWED_ORIGINS`, `PUBLIC_BASE_URL` | Origin check allowlist; base of pay links |
| `PAYMENT_PROVIDER`, `RAZORPAY_*` | `fake` or `razorpay` (test keys) |
| `STATIC_DIR` | location of the built SPA (`/app/app/static` in the image) |
| `RUN_MIGRATIONS`, `SEED_DEMO` | entrypoint conveniences for single-replica demos (never in production) |

Create the first organisation and admin in a fresh environment: `ADMIN_PASSWORD=... python -m app.cli create-org --name "..." --state-code 33 --admin-email ... --admin-name ...`.

## Health and readiness

- `GET /healthz` is liveness only. `GET /readyz` is readiness: the database answers and Alembic is at head. `{"migrations":"behind"}` means run migrations.
- Every log line is JSON with a `request_id`; the same id is returned in the `X-Request-ID` header and in problem responses (`request_id`), so a user-reported error can be found by id. Secrets, cookies, passwords and tokens are never logged (tests assert this).

## Deploying

1. Build the image; run `alembic upgrade head` as the **owner** role **before** the new revision takes traffic (the deploy workflow does this).


## Backups and the restore drill

Managed PostgreSQL takes automatic backups; also take a logical dump before risky changes:

```bash
pg_dump -Fc -d revops -f revops.dump
createdb -O revops_owner revops_restore
pg_restore -d revops_restore --no-owner --role=revops_owner revops.dump
```

**Drill log (RAN, 2026-11-04, sandbox PostgreSQL 16.15):** dumped the seeded E2E database (174 KB custom format), created an empty
database owned by `revops_owner`, restored it (exit 0), and compared: 286 invoices and 178 payments restored, `alembic_version = 0003`.
Started the app against the restored copy with the runtime role: `GET /readyz` returned `{"status":"ready","checks":{"database":"ok","migrations":"ok"}}`.
Repeat this on the real managed instance before relying on it; record the date and result here. The roles are cluster-level: on a
new server run `contract/roles.sql` first.

## Reporting refresh

`mv_collections_daily` is refreshed by `POST /api/v1/admin/reporting/refresh` (admin) or `python -m app.cli refresh-reporting`; both call a
`SECURITY DEFINER` function, so the runtime role needs no ownership. The first fill is a plain refresh, later ones are concurrent. Schedule it
(Azure: a Container Apps job or any cron) at least daily; the dashboard figures that read live tables do not depend on it.

## Key rotation (JWT)

1. Generate a new key: `openssl genrsa -out jwt-new.pem 2048`; pick a new `JWT_KEY_ID`.
2. Set `JWT_PRIVATE_KEY_PEM` to the new key, `JWT_PREVIOUS_PUBLIC_KEY_PEM` and `JWT_PREVIOUS_KEY_ID` to the old public key and id, redeploy.
   Both keys are in `/.well-known/jwks.json`, so sessions signed by the old key keep working until they expire (at most 60 minutes).
3. After an hour remove the previous-key settings. Razorpay secret rotation: change it in the dashboard and in the app setting together; deliveries between the two fail signature checks and are retried by Razorpay.

## Incidents

| Symptom | Likely cause | Action |
|---|---|---|
| Webhooks answer 4xx | signature secret mismatch or missing event id header | compare `RAZORPAY_WEBHOOK_SECRET` with the dashboard; failed deliveries are retried by Razorpay, then redeliver from the dashboard |
| Invoice not marked paid after paying | webhook not delivered or quarantined | admin dashboard shows "Webhook problems (7 days)"; look at `webhook_events.processing_status` (`quarantined`, `failed`) and the audit log for `payment.quarantined` |
| Everyone logged out | process restarted without `JWT_PRIVATE_KEY_PEM` (ephemeral key) | set the key |
| 429 on login | in-memory rate limit (10/min per IP, 5/min per email) | wait a minute; limits are per replica |
| `/readyz` says `behind` | migrations not run | run `alembic upgrade head` with the owner URL |
| Cold start slow | scale-to-zero replica | set minimum replicas to 1 (cost trade-off, see `infra/azure/README.md`) |

## Known limitations

In-process rate limits (per replica, reset on restart); single Razorpay account; offset pagination; materialized view staleness; no email notifications.
