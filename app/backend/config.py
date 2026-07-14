"""GatewayIQ — production configuration (env-driven, no demo values).

Every environment-specific value is read from an env var. Infra + customer-
specific values have NO baked-in default and MUST be set (see REQUIRED below);
universal Databricks values (system tables, ports) have sensible defaults.

Set these in `app.yaml` (app) and pass to the loaders / Job. `validate()` logs
anything missing at startup.
"""
import os


def _env(name, default=""):
    return os.environ.get(name, default).strip()


# ── Lakebase (serving DB the app reads; also holds app_* state) ──────────────
LAKEBASE_HOST = _env("PGHOST")                       # REQUIRED — Lakebase instance host
LAKEBASE_DB = _env("PGDATABASE", "gatewayiq")
LAKEBASE_PORT = int(_env("PGPORT", "5432"))
LAKEBASE_SSLMODE = _env("PGSSLMODE", "require")
APP_SP_ROLE = _env("APP_SP_ROLE")                    # REQUIRED — app SP Postgres role (client_id)
LAKEBASE_ADMIN_USER = _env("LAKEBASE_ADMIN_USER")    # REQUIRED for loaders — table owner identity
DATABRICKS_PROFILE = _env("DATABRICKS_PROFILE", "DEFAULT")

# ── App identity / links ─────────────────────────────────────────────────────
APP_URL = _env("APP_URL")                            # REQUIRED — deployed app URL (email links)
EMAIL_DOMAIN = _env("EMAIL_DOMAIN")                  # REQUIRED — customer email domain (handle↔email)

# ── Mail (weekly report emails) ──────────────────────────────────────────────
MAIL_TRANSPORT = _env("MAIL_TRANSPORT", "gmail")     # "gmail" | "smtp"
MAIL_FROM_NAME = _env("MAIL_FROM_NAME", "GatewayIQ")
MAIL_FROM_EMAIL = _env("MAIL_FROM_EMAIL")            # REQUIRED to send — sender on customer domain
GOOGLE_QUOTA_PROJECT = _env("GOOGLE_QUOTA_PROJECT")  # gmail transport only

# ── Data source: real Unity AI Gateway tables (loaders) ──────────────────────
SOURCE_USAGE_TABLE = _env("SOURCE_USAGE_TABLE", "system.serving.endpoint_usage")
SOURCE_INFERENCE_TABLE = _env("SOURCE_INFERENCE_TABLE")   # REQUIRED — AI Gateway inference (payload) table
SOURCE_AUDIT_TABLE = _env("SOURCE_AUDIT_TABLE", "system.access.audit")
SOURCE_DIRECTORY_TABLE = _env("SOURCE_DIRECTORY_TABLE")   # REQUIRED for identity — email/team/dept/role/manager
# AI classifier for use-case labelling (ALWAYS on — classification MUST be AI-driven).
CLASSIFIER_MODEL = _env("CLASSIFIER_MODEL", "databricks-claude-haiku-4-5")

# Model pricing ($ per 1M tokens) used by the loader to compute cost from tokens.
# Two named tiers + a default for any other model. Set `MODEL_PRICING` (JSON) to
# the customer's actual models & negotiated rates — no SQL editing needed.
import json as _json  # noqa: E402
_PRICING_DEFAULT = {
    "expensive": {"name": "claude-sonnet-4-6", "label": "Sonnet", "input": 3.0, "output": 15.0},
    "cheap":     {"name": "claude-haiku-4-5",  "label": "Haiku",  "input": 0.8, "output": 4.0},
    "default":   {"input": 1.0, "output": 5.0},
}
try:
    MODEL_PRICING = _json.loads(_env("MODEL_PRICING")) if _env("MODEL_PRICING") else _PRICING_DEFAULT
except Exception:
    MODEL_PRICING = _PRICING_DEFAULT

# Short UI labels for the two model tiers (used in charts / columns / reco text).
EXPENSIVE_LABEL = MODEL_PRICING.get("expensive", {}).get("label", "Sonnet")
CHEAP_LABEL = MODEL_PRICING.get("cheap", {}).get("label", "Haiku")

# ── UC target (Genie-able source of truth the loaders write) ─────────────────
UC_CATALOG = _env("UC_CATALOG")                      # REQUIRED
UC_SCHEMA = _env("UC_SCHEMA", "gatewayiq")

# ── Identity ─────────────────────────────────────────────────────────────────
# Production default is "directory": app_users / app_membership are seeded from
# SOURCE_DIRECTORY_TABLE by scripts/seed_identity.py, and auth is workspace SSO.
IDENTITY_SOURCE = _env("IDENTITY_SOURCE", "directory")
ADMIN_EMAILS = [e.strip().lower() for e in _env("ADMIN_EMAILS").split(",") if e.strip()]

# Values that must be set for a working deployment.
REQUIRED = ["PGHOST", "APP_SP_ROLE", "APP_URL", "EMAIL_DOMAIN",
            "SOURCE_INFERENCE_TABLE", "SOURCE_DIRECTORY_TABLE", "UC_CATALOG"]


def validate():
    return [v for v in REQUIRED if not os.environ.get(v, "").strip()]
