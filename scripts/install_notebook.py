# Databricks notebook source
# MAGIC %md
# MAGIC # GatewayIQ — In-Workspace Installer (no CLI needed)
# MAGIC
# MAGIC Deploy the entire GatewayIQ app **from inside your Databricks workspace** — no
# MAGIC local machine, Databricks CLI, or `git clone` required. You cloned this repo as
# MAGIC a **Git folder** (Workspace → Create → Git folder →
# MAGIC `https://github.com/anuj1303/gatewayiq`); now fill the widgets above and
# MAGIC **Run All**.
# MAGIC
# MAGIC It does everything `install.sh` does, using `spark.sql` + the Databricks SDK
# MAGIC instead of shelling out — and it reuses the SAME `render_config` /
# MAGIC `load_from_gateway` code the CLI path uses, so the two can't drift:
# MAGIC
# MAGIC 1. render `app/app.yaml` from your widget values,
# MAGIC 2. build the UC layer (adapter views → `ai_query` classifier → 13 `v_*` → 33 `ds_*`),
# MAGIC 3. provision Lakebase (DB + the app SP's Postgres role + copy `ds_*` + `app_*` tables),
# MAGIC 4. deploy the **App** (with its db/secret resources),
# MAGIC 5. create the **Weekly Report** + **Data Refresh** Jobs (both **PAUSED**),
# MAGIC 6. print the app URL.
# MAGIC
# MAGIC **Run as** a user who can create the UC catalog/schema, connect to Lakebase as
# MAGIC an admin, and create Apps + Jobs. Idempotent — safe to re-run.

# COMMAND ----------
# MAGIC %pip install databricks-sdk --upgrade -q
# MAGIC %pip install psycopg2-binary pyyaml -q
# MAGIC dbutils.library.restartPython()

# COMMAND ----------
# MAGIC %md ## Configuration — fill these in (same values as `customer.yaml`)

# COMMAND ----------
dbutils.widgets.text("app_name", "gatewayiq", "App name (lowercase)")
dbutils.widgets.text("warehouse_id", "", "SQL warehouse id (for the ai_query classifier)")
dbutils.widgets.text("uc_catalog", "", "UC catalog to write ds_* into")
dbutils.widgets.text("uc_schema", "gatewayiq", "UC schema")
dbutils.widgets.text("inference_table", "", "Unity AI Gateway inference (payload) table")
dbutils.widgets.text("usage_table", "system.serving.endpoint_usage", "AI Gateway usage table")
dbutils.widgets.text("lakebase_instance", "", "Lakebase instance name")
dbutils.widgets.text("lakebase_host", "", "Lakebase host (PGHOST)")
dbutils.widgets.text("lakebase_db", "gatewayiq", "Lakebase database name")
dbutils.widgets.text("app_sp", "", "App service-principal client_id (its Postgres role)")
dbutils.widgets.text("email_domain", "", "Email domain, e.g. acme.com")
dbutils.widgets.text("admin_emails", "", "Comma-separated admin emails (bootstrapped at startup)")
dbutils.widgets.text("app_url", "", "App URL (leave blank on first run; re-run after to set email links)")
dbutils.widgets.text("secret_scope", "gatewayiq", "Secret scope for Gmail creds (optional; leave if unused)")
dbutils.widgets.dropdown("skip_classifier", "false", ["false", "true"], "Skip ai_query classifier (no LLM cost)")
dbutils.widgets.dropdown("refresh_cron", "0 0 6 * * ?", ["0 0 6 * * ?", "0 0 */6 * * ?", "0 0 0 * * ?"], "Data-refresh cron")

# COMMAND ----------
import os, sys, json

# Locate the Git-folder root (this notebook lives in <root>/scripts/) so we can
# import the app modules and render app.yaml into <root>/app.
NB_PATH = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
REPO_ROOT_WS = "/Workspace" + os.path.dirname(os.path.dirname(NB_PATH))   # strip /scripts/<nb>
SCRIPTS_DIR = os.path.join(REPO_ROOT_WS, "scripts")
BACKEND_DIR = os.path.join(REPO_ROOT_WS, "app", "backend")
print("repo root:", REPO_ROOT_WS)

W = lambda k: dbutils.widgets.get(k).strip()
c = {
    "app_name": W("app_name"),
    "warehouse_id": W("warehouse_id"),
    "lakebase": {"instance": W("lakebase_instance"), "host": W("lakebase_host"),
                 "database": W("lakebase_db"), "admin_user": "",  # set below to current_user
                 "app_sp": W("app_sp")},
    "uc": {"catalog": W("uc_catalog"), "schema": W("uc_schema")},
    "sources": {"inference_table": W("inference_table"), "usage_table": W("usage_table")},
    "identity": {"email_domain": W("email_domain"),
                 "admins": [e.strip() for e in W("admin_emails").split(",") if e.strip()], "managers": []},
    "app": {"url": W("app_url"), "classifier_model": "databricks-claude-haiku-4-5"},
    "mail": {"secret_scope": W("secret_scope"), "from_name": "GatewayIQ", "from_email": "", "quota_project": ""},
}
CURRENT_USER = spark.sql("select current_user()").first()[0]
c["lakebase"]["admin_user"] = CURRENT_USER   # loaders connect to Lakebase as the runner

# Make config.py + the shared scripts see these values, then import them.
env = {
    "PGHOST": c["lakebase"]["host"], "PGDATABASE": c["lakebase"]["database"],
    "APP_SP_ROLE": c["lakebase"]["app_sp"], "LAKEBASE_ADMIN_USER": CURRENT_USER,
    "APP_URL": c["app"]["url"], "EMAIL_DOMAIN": c["identity"]["email_domain"],
    "ADMIN_EMAILS": ",".join(c["identity"]["admins"]),
    "SOURCE_INFERENCE_TABLE": c["sources"]["inference_table"],
    "SOURCE_USAGE_TABLE": c["sources"]["usage_table"],
    "UC_CATALOG": c["uc"]["catalog"], "UC_SCHEMA": c["uc"]["schema"],
    "CLASSIFIER_MODEL": c["app"]["classifier_model"],
}
os.environ.update({k: v for k, v in env.items() if v is not None})

for p in (SCRIPTS_DIR, BACKEND_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)
import config as cfg            # noqa: E402
import render_config as rc      # noqa: E402
import load_from_gateway as lg  # noqa: E402

missing = cfg.validate()
assert not missing, f"Missing required config: {missing} — fill the widgets and re-run."
TGT = f"{cfg.UC_CATALOG}.{cfg.UC_SCHEMA}"
SKIP = W("skip_classifier") == "true"
print("config OK →", TGT, "| app:", c["app_name"], "| Lakebase:", c["lakebase"]["host"])

# COMMAND ----------
# MAGIC %md ## 1. Render `app/app.yaml` from your config

# COMMAND ----------
# resolve_pricing reads the bundled fallback (fetch_pricing needs the CLI); good enough
# to start. The daily refresh + a later fetch_pricing run can refine rates.
rc.render_app_yaml(c)
print("wrote", os.path.join(REPO_ROOT_WS, "app", "app.yaml"))

# COMMAND ----------
# MAGIC %md ## 2. Build ds_* in UC (same build_all() the CLI installer runs)

# COMMAND ----------
def run_sql(stmt):
    return spark.sql(stmt)

views, datasets = lg.load_etl_sql()
lg.build_all(run_sql, views, datasets, skip_classifier=SKIP)

# COMMAND ----------
# MAGIC %md ## 3. Provision Lakebase — DB, app-SP role, copy ds_*, app_* tables

# COMMAND ----------
import psycopg2
from psycopg2.extras import execute_values
from psycopg2 import sql as _sql
from databricks.sdk import WorkspaceClient

w = WorkspaceClient()
ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()
PG_TOKEN = ctx.apiToken().get()
HOST, DB, APP_SP = c["lakebase"]["host"], c["lakebase"]["database"], c["lakebase"]["app_sp"]

def pg(dbname):
    conn = psycopg2.connect(host=HOST, port=5432, dbname=dbname, user=CURRENT_USER,
                            password=PG_TOKEN, sslmode="require", connect_timeout=20)
    conn.autocommit = True
    return conn

# 3a. Create the database if missing (via the default admin DB).
admin = pg("databricks_postgres")
with admin.cursor() as cur:
    cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (DB,))
    if not cur.fetchone():
        cur.execute(_sql.SQL("CREATE DATABASE {}").format(_sql.Identifier(DB)))
        print("created database", DB)
    else:
        print("database exists:", DB)
admin.close()

conn = pg(DB)
cur = conn.cursor()

# 3b. Create the app SP's Postgres role (the CAN_CONNECT_AND_CREATE grant does NOT
#     auto-provision it). Look up the SP's numeric id via the SDK for the security
#     label. Best-effort: if it fails, the PUBLIC grant below still lets the app read.
try:
    sp_numeric = None
    for sp in w.service_principals.list():
        if sp.application_id == APP_SP:
            sp_numeric = sp.id
            break
    cur.execute(_sql.SQL("SELECT 1 FROM pg_roles WHERE rolname=%s"), (APP_SP,))
    if not cur.fetchone():
        cur.execute(_sql.SQL("CREATE ROLE {} LOGIN").format(_sql.Identifier(APP_SP)))
    if sp_numeric:
        cur.execute(_sql.SQL("SECURITY LABEL FOR databricks_auth ON ROLE {} IS %s")
                    .format(_sql.Identifier(APP_SP)), (f"id={sp_numeric},type=SERVICE_PRINCIPAL",))
    print(f"app SP role ready ({APP_SP}, numeric={sp_numeric})")
except Exception as e:
    print(f"(SP role setup best-effort — PUBLIC grant will cover reads: {str(e)[:120]})")

# 3c. Copy every ds_* from UC → Lakebase (TEXT columns).
tabs = [r["table_name"] for r in spark.sql(
    f"SELECT table_name FROM {cfg.UC_CATALOG}.information_schema.tables "
    f"WHERE table_schema='{cfg.UC_SCHEMA}' AND table_name LIKE 'ds_%'").collect()]
for t in tabs:
    sdf = spark.table(f"{TGT}.{t}")
    cols = sdf.columns
    cur.execute(_sql.SQL("DROP TABLE IF EXISTS {}").format(_sql.Identifier(t)))
    cur.execute(_sql.SQL("CREATE TABLE {} ({})").format(
        _sql.Identifier(t), _sql.SQL(", ").join(_sql.SQL("{} TEXT").format(_sql.Identifier(col)) for col in cols)))
    rows = [[None if v is None else str(v) for v in r] for r in sdf.collect()]
    if rows:
        stmt = _sql.SQL("INSERT INTO {} ({}) VALUES %s").format(
            _sql.Identifier(t), _sql.SQL(", ").join(_sql.Identifier(col) for col in cols))
        execute_values(cur, stmt.as_string(cur), rows, page_size=1000)
    print(f"  {t}: {len(rows)} rows")

# 3d. The app_* login/user-management tables (owned by the runner; DML granted to SP).
cur.execute("CREATE TABLE IF NOT EXISTS app_users (email TEXT PRIMARY KEY, handle TEXT, name TEXT, "
            "title TEXT, dept TEXT, role TEXT, role_type TEXT, manager TEXT, team TEXT)")
cur.execute("CREATE TABLE IF NOT EXISTS app_credentials (email TEXT PRIMARY KEY, password TEXT)")
cur.execute("CREATE TABLE IF NOT EXISTS app_membership (email TEXT PRIMARY KEY, team_owner TEXT)")
cur.execute("CREATE TABLE IF NOT EXISTS app_wordclouds (owner_email TEXT PRIMARY KEY, created_at TEXT, "
            "scope_kind TEXT, target_label TEXT, date_from TEXT, date_to TEXT, models TEXT, "
            "prompt_count TEXT, image_b64 TEXT)")
cur.execute("CREATE TABLE IF NOT EXISTS app_email_log (recipient TEXT, kind TEXT, subject TEXT, "
            "status TEXT, error TEXT, sent_at TEXT)")

# 3e. Grants: PUBLIC read (covers the SP even if the explicit role lags) + explicit SP.
cur.execute("GRANT USAGE ON SCHEMA public TO PUBLIC")
cur.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO PUBLIC")
cur.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO PUBLIC")
try:
    cur.execute(_sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(_sql.Identifier(APP_SP)))
    cur.execute(_sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}").format(_sql.Identifier(APP_SP)))
    for t in ("app_users", "app_credentials", "app_membership", "app_wordclouds", "app_email_log"):
        cur.execute(_sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON {} TO {}")
                    .format(_sql.Identifier(t), _sql.Identifier(APP_SP)))
    print("granted SELECT + app_* DML to the app SP role")
except Exception as e:
    print(f"(explicit SP grant skipped — PUBLIC covers reads: {str(e)[:100]})")
conn.close()
print(f"Lakebase ready: {len(tabs)} ds_* tables + app_* in {DB}")

# COMMAND ----------
# MAGIC %md ## 4. Deploy the App (with its Lakebase + secret resources)

# COMMAND ----------
from databricks.sdk.service.apps import App, AppResource, AppResourceDatabase, AppResourceSecret, AppDeployment

APP_NAME = c["app_name"]
APP_SRC = os.path.join(REPO_ROOT_WS, "app")   # the synced git-folder app dir

# Resources the app needs at runtime. The DB resource is required; the 3 Gmail
# secrets are only needed if you set up email — added only if the scope has them.
resources = [AppResource(name="gatewayiq-db",
                         database=AppResourceDatabase(instance_name=c["lakebase"]["instance"],
                                                      database_name=DB,
                                                      permission="CAN_CONNECT_AND_CREATE"))]
scope = c["mail"]["secret_scope"]
try:
    have = {s.key for s in w.secrets.list_secrets(scope)}
    for res_name, key in [("gmail-client-id", "google-client-id"),
                          ("gmail-client-secret", "google-client-secret"),
                          ("gmail-refresh-token", "google-refresh-token")]:
        if key in have:
            resources.append(AppResource(name=res_name, secret=AppResourceSecret(
                scope=scope, key=key, permission="READ")))
    print(f"secret scope '{scope}': found {len(resources) - 1} Gmail secret(s)")
except Exception as e:
    print(f"(no secret scope '{scope}' yet — deploying without email creds: {str(e)[:80]})")

# Create the app object if it doesn't exist, else update its resources.
try:
    existing = w.apps.get(name=APP_NAME)
    print("app exists:", APP_NAME, "→ updating resources")
    w.apps.update(name=APP_NAME, app=App(name=APP_NAME, resources=resources))
except Exception:
    print("creating app:", APP_NAME)
    w.apps.create(app=App(name=APP_NAME, resources=resources)).result()

# Deploy the synced source. This is what actually starts/updates the running app.
dep = w.apps.deploy(app_name=APP_NAME,
                    app_deployment=AppDeployment(source_code_path=APP_SRC)).result()
app = w.apps.get(name=APP_NAME)
print("deployment state:", getattr(dep, "status", None))
print("APP URL →", app.url)

# COMMAND ----------
# MAGIC %md ## 5. Create the Weekly Report + Data Refresh Jobs (both PAUSED)

# COMMAND ----------
from databricks.sdk.service.jobs import (JobSettings, Task, NotebookTask, CronSchedule,
                                         PauseStatus)

def upsert_job(name, settings):
    """Create the job, or reset an existing one with the same name (idempotent).
    reset() takes a JobSettings; create() takes typed kwargs — so pull the fields
    off the settings object rather than passing serialized dicts."""
    for j in w.jobs.list():
        if j.settings and j.settings.name == name:
            w.jobs.reset(job_id=j.job_id, new_settings=settings)
            print("updated job:", name, f"(id {j.job_id})")
            return j.job_id
    jid = w.jobs.create(name=settings.name, schedule=settings.schedule,
                        tasks=settings.tasks,
                        max_concurrent_runs=settings.max_concurrent_runs).job_id
    print("created job:", name, f"(id {jid})")
    return jid

common_params = {"pg_host": HOST, "app_url": c["app"]["url"] or (app.url or ""),
                 "app_backend": BACKEND_DIR}

# 5a. Data Refresh — daily, PAUSED.
upsert_job("GatewayIQ — Data Refresh", JobSettings(
    name="GatewayIQ — Data Refresh",
    schedule=CronSchedule(quartz_cron_expression=W("refresh_cron"), timezone_id="UTC",
                          pause_status=PauseStatus.PAUSED),
    max_concurrent_runs=1,
    tasks=[Task(task_key="refresh_dashboard_data",
                notebook_task=NotebookTask(
                    notebook_path=os.path.join(SCRIPTS_DIR, "refresh_data_job"),
                    base_parameters={**common_params, "app_sp": APP_SP,
                                     "scripts_dir": SCRIPTS_DIR,
                                     "skip_classifier": W("skip_classifier")}))]))

# 5b. Weekly Report emails — Mondays 09:00, PAUSED + test_mode on.
upsert_job("GatewayIQ — Weekly Report Emails", JobSettings(
    name="GatewayIQ — Weekly Report Emails",
    schedule=CronSchedule(quartz_cron_expression="0 0 9 ? * MON", timezone_id="UTC",
                          pause_status=PauseStatus.PAUSED),
    max_concurrent_runs=1,
    tasks=[Task(task_key="send_weekly_reports",
                notebook_task=NotebookTask(
                    notebook_path=os.path.join(SCRIPTS_DIR, "weekly_report_job"),
                    base_parameters={**common_params, "test_mode": "true",
                                     "test_recipient": (c["identity"]["admins"] or [""])[0],
                                     "days": "7"}))]))

# COMMAND ----------
# MAGIC %md
# MAGIC ## ✅ Done
# MAGIC
# MAGIC - Open the **APP URL** printed in step 4 and sign in with SSO (one of the
# MAGIC   admin emails you listed). Add your people from the **Manage Users** tab.
# MAGIC - Both Jobs were created **PAUSED**. Enable them in **Workflows** when ready
# MAGIC   (Data Refresh keeps the dashboard current; Weekly Report emails users).
# MAGIC - **Re-run this notebook any time** — every step is idempotent. If you left
# MAGIC   the app URL blank on the first run, paste it into the widget and re-run so
# MAGIC   email links resolve.

# COMMAND ----------
displayHTML(f'<h3>GatewayIQ installed</h3><p>Open your app: '
            f'<a href="{app.url}" target="_blank">{app.url}</a></p>')
