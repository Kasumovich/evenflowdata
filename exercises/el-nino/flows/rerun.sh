#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Systematic re-run. Cron this, or point a webhook at it.
#
#   crontab:  0 */6 * * *  /opt/enso-tracker/flows/rerun.sh >> /var/log/enso.log 2>&1
#
# Treats the previous run as the baseline and only updates forward. Exits
# non-zero ONLY on infrastructure failure -- a dead data source is a normal,
# handled condition that produces a stale badge and an alert, not a failed run.
# ---------------------------------------------------------------------------
set -euo pipefail

ROOT="${ENSO_TRACKER_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$ROOT"

if [[ -f .env ]]; then
  set -a; source .env; set +a
fi

STAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
OUT="${ENSO_OUTPUT_DIR:-$ROOT/output}"
LOCK="${TMPDIR:-/tmp}/enso-tracker.lock"

# Prevent overlapping runs -- a slow ingest must not race the next cron tick.
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "[$STAMP] previous run still in progress; skipping this tick"
  exit 0
fi

echo "[$STAMP] === enso-tracker systematic re-run ==="

PY="${PYTHON:-python3}"
export PYTHONPATH="$ROOT/src:${PYTHONPATH:-}"

# Steps A-C and F.
$PY -m enso_tracker.cli run "$@"

# Step D.
$PY -m enso_tracker.cli report --in-docs || \
  echo "[warn] Situation Report step failed; continuing"

# Step E: publish and archive.
$PY -m enso_tracker.cli export

ARCHIVE="$OUT/archive"
mkdir -p "$ARCHIVE"
if [[ -f "$OUT/dashboard.html" ]]; then
  cp "$OUT/dashboard.html" "$ARCHIVE/dashboard_$(date -u +%Y%m%dT%H%M%SZ).html"
fi

# Retain 90 days of snapshots; the Parquet store keeps the full audit history
# regardless, so this only bounds the rendered artefacts.
find "$ARCHIVE" -name 'dashboard_*.html' -mtime +90 -delete 2>/dev/null || true

# Step F: surface alerts to the caller's log and optional webhook.
if [[ -f "$OUT/dashboard_payload.json" ]]; then
  $PY - <<'PYCODE'
import json, os, sys
payload = json.load(open(os.path.join(
    os.environ.get("ENSO_OUTPUT_DIR", "output"), "dashboard_payload.json")))
alerts = payload.get("alerts", [])
for a in alerts:
    print(f"[ALERT] {a}")
hook = os.environ.get("ENSO_ALERT_WEBHOOK")
if alerts and hook:
    try:
        import requests
        requests.post(hook, json={"alerts": alerts}, timeout=20)
        print(f"[info] posted {len(alerts)} alert(s) to webhook")
    except Exception as exc:
        print(f"[warn] webhook failed: {exc}", file=sys.stderr)
PYCODE
fi

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] === complete ==="
