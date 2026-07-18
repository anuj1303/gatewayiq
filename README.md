# GatewayIQ — Production Deployment

> 🚀 **New to this / not technical?** Start with **[GET_STARTED.md](GET_STARTED.md)** —
> a plain-English, baby-steps walkthrough (installs the tools, logs you in, fills in
> the config, runs the installer, opens the dashboard) with a jargon glossary and a
> troubleshooting table. This README below is the terser, engineer-oriented version.

A role-based governance console for **Unity AI Gateway** (Databricks App: FastAPI +
React, backed by Lakebase + Unity Catalog). This is the **clean, config-only**
codebase for customer environments — **no demo data, identities, or values**.
(The demo instance with synthetic data lives in the sibling `gatewayiq/` repo.)

## Data & auth model
```
Unity AI Gateway logs + system tables            (real source)
   → adapter views → 13 v_* views → 33 ds_* datasets   (UC, Genie-able)
      → Lakebase (serving copy) → GatewayIQ app         (SSO-scoped)
```
- **Use-case classification is AI-driven** — `ai_query()` with a Claude model
  (`CLASSIFIER_MODEL`, default Haiku). This is enforced, not rule-based.
- **Auth is workspace SSO** — the app reads `X-Forwarded-Email`; identity/teams
  come from the customer directory. No passwords are stored.
- **Scoping**: admin → all users, manager → their team (editable), IC → self.

## Prerequisites
1. Unity AI Gateway **usage tracking + inference (payload) logging** enabled on
   the serving endpoints; **system tables** on (`system.serving.*`, `system.access.audit`).
2. A SQL **warehouse**, a **Lakebase** instance, a **service principal** for the app,
   and (for email) a mail transport + secret scope.

## Install (one command)

Everything is driven by a **single `customer.yaml`** and packaged as a **Databricks Asset Bundle** + a one-command wrapper. From a clone of this repo:

```bash
cp customer.yaml.example customer.yaml      # 1. fill in the customer's values (one file)
# 2. create the mail secret scope `gatewayiq` (google-client-id/secret/refresh-token) — secrets can't live in yaml
./install.sh customer.yaml                  # 3. installs everything
```

`install.sh` does three things from that one config:
1. **render** `app.yaml` env + bundle variables from `customer.yaml`,
2. **`databricks bundle deploy`** — provisions the **App + its db/secret resources + the weekly Job** declaratively (no `apps update --json`, no manual Job creation), and
3. **data-plane install** (`scripts/install.py`) — creates the Lakebase DB, runs `load_from_gateway.py` (AI classifier) to build `ds_*` in UC, copies them to Lakebase, and seeds identity from the directory.

Re-running `install.sh` (or `databricks bundle deploy`) is idempotent — that's your upgrade path too. `bundle validate` passes.

Prefer to run steps individually? Each is a standalone script (`render_config.py`, `load_from_gateway.py`, `seed_identity.py`, `install.py`) — see below.

## Config reference (`customer.yaml`; maps to `app/backend/config.py`)
| Env | Required | Notes |
|---|---|---|
| `PGHOST` / `PGDATABASE` | ✅ / (gatewayiq) | Lakebase serving DB |
| `APP_SP_ROLE` | ✅ | App SP Postgres role (client_id) |
| `LAKEBASE_ADMIN_USER` | ✅ (loaders) | Table-owner identity |
| `APP_URL` | ✅ | Deployed app URL (email links) |
| `EMAIL_DOMAIN` | ✅ | Customer email domain (handle↔email) |
| `ADMIN_EMAILS` | ✅ | Comma-separated admin emails |
| `SOURCE_INFERENCE_TABLE` | ✅ | AI Gateway inference (payload) table |
| `SOURCE_DIRECTORY_TABLE` | ✅ | email/team/dept/role/manager |
| `SOURCE_USAGE_TABLE` | (system.serving.endpoint_usage) | override if different |
| `UC_CATALOG` / `UC_SCHEMA` | ✅ / (gatewayiq) | UC target the loader writes |
| `CLASSIFIER_MODEL` | (Haiku) | AI use-case classifier |
| `MODEL_PRICING` | (auto-resolved from system.billing; bundled fallback) | Full per-model rate map — see "Model pricing" below |
| `MAIL_FROM_EMAIL` / `GMAIL_*` | to send | mail sender + secret-scope creds |
| `IDENTITY_SOURCE` | (directory) | leave as `directory` |

## Individual steps (what `install.sh` runs under the hood)
> The App, its db/secret **resources**, and the weekly **Job** are all declared in
> `databricks.yml` and created by `databricks bundle deploy` — no manual
> `apps update --json` or Job creation. The steps below are the data-plane
> pieces the bundle can't do (plus the dataset/identity builders).

1. **Build datasets from real data** → UC (`ai_query` classifier ON by default):
   ```bash
   SOURCE_INFERENCE_TABLE=<cat.sch.inference> SOURCE_DIRECTORY_TABLE=<cat.sch.dir> \
   UC_CATALOG=<cat> UC_SCHEMA=gatewayiq \
   python3 scripts/load_from_gateway.py --warehouse <id> --profile <p> --dry-run  # inspect first
   ```
   ⚠️ **Validate the ADAPTER views** in `load_from_gateway.py` against the real
   schema (the demo base-table shapes match production, so they're usually
   passthrough — map columns only if names differ).
2. **Seed identity** from the directory:
   ```bash
   ADMIN_EMAILS=<a@x,b@x> SOURCE_DIRECTORY_TABLE=<cat.sch.dir> EMAIL_DOMAIN=<x> \
   python3 scripts/seed_identity.py --warehouse <id> --profile <p> \
     --email-col email --team-col team --manager-col manager_email --role-col title
   ```
3. **Provision Lakebase** (instance + `gatewayiq` DB), push `ds_*` from UC into
   Lakebase (UC→Lakebase sync, or adapt `load_lakebase.py` to read UC), and grant
   the app SP its Postgres role + `SELECT` on `ds_*` / DML on `app_*`.
4. **Secret scope** for mail (`gatewayiq`: `google-client-id/secret/refresh-token`),
   grant the app SP READ.
5. **Deploy the app**: fill `app/app.yaml`, run `scripts/deploy.sh`, then register
   resources: `databricks apps update <app> --json '{"resources":[…]}'` (Apps does
   NOT auto-apply the yaml `resources:` block).
6. **Weekly Job**: upload `scripts/weekly_report_job.py`, create it scheduled
   (keep `test_mode=true` + a `test_recipient` until you're ready to email users).
7. **Verify**: open via SSO → land on My Usage; admins/managers see all tabs;
   Notifications → preview + send-test.

## Model pricing — full catalog, region-correct, auto-resolved
GatewayIQ prices the **entire Unity AI Gateway model catalog** (Claude, Llama,
DBRX, Mixtral, embeddings, external models, …) — not two hard-coded tiers. The
loader computes per-request cost from a **full per-model rate map**, and that map
is populated automatically at install:

1. **`scripts/fetch_pricing.py`** reads the customer's own
   **`system.billing.list_prices`** (their region's SKU prices, at their
   negotiated rates — always current) and writes
   `scripts/gateway_etl/pricing.resolved.json`. `install.sh` runs this first.
   ```bash
   python3 scripts/fetch_pricing.py --profile <p> --warehouse <id>   # prints every SKU it finds
   ```
2. **`scripts/gateway_etl/model_pricing.json`** is the bundled fallback (approximate
   published $/1M rates) for any model billing didn't resolve.
3. **`customer.yaml → model_pricing`** (optional) overrides UI labels, the default
   rate, or specific models:
   ```yaml
   model_pricing:
     labels: { expensive: "Premium", cheap: "Standard" }
     models:
       databricks-claude-sonnet-4-6: { input: 3.0, output: 15.0, tier: premium }
   ```

The loader generates the per-model cost `CASE` across **all** models (unlisted →
`default` rate), and the premium/standard **tier** aggregates (model-mix, savings)
span every premium/standard model. Tier `label`s flow through the UI charts,
columns, and recommendation/email text. No SQL or JSX edits — ever. `render_config`
writes the resolved map into `app.yaml` so the app shows the right labels too.

## Other per-customer tuning
- **Recommendation thresholds** — `app/backend/insights.py` (savings/model-mix/
  anomaly counts) are tuned to demo scale; adjust to the customer's volume.
- **Directory column names** — pass the real ones to `seed_identity.py`.
- **Adapter views** — validate `load_from_gateway.py` adapters vs real schema.

## What is guaranteed demo-free
No demo Lakebase/URL/SP/app-URL, no demo people/roster, no passwords or login
hints, no `versepay`/synthetic data; email domain **and model pricing** are
parameterized. The only literals left are env defaults that are universal
Databricks values (system-table names, ports, the gcloud public OAuth client).
