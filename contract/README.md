# Contract package (version in CONTRACT_VERSION)

Everything Project 2 needs from Project 1. Treat this folder as read-only; a needed change is logged in P2's
`.build/SPEC_ISSUES.md` and decided by the human.

| File | What it is |
|---|---|
| `CONTRACT_VERSION` | semantic version of the contract (02) |
| `roles.sql` | creates `revops_owner`, `revops_app`, `ai_reader`, schemas and default privileges (run once, as superuser) |
| `schema_snapshot.sql` | `pg_dump --schema-only` of schemas `public` and `ai` (tables, the materialized view, the refresh function, the ai views, grants) |
| `ai_views.sql` | the exact DDL of the seven `ai` views (already contained in the snapshot; use it to re-apply or review them) |
| `seed_demo.sql` | data-only dump (`--column-inserts`) of the deterministic demo dataset. Users cannot log in (no password hashes) |
| `seed_manifest.json` | expected counts per table per organisation, overdue totals, visible webhook events (computed by SQL) |
| `openapi.json` | the P1 REST API |

## Load into a fresh PostgreSQL (16 or 17)

```bash
createdb revops
psql -v ON_ERROR_STOP=1 -d revops -v dbname=revops -v owner_password=... -v app_password=... -v ai_password=... \
     -f roles.sql                                  # as a superuser
PGPASSWORD=<owner_password> psql -v ON_ERROR_STOP=1 -h <host> -U revops_owner -d revops -f schema_snapshot.sql
PGPASSWORD=<owner_password> psql -v ON_ERROR_STOP=1 -h <host> -U revops_owner -d revops -f seed_demo.sql
PGPASSWORD=<owner_password> psql -h <host> -U revops_owner -d revops -c "REFRESH MATERIALIZED VIEW public.mv_collections_daily"
```

The snapshot creates the materialized view empty (`WITH NO DATA`), and `REFRESH ... CONCURRENTLY` (what
`public.refresh_collections_mv()` runs) refuses an unpopulated view, so the first fill is a plain refresh by the owner.
After that, use `SELECT public.refresh_collections_mv()`.

## Read as the agent role

```sql
-- as ai_reader, inside a transaction:
SELECT set_config('app.current_org', '<org uuid from seed_manifest.json>', true);
SELECT * FROM ai.v_receivables_summary;
```

With the setting unset every `ai` view returns zero rows. `ai_reader` has no access to the `public` schema.

## Notes

* Overdue values depend on the date: the views use the database clock (IST calendar date). The manifest records the
  `today` the data was generated for; regenerate with `seed-demo --reset --seed 42 --today YYYY-MM-DD` for a fixed day.
* `ai.v_webhook_health` aggregates the last 7 days (the contract's `event_count_7d`).
