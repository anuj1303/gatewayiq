"""Render app.yaml env + bundle variables from a single customer.yaml.

  python3 scripts/render_config.py --config customer.yaml            # writes app/app.yaml
  python3 scripts/render_config.py --config customer.yaml --print-vars   # prints `--var k=v …` for the bundle

Keeps `customer.yaml` as the single source of truth: the app's runtime env is
generated here, and the same values feed the Databricks Asset Bundle. Requires
PyYAML (`pip install pyyaml`).
"""
import argparse, json, os, sys
import yaml

ROOT = os.path.join(os.path.dirname(__file__), "..")


def render_app_yaml(c):
    mp = json.dumps(c["model_pricing"], separators=(",", ":"))
    env = {
        "PGHOST": c["lakebase"]["host"], "PGDATABASE": c["lakebase"]["database"],
        "PGPORT": "5432", "PGSSLMODE": "require", "APP_SP_ROLE": c["lakebase"]["app_sp"],
        "APP_URL": c["app"]["url"], "EMAIL_DOMAIN": c["identity"]["email_domain"],
        "ADMIN_EMAILS": ",".join(c["identity"].get("admins", [])), "IDENTITY_SOURCE": "directory",
        "SOURCE_INFERENCE_TABLE": c["sources"]["inference_table"],
        "SOURCE_DIRECTORY_TABLE": c["sources"]["directory_table"],
        "SOURCE_USAGE_TABLE": c["sources"].get("usage_table", "system.serving.endpoint_usage"),
        "UC_CATALOG": c["uc"]["catalog"], "UC_SCHEMA": c["uc"]["schema"],
        "CLASSIFIER_MODEL": c["app"].get("classifier_model", "databricks-claude-haiku-4-5"),
        "MODEL_PRICING": mp,
        "MAIL_FROM_NAME": c["mail"].get("from_name", "GatewayIQ"),
        "MAIL_FROM_EMAIL": c["mail"].get("from_email", ""),
        "GOOGLE_QUOTA_PROJECT": c["mail"].get("quota_project", ""),
    }
    lines = ['# GENERATED from customer.yaml by render_config.py — do not edit by hand.',
             'command: ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]', "", "env:"]
    for k, v in env.items():
        lines.append(f'  - name: "{k}"')
        lines.append(f'    value: {json.dumps(v)}')
    # Gmail creds come from the secret-scope app resources (defined in the bundle).
    for name, key in [("GMAIL_CLIENT_ID", "gmail-client-id"), ("GMAIL_CLIENT_SECRET", "gmail-client-secret"),
                      ("GMAIL_REFRESH_TOKEN", "gmail-refresh-token")]:
        lines.append(f'  - name: "{name}"')
        lines.append(f'    valueFrom: "{key}"')
    open(os.path.join(ROOT, "app", "app.yaml"), "w").write("\n".join(lines) + "\n")


def bundle_vars(c):
    wr = c["weekly_report"]
    return {
        "app_name": c["app_name"], "warehouse_id": c["warehouse_id"],
        "lakebase_instance": c["lakebase"]["instance"], "lakebase_host": c["lakebase"]["host"],
        "lakebase_db": c["lakebase"]["database"], "secret_scope": c["mail"]["secret_scope"],
        "uc_catalog": c["uc"]["catalog"], "app_url": c["app"]["url"],
        "test_mode": str(wr.get("test_mode", True)).lower(), "test_recipient": wr.get("test_recipient", ""),
        "days": str(wr.get("days", 7)), "schedule_cron": wr.get("schedule_cron", "0 0 9 ? * MON"),
        "timezone": wr.get("timezone", "UTC"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--print-vars", action="store_true")
    args = ap.parse_args()
    c = yaml.safe_load(open(args.config))
    if args.print_vars:
        print(" ".join(f"--var {k}={json.dumps(v)}" for k, v in bundle_vars(c).items()))
    else:
        render_app_yaml(c)
        print("wrote app/app.yaml from", args.config)


if __name__ == "__main__":
    main()
