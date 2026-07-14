"""Build the GatewayIQ `ds_*` datasets from REAL Unity AI Gateway data.

This replays the whole demo ETL — adapter layer → 13 views → 33 `ds_*` datasets —
against a customer's real tables, writing UC managed tables (Genie-able source of
truth) and optionally pushing them to Lakebase (the app's serving copy).

Layers it builds in `{UC_CATALOG}.{UC_SCHEMA}`:
  1. ADAPTER views (`ai_gateway_usage`, `ai_gateway_inference_logs`, `user_directory`)
     over the real sources. The demo base-table shapes ARE the real Unity AI
     Gateway inference/usage shapes, so these are usually near-passthrough — but
     this is the one place you map columns if a name differs. ← EDIT HERE.
  2. `classified_requests` — per-request use-case labels via `ai_query()` (the
     Haiku classifier from notebook 04). LLM per row — cost scales with volume.
  3. `anomaly_legend` — small static reference (copied from source if present).
  4. The 13 `v_*` views (definitions captured from the demo, schema-substituted).
  5. The 33 `ds_*` datasets, materialized as `CREATE OR REPLACE TABLE` (CTAS).

All source/target names come from `app/backend/config.py` (env-overridable).
Reference SQL lives in `scripts/gateway_etl/{views,datasets,base_tables}.json`.

    python3 scripts/load_from_gateway.py --warehouse <id> [--profile <p>] [--to-lakebase] [--dry-run]

NOTE: This is a config-driven reference implementation. Validate the ADAPTER
views against the customer's real schema before a production run.
"""
import argparse, json, os, re, subprocess, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app", "backend"))
import config as cfg  # noqa: E402

ETL = os.path.join(os.path.dirname(__file__), "gateway_etl")
TGT = f"{cfg.UC_CATALOG}.{cfg.UC_SCHEMA}"


def run_sql(profile, warehouse, stmt, dry):
    if dry:
        print("  [dry-run]\n" + "\n".join("    " + l for l in stmt.splitlines()[:6]) + " ...")
        return {"status": {"state": "DRY"}}
    r = subprocess.run(["databricks", "api", "post", "/api/2.0/sql/statements/", "--profile", profile,
                        "--json", json.dumps({"statement": stmt, "warehouse_id": warehouse,
                                              "format": "JSON_ARRAY", "wait_timeout": "50s"})],
                       capture_output=True, text=True)
    d = json.loads(r.stdout or "{}")
    st = d.get("status", {}).get("state")
    while st in ("PENDING", "RUNNING"):
        time.sleep(2)
        sid = d["statement_id"]
        r = subprocess.run(["databricks", "api", "get", f"/api/2.0/sql/statements/{sid}", "--profile", profile],
                           capture_output=True, text=True)
        d = json.loads(r.stdout or "{}"); st = d.get("status", {}).get("state")
    if st != "SUCCEEDED":
        raise RuntimeError(f"SQL failed ({st}): {json.dumps(d.get('status', {}))[:300]}\n---\n{stmt[:400]}")
    return d


def _reprice(sql):
    """Swap the demo model names + per-model token rates for the configured
    MODEL_PRICING (customer models/rates) — no SQL editing needed."""
    p = cfg.MODEL_PRICING
    exp, ch, dft = p["expensive"], p["cheap"], p["default"]
    sql = sql.replace("'claude-sonnet-4-6'", f"'{exp['name']}'").replace("'claude-haiku-4-5'", f"'{ch['name']}'")
    # (token_col * rate) — literal uniquely identifies the tier; 15.0 before 5.0.
    reps = [(r"(input_tokens\s*\*\s*)3\.0\b", exp["input"]),
            (r"(output_tokens\s*\*\s*)15\.0\b", exp["output"]),
            (r"(input_tokens\s*\*\s*)0\.80?\b", ch["input"]),
            (r"(output_tokens\s*\*\s*)4\.0\b", ch["output"]),
            (r"(input_tokens\s*\*\s*)1\.0\b", dft["input"]),
            (r"(output_tokens\s*\*\s*)5\.0\b", dft["output"])]
    for pat, val in reps:
        sql = re.sub(pat, lambda m, v=val: f"{m.group(1)}{v}", sql)
    return sql


def sub(s):
    """Repoint captured demo SQL at the target UC schema + apply configured pricing."""
    s = s.replace("finserv_ai_governance.gateway_demo.", TGT + ".").replace("gateway_demo.", TGT + ".")
    return _reprice(s)


def view_body(ddl):
    # Header is `CREATE VIEW name (cols) [DEFAULT COLLATION ...][WITH SCHEMA
    # COMPENSATION] AS <query>`; the query starts with SELECT / WITH / '('.
    m = re.search(r"\bAS\s+(SELECT|WITH|\()", ddl, re.I)
    if not m:
        raise ValueError("could not locate view body (no 'AS SELECT/WITH/(')")
    return ddl[m.start(1):]   # from the query keyword onward


# ── ADAPTER layer: real Unity AI Gateway tables → the shape the ETL expects ──
# The demo base-table shapes equal the real AI Gateway inference/usage shapes,
# so these default to passthrough. If the customer's column names differ, map
# them explicitly here (rename in the SELECT).
def adapter_views():
    return {
        # Per-request usage/cost/guardrail/latency (real: AI Gateway usage table)
        "ai_gateway_usage": f"SELECT * FROM {cfg.SOURCE_USAGE_TABLE}",
        # Payload logging: request/response JSON, requester, tokens (real: AI Gateway inference table)
        "ai_gateway_inference_logs": f"SELECT * FROM {cfg.SOURCE_INFERENCE_TABLE}",
        # Org directory: email → team/department/role. Falls back to distinct
        # requesters if no directory table is configured.
        "user_directory": (f"SELECT user_email, team, department, role, primary_model FROM {cfg.SOURCE_DIRECTORY_TABLE}"
                           if cfg.SOURCE_DIRECTORY_TABLE else
                           f"SELECT DISTINCT requester AS user_email, CAST(NULL AS STRING) team, "
                           f"CAST(NULL AS STRING) department, CAST(NULL AS STRING) role, "
                           f"CAST(NULL AS STRING) primary_model FROM {cfg.SOURCE_INFERENCE_TABLE}"),
    }


CLASSIFIER_SQL = f"""
CREATE OR REPLACE TABLE {TGT}.classified_requests AS
SELECT *, LOWER(TRIM(ai_query('{cfg.CLASSIFIER_MODEL}', CONCAT(
  'Classify this AI assistant request into exactly ONE category. Reply with ONLY the label.\\n',
  'Categories: coding, code_review, debugging, sql_query, data_analysis, business_question, ',
  'regulatory, test_generation, documentation, math, architecture, trivial.\\n\\nRequest: ',
  get_json_object(request, '$.messages[0].content')
))) AS classified_use_case
FROM {TGT}.ai_gateway_inference_logs WHERE status_code = 200
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--warehouse", required=True, help="target SQL warehouse id")
    ap.add_argument("--profile", default=cfg.DATABRICKS_PROFILE)
    ap.add_argument("--to-lakebase", action="store_true", help="also copy ds_* into Lakebase")
    ap.add_argument("--skip-classifier", action="store_true", help="skip the ai_query use-case classifier (LLM cost)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    views = json.load(open(f"{ETL}/views.json"))
    datasets = json.load(open(f"{ETL}/datasets.json"))
    run = lambda s: run_sql(args.profile, args.warehouse, s, args.dry_run)

    print(f"Target: {TGT}  |  usage={cfg.SOURCE_USAGE_TABLE}  inference={cfg.SOURCE_INFERENCE_TABLE}")
    run(f"CREATE CATALOG IF NOT EXISTS {cfg.UC_CATALOG}")
    run(f"CREATE SCHEMA IF NOT EXISTS {TGT}")

    print("== 1. adapter views ==")
    for name, body in adapter_views().items():
        run(f"CREATE OR REPLACE VIEW {TGT}.{name} AS {body}"); print(f"  {name}")

    if not args.skip_classifier:
        print("== 2. classified_requests (ai_query) =="); run(CLASSIFIER_SQL); print("  done")
    else:
        run(f"CREATE OR REPLACE VIEW {TGT}.classified_requests AS SELECT *, 'trivial' AS classified_use_case "
            f"FROM {TGT}.ai_gateway_inference_logs WHERE status_code = 200")

    print("== 3. anomaly_legend (static ref) ==")
    run(f"CREATE TABLE IF NOT EXISTS {TGT}.anomaly_legend (priority INT, anomaly_code STRING, anomaly_type STRING, "
        f"what_it_detects STRING, example STRING, detection_method STRING)")

    print("== 4. views ==")
    for name, ddl in views.items():
        run(f"CREATE OR REPLACE VIEW {TGT}.{name} AS {sub(view_body(ddl))}"); print(f"  {name}")

    print("== 5. ds_* datasets (materialized) ==")
    for name, dsql in datasets.items():
        run(f"CREATE OR REPLACE TABLE {TGT}.{name} AS {sub(dsql)}"); print(f"  {name}")

    print(f"\nOK — built adapter + {len(views)} views + {len(datasets)} ds_* tables in {TGT}")
    if args.to_lakebase:
        print("\n== 6. push ds_* to Lakebase ==")
        print("  Run: python3 scripts/load_lakebase.py  (point PGHOST/PGDATABASE at the target Lakebase,")
        print("       and source from UC instead of JSON), or set up a UC→Lakebase sync pipeline.")
    print("\nNext: seed app_users/app_membership from your directory (IDENTITY_SOURCE=directory),")
    print("configure mail transport, and deploy the app pointing PGHOST/PGDATABASE at the target Lakebase.")


if __name__ == "__main__":
    main()
