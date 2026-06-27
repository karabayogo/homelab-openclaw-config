#!/usr/bin/env bash
# test-config.sh — GitOps cattle regression for homelab-openclaw-config
#
# Invoked by .github/workflows/ci.yml on every push and PR to main.
# Validates that the pinned openclaw.json is internally consistent and that
# every model in agents.defaults.model.{primary,fallbacks} exists in
# models.providers.<provider>.models[].id (v2026.6.10 schema requires this).
#
# Exit 0 = healthy; exit 1 = config is unsafe to deploy.
#
# NOTE 2026-06-27: auth.profiles is NOT validated here. The v2026.6.10 schema
# validator on VM 252 rejects auth.profiles with misleading errors. Actual
# auth is loaded from agents/main/agent/auth-profiles.json (per-agent file).
# See references/2026-06-27-openclaw-llm-timeout-cascade-rca.md for the full
# root cause analysis.

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

# ── 2. Static chain-vs-providers check (the fix for 2026-06-27 outage) ─
# For every entry in agents.defaults.model.{primary,fallbacks}:
#   - The provider prefix must exist in models.providers
#   - The model id must exist in models.providers.<provider>.models[].id
# Plus guardrails: timeoutSeconds set, fallbacks list non-empty (in strict mode).
STRICT=1 python3 - <<'PYEOF' "$CONFIG_FILE"
import json, os, sys
cfg_path = sys.argv[1]
with open(cfg_path) as f:
    cfg = json.load(f)

primary = cfg.get("agents", {}).get("defaults", {}).get("model", {}).get("primary", "")
fallbacks = cfg.get("agents", {}).get("defaults", {}).get("model", {}).get("fallbacks", []) or []
chain = ([primary] if primary else []) + list(fallbacks)

providers_cfg = cfg.get("models", {}).get("providers", {})

failures = []
for entry in chain:
    if not entry:
        continue
    if "/" not in entry:
        failures.append(f"[FAIL] chain entry '{entry}' missing provider/model separator")
        continue
    provider, model_id = entry.split("/", 1)
    if provider not in providers_cfg:
        failures.append(
            f"[FAIL] chain entry '{entry}': provider '{provider}' not in models.providers. "
            f"Available: {sorted(providers_cfg.keys())}"
        )
        continue
    pdata = providers_cfg[provider]
    provider_models = pdata.get("models", [])
    model_ids = {m.get("id") for m in provider_models if isinstance(m, dict)}
    if model_id not in model_ids:
        failures.append(
            f"[FAIL] chain entry '{entry}': model '{model_id}' not declared in "
            f"models.providers.{provider}.models[].id (declared: {sorted(model_ids)})"
        )

# timeoutSeconds must be set
timeout_seconds = cfg.get("agents", {}).get("defaults", {}).get("timeoutSeconds")
if timeout_seconds is None:
    failures.append(
        "[FAIL] agents.defaults.timeoutSeconds is not set "
        "(default 120s = silent multi-minute outage when a model fails)"
    )
elif not isinstance(timeout_seconds, int) or timeout_seconds <= 0 or timeout_seconds > 600:
    failures.append(
        f"[FAIL] agents.defaults.timeoutSeconds={timeout_seconds} out of safe range (1..600)"
    )

# fallbacks must be a list (in strict mode, require ≥2 for resilience)
if not fallbacks:
    failures.append(
        "[FAIL] agents.defaults.model.fallbacks is null/empty — "
        "single dead model = silent total Discord outage. Set fallbacks to ≥2."
    )
elif len(fallbacks) < 2:
    failures.append(
        f"[FAIL] agents.defaults.model.fallbacks has only {len(fallbacks)} entry — "
        f"require ≥2 for resilience."
    )

# auth.profiles is dead config on v2026.6.10 — warn if present
auth_profiles = cfg.get("auth", {}).get("profiles", {})
if auth_profiles:
    print(f"[WARN] auth.profiles is present in openclaw.json — v2026.6.10 schema validator "
          f"rejects this with misleading errors. The actual auth is loaded from "
          f"agents/main/agent/auth-profiles.json. Remove auth.profiles from this file.")

if failures:
    for f in failures:
        print(f)
    sys.exit(1)

print(f"CHECKED={len(chain)} chain entries — all have working providers/models")
print(f"  primary: {primary}")
print(f"  fallbacks: {fallbacks}")
print(f"  timeoutSeconds: {timeout_seconds}")
PYEOF

# ── 3. Live probe (best-effort, requires secrets + Ollama on runner) ─
echo "[INFO] Live probe is intentionally skipped in CI (runs on moltbot cron)"
echo "[INFO] See clawd/scripts/sh/cron-model-adaptive-reorder.sh + py/model_health_probe.py"

echo "[OK] Config is safe to deploy"
exit 0
