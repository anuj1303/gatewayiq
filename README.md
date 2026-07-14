# GatewayIQ — Production Deployment

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

## Config (fill every `<SET_ME>` in `app/app.yaml`; see `app/backend/config.py`)
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
| `MODEL_PRICING` | (Claude Sonnet/Haiku demo rates) | JSON: customer models + $/1M-token rates (see below) |
| `MAIL_FROM_EMAIL` / `GMAIL_*` | to send | mail sender + secret-scope creds |
| `IDENTITY_SOURCE` | (directory) | leave as `directory` |

## Deploy steps
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

## Model pricing — now config-driven (no SQL editing)
The loader computes cost from tokens using **`MODEL_PRICING`** (env). It has two
named tiers + a default; set it to the customer's models and negotiated rates:
```bash
export MODEL_PRICING='{
  "expensive": {"name": "<expensive model id>", "label": "GPT-4o",      "input": 5.0,  "output": 20.0},
  "cheap":     {"name": "<cheap model id>",     "label": "GPT-4o-mini", "input": 0.15, "output": 0.6},
  "default":   {"input": 1.0, "output": 5.0}
}'   # $ per 1M tokens; defaults = Claude Sonnet/Haiku
```
`load_from_gateway.py` swaps the model names + rates throughout the ETL, and the
`label`s flow through the **UI** (the "Model Choice" chart title + the tier
columns) and the **recommendation text / emails** — all via `MODEL_PRICING`, no
SQL or JSX edits needed. Set `MODEL_PRICING` in `app.yaml` too so the app shows
the right labels.

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
