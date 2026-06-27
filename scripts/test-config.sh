#!/usr/bin/env bash
# test-config.sh — GitOps cattle regression for homelab-openclaw-config
#
# Invoked by .github/workflows/ci.yml on every push and PR to main.
# Validates that the pinned openclaw.json is internally consistent and that
# every model in agents.defaults.model.{primary,fallbacks} has a working
# auth profile (either auth.profiles[].provider OR models.providers[].apiKey).
#
# Exit 0 = healthy; exit 1 = config is unsafe to deploy.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_FILE="$REPO_DIR/openclaw/openclaw.json"

if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "[FAIL] Config file not found: $CONFIG_FILE"
  exit 1
fi

echo "[INFO] Validating $CONFIG_FILE"

# ── 1. JSON syntax ────────────────────────────────────────────────────
if ! python3 -c "import json; json.load(open('$CONFIG_FILE'))"; then
  echo "[FAIL] Config is not valid JSON"
  exit 1
fi
echo "[OK] JSON syntax valid"

# ── 2. Static model-health check (strict mode) ───────────────────────
# Clawscripts lives outside this repo — try the canonical path used by
# cron-model-health-check.sh on moltbot. If unavailable, fall back to a
# self-contained in-line check.
HEALTH_CHECK_PY="${HEALTH_CHECK_PY:-/home/moltbot/clawd/scripts/py/_health_check_debug.py}"

if [[ -f "$HEALTH_CHECK_PY" ]]; then
  echo "[INFO] Running static model-health check (STRICT=1) at $HEALTH_CHECK_PY"
  if ! STRICT=1 OPENCLAW_CONFIG="$CONFIG_FILE" python3 "$HEALTH_CHECK_PY" "$CONFIG_FILE"; then
    echo "[FAIL] Static model-health check failed (see above for [FAIL] lines)"
    exit 1
  fi
  echo "[OK] Static model-health check passed"
else
  echo "[WARN] $HEALTH_CHECK_PY not available — running self-contained strict check"
  STRICT=1 python3 - <<'PYEOF' "$CONFIG_FILE"
import json, os, sys
strict = os.environ.get("STRICT") == "1"
cfg_path = sys.argv[1]
with open(cfg_path) as f:
    cfg = json.load(f)

primary = cfg.get("agents", {}).get("defaults", {}).get("model", {}).get("primary", "")
fallbacks = cfg.get("agents", {}).get("defaults", {}).get("model", {}).get("fallbacks", []) or []
chain = [primary] + list(fallbacks) if primary else list(fallbacks)

auth_profiles = cfg.get("auth", {}).get("profiles", {})
profile_providers = set()
for name, prof in auth_profiles.items():
    profile_providers.add(prof.get("provider", name.split(":", 1)[0]))

providers_cfg = cfg.get("models", {}).get("providers", {})
provider_keys = set(providers_cfg.keys())
provider_inline_keys = {p for p, v in providers_cfg.items() if v.get("apiKey")}

failures = []
for entry in chain:
    if not entry:
        continue
    provider = entry.split("/", 1)[0] if "/" in entry else entry
    if provider in profile_providers:
        continue
    if provider in provider_inline_keys:
        continue
    if not strict and provider in ("openai-codex", "openai", "google", "ollama"):
        continue
    failures.append(f"[FAIL] chain entry '{entry}': provider '{provider}' has no auth profile (auth.profiles) and no inline apiKey (models.providers)")

# timeoutSeconds must be set
timeout_seconds = cfg.get("agents", {}).get("defaults", {}).get("timeoutSeconds")
if timeout_seconds is None:
    failures.append("[FAIL] agents.defaults.timeoutSeconds is not set (default 120s = silent outages)")
elif not isinstance(timeout_seconds, int) or timeout_seconds <= 0 or timeout_seconds > 600:
    failures.append(f"[FAIL] agents.defaults.timeoutSeconds={timeout_seconds} out of safe range (1..600)")

# fallbacks must be a list (not None)
if fallbacks is None:
    failures.append("[FAIL] agents.defaults.model.fallbacks is null — single dead model = silent outage")

# 3. Deprecated 'mode'/'type' fields on auth.profiles — v2026.6.10 uses
#    'mode' as the field name (NOT 'type' as documented for v2026.5.27+).
#    Allowed mode values: 'api_key', 'aws-sdk', 'oauth', 'token'.
#    Empirically verified on 2026-06-27: validator accepts 'mode' and
#    rejects 'type' as Unrecognized key.
for pname, prof in auth_profiles.items():
    if not isinstance(prof, dict):
        continue
    if "type" in prof:
        failures.append(
            f'[FAIL] auth.profiles.{pname}: uses "type" field; v2026.6.10 schema '
            f'requires "mode" (api_key|aws-sdk|oauth|token).'
        )
    if "mode" not in prof:
        failures.append(
            f'[FAIL] auth.profiles.{pname}: missing "mode" field. v2026.6.10 '
            f'allowed values: api_key|aws-sdk|oauth|token.'
        )
    elif prof["mode"] not in ("api_key", "aws-sdk", "oauth", "token"):
        failures.append(
            f'[FAIL] auth.profiles.{pname}: "mode" value "{prof["mode"]}" is not '
            f'one of api_key|aws-sdk|oauth|token.'
        )

if failures:
    for f in failures:
        print(f)
    sys.exit(1)
print(f"CHECKED={len(chain)} chain entries — all have working auth")
print(f"  primary: {primary}")
print(f"  fallbacks: {fallbacks}")
print(f"  timeoutSeconds: {timeout_seconds}")
PYEOF
fi

# ── 3. Live probe (best-effort, requires secrets + Ollama on runner) ─
# In CI we only do the static check (step 2). The live probe must run on
# the LAN self-hosted runner, not GitHub-hosted (no LAN access). The
# cron-model-adaptive-reorder.sh on moltbot already runs the live probe
# every 5 minutes and reorders the chain on health changes.
echo "[INFO] Live probe is intentionally skipped in CI (runs on moltbot cron)"
echo "[INFO] See clawd/scripts/sh/cron-model-adaptive-reorder.sh + py/model_health_probe.py"

# ── 4. Reference integrity (openclaw runtime is NOT modified by us) ──
echo "[OK] Config is safe to deploy"
exit 0
