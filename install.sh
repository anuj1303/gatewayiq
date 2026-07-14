#!/bin/bash
# GatewayIQ — one-command install from a single customer.yaml.
#   ./install.sh [customer.yaml]
# Does: render app.yaml + bundle vars → bundle deploy (app + resources + job)
#       → data-plane install (Lakebase db, datasets, identity).
set -euo pipefail
CFG="${1:-customer.yaml}"
[ -f "$CFG" ] || { echo "config not found: $CFG (copy customer.yaml.example → customer.yaml)"; exit 1; }
PROFILE=$(python3 -c "import yaml,sys;print(yaml.safe_load(open('$CFG')).get('profile','DEFAULT'))")

echo "==> [1/3] render app.yaml from $CFG"
python3 scripts/render_config.py --config "$CFG"

echo "==> [2/3] databricks bundle deploy (app + resources + weekly job)"
VARS=$(python3 scripts/render_config.py --config "$CFG" --print-vars)
eval databricks bundle deploy -t customer --profile "$PROFILE" $VARS

echo "==> [3/3] data-plane install (Lakebase db, datasets, identity)"
python3 scripts/install.py --config "$CFG"

echo "✅ GatewayIQ installed. Open the app (SSO); Notifications → send a test; resume the weekly Job when ready."
