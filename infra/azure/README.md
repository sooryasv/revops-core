# Deploying to Azure Container Apps

WROTE-NOT-RUN: these steps were written, not executed (no Azure subscription in the build sandbox). Steps marked **MUST-VERIFY**
depend on current Azure behaviour or pricing; check the Azure documentation before relying on them. Replace the `<placeholders>`.

## 0. Decide the cost shape

- **Free-offer eligibility (MUST-VERIFY):** check whether your account qualifies for any free credit or free-tier database before creating resources, and set a budget alert. See `.build/UNVERIFIED.md` U8.
- **Minimum replicas:** `0` costs nothing while idle but the first request after idle is slow (cold start; the pay page and webhooks can time out); `1` costs a little always but is responsive. For payments, prefer `1` once real testers use it.

## 1. Resource group, environment, database

```bash
az login
az group create --name rg-revops --location centralindia
az extension add --name containerapp --upgrade
az containerapp env create --name env-revops --resource-group rg-revops --location centralindia

# PostgreSQL Flexible Server (MUST-VERIFY SKU/tier and region availability)
az postgres flexible-server create --resource-group rg-revops --name <pg-name> --location centralindia \
  --admin-user pgadmin --admin-password '<strong-admin-password>' --version 17 --tier Burstable --sku-name Standard_B1ms \
  --storage-size 32 --public-access None
az postgres flexible-server db create --resource-group rg-revops --server-name <pg-name> --database-name revops
```

Allow the Container Apps environment to reach the server (private networking or a firewall rule; MUST-VERIFY the option that fits your plan).

## 2. Create the database roles

From a machine that can reach the server, as the admin user, once:

```bash
psql "host=<pg-name>.postgres.database.azure.com dbname=revops user=pgadmin sslmode=require" \
  -v ON_ERROR_STOP=1 -v dbname=revops -v owner_password='<owner-pw>' -v app_password='<app-pw>' -v ai_password='<ai-pw>' \
  -f contract/roles.sql
```

(Managed servers may not give the admin user full superuser rights; if `roles.sql` fails on a privilege, note which statement, and
ask the platform docs how to grant it. MUST-VERIFY.) Then run migrations as the owner (also done by `deploy.yml`):

```bash
cd backend && MIGRATION_DATABASE_URL='postgresql+asyncpg://revops_owner:<owner-pw>@<pg-name>.postgres.database.azure.com:5432/revops?ssl=require' uv run alembic upgrade head
```

## 3. Create the app and its secrets

```bash
az containerapp create --name revops --resource-group rg-revops --environment env-revops \
  --image ghcr.io/<owner>/<repo>:latest --target-port 8000 --ingress external \
  --min-replicas 1 --max-replicas 1 \
  --secrets db-url='postgresql+asyncpg://revops_app:<app-pw>@<pg-name>.postgres.database.azure.com:5432/revops?ssl=require' \
            jwt-key="$(cat jwt.pem)" rzp-key-secret='<test-key-secret>' rzp-webhook-secret='<webhook-secret>' \
  --env-vars ENV=production DATABASE_URL=secretref:db-url JWT_PRIVATE_KEY_PEM=secretref:jwt-key JWT_KEY_ID=prod-1 \
             PAYMENT_PROVIDER=razorpay RAZORPAY_KEY_ID=<rzp_test_key_id> RAZORPAY_KEY_SECRET=secretref:rzp-key-secret \
             RAZORPAY_WEBHOOK_SECRET=secretref:rzp-webhook-secret \
             PUBLIC_BASE_URL=https://<your-domain> ALLOWED_ORIGINS=https://<your-domain>
```

The runtime container gets only the **app** role URL. Generate the JWT key with `openssl genrsa -out jwt.pem 2048`. If the GHCR package is private, add registry credentials
(`--registry-server ghcr.io --registry-username <user> --registry-password <PAT with read:packages>`). Production refuses to start without the JWT key or with the fake provider.

## 4. Custom domain and certificate (MUST-VERIFY)

```bash
az containerapp hostname add --name revops --resource-group rg-revops --hostname revops.<your-domain>
# create the DNS records Azure tells you to (CNAME to the app FQDN, TXT for domain verification), then:
az containerapp hostname bind --name revops --resource-group rg-revops --hostname revops.<your-domain> \
  --environment env-revops --validation-method CNAME
```

The managed certificate steps and validation method change over time; follow the current Container Apps "custom domain and managed certificate" guide.

## 5. Razorpay webhook

Dashboard (Test Mode) → Webhooks → URL `https://<your-domain>/api/v1/webhooks/razorpay`, secret equal to `rzp-webhook-secret`, events
`payment.authorized`, `payment.captured`, `payment.failed`, `order.paid`. Then follow `docs/LOCAL_VERIFICATION.md` steps 3 to 5 against the deployed URL.

## 6. GitHub Actions deployment through OIDC (no stored cloud password)

1. Create an app registration (or user-assigned managed identity) and a **federated credential** for your repository and the `production` environment (MUST-VERIFY the current portal/CLI flow: `az ad app federated-credential create`).
2. Give it the `Contributor` role on `rg-revops` only.
3. In GitHub add repository secrets `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `MIGRATION_DATABASE_URL`, optional `SMOKE_EMAIL`/`SMOKE_PASSWORD`, and variables `AZURE_RESOURCE_GROUP`, `AZURE_CONTAINER_APP`, `APP_URL`. Add required reviewers on the `production` environment.
4. Push a tag `vX.Y.Z` or run the `deploy` workflow manually.

Reporting refresh: schedule `python -m app.cli refresh-reporting` (a Container Apps job with a cron expression) at least daily.

## Fallback host (if credits expire)

Nothing here is Azure-specific except these commands. Any container host plus any managed PostgreSQL works: run the same image with the same environment variables,
run `contract/roles.sql` and `alembic upgrade head` once, and put a TLS-terminating proxy in front. Cheap options are a small VM with Docker Compose (`docker-compose.yml`,
swap the dev passwords) or another PaaS that runs containers; keep `ENV=production`, one replica while rate limits are in-process, and daily dumps (`docs/runbook.md`).
