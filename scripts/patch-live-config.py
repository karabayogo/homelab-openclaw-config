#!/usr/bin/env python3
"""Targeted patch: update only agents.defaults in the live openclaw.json,
preserving the rest of the rich live config (channels, gateway, session, etc.).

This is the GitOps sync operation: the GitOps repo is the source of truth for
agents.defaults, but the rest of the live config has been hand-tuned and
should be preserved.

Inputs:
  --live PATH     Path to live openclaw.json (e.g. /home/henesink/.openclaw/openclaw.json)
  --source PATH   Path to GitOps source (e.g. homelab-openclaw-config/openclaw/openclaw.json)
  --backup        Create a timestamped backup before mutating
  --dry-run       Print what would change without writing

Exit 0 = patched (or no changes needed); exit 1 = error.
"""
import argparse
import json
import os
import sys
import time

STRICT = os.environ.get("STRICT") == "1"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", required=True)
    ap.add_argument("--source", required=True)
    ap.add_argument("--backup", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with open(args.live) as f:
        live = json.load(f)
    with open(args.source) as f:
        source = json.load(f)

    source_agents_defaults = source.get("agents", {}).get("defaults", {})
    live_agents_defaults = live.get("agents", {}).get("defaults", {})

    # Build a diff: what fields from source.defaults override live.defaults.
    overrides = {}
    for key in ("timeoutSeconds", "model", "models", "workspace"):
        if key in source_agents_defaults and source_agents_defaults[key] != live_agents_defaults.get(key):
            overrides[key] = source_agents_defaults[key]

    if not overrides:
        print("[OK] No changes needed — live config already matches source.")
        sys.exit(0)

    print(f"[INFO] Will patch live config with these overrides:")
    for k, v in overrides.items():
        if k == "model":
            print(f"  model.primary: {v.get('primary')}")
            print(f"  model.fallbacks: {v.get('fallbacks')}")
        elif k == "models":
            print(f"  models: {list(v.keys())}")
        else:
            print(f"  {k}: {v}")

    if args.dry_run:
        print("[DRY-RUN] No changes written.")
        sys.exit(0)

    if args.backup:
        ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        backup = f"{args.live}.bak-targeted-patch-{ts}"
        with open(backup, "w") as f:
            json.dump(live, f, indent=2)
        print(f"[INFO] Backup written: {backup}")

    # Apply the patch
    if "agents" not in live:
        live["agents"] = {}
    if "defaults" not in live["agents"]:
        live["agents"]["defaults"] = {}
    for k, v in overrides.items():
        live["agents"]["defaults"][k] = v

    with open(args.live, "w") as f:
        json.dump(live, f, indent=2)
    os.chmod(args.live, 0o600)

    print(f"[OK] Patched {args.live}")
    print(f"     final agents.defaults.timeoutSeconds: {live['agents']['defaults'].get('timeoutSeconds')}")
    print(f"     final agents.defaults.model.primary: {live['agents']['defaults']['model'].get('primary')}")
    print(f"     final agents.defaults.model.fallbacks: {live['agents']['defaults']['model'].get('fallbacks')}")


if __name__ == "__main__":
    main()
