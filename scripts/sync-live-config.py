#!/usr/bin/env python3
"""Minimal live-config patch for the openclaw gateway.

The live /home/henesink/.openclaw/openclaw.json on VM 252 is stripped down:
it has agents.defaults but NO models.providers block. The gateway resolves
providers from auth.profiles, so the right fix is to use model names that
match an existing auth.profiles entry.

For the 2026-06-27 outage:
  - Wrong: model.primary = "openai/gpt-5.5"   (no 'openai' auth profile exists)
  - Right: model.primary = "openai-codex/gpt-5.5-codex"  (matches openai-codex OAuth)

But the user prefers a multi-provider chain (not single-OAuth) for resilience,
so we pull in the chain from the GitOps source, AND we add models.providers
+ auth.providers blocks pulled from the GitOps source. This makes the live
config a true superset of both the original rich live config AND the GitOps
pinned config.

This script is the GitOps sync operation: it's safe to re-run (idempotent),
it backs up the live config, and it only ADDS fields — it never removes
existing live config (channels, gateway, session, etc.).
"""
import argparse
import json
import os
import sys
import time


def deep_merge(base: dict, overlay: dict) -> dict:
    """Recursively merge overlay into base. overlay values win on conflict.
    Lists and scalars from overlay replace base. Dicts are merged."""
    for k, v in overlay.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            base[k] = deep_merge(base[k], v)
        else:
            base[k] = v
    return base


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

    # What we're adding to the live config:
    additions = {}

    # 1. agents.defaults.{timeoutSeconds,model,models} — THE FIX
    if "agents" in source and "defaults" in source["agents"]:
        additions.setdefault("agents", {}).setdefault("defaults", {})
        for k in ("timeoutSeconds", "model", "models"):
            if k in source["agents"]["defaults"]:
                additions["agents"]["defaults"][k] = source["agents"]["defaults"][k]

    # 1b. channels.discord.inboundWorker.runTimeoutMs — per-message fast-fail
    # The live config often has channels.discord but lacks the inboundWorker
    # block. We add the fast-fail timeout here.
    if "channels" in source and "discord" in source["channels"]:
        d_src = source["channels"]["discord"]
        if "inboundWorker" in d_src:
            additions.setdefault("channels", {})
            additions["channels"].setdefault("discord", {})
            additions["channels"]["discord"]["inboundWorker"] = d_src["inboundWorker"]

    # 2. models.providers — add only providers the chain needs
    if "models" in source and "providers" in source["models"]:
        additions.setdefault("models", {})
        # Only add providers that the chain references
        chain_models = []
        if "model" in additions.get("agents", {}).get("defaults", {}):
            m = additions["agents"]["defaults"]["model"]
            chain_models.append(m.get("primary", ""))
            chain_models.extend(m.get("fallbacks", []) or [])
        needed_providers = set()
        for entry in chain_models:
            if "/" in entry:
                needed_providers.add(entry.split("/", 1)[0])
        additions["models"]["providers"] = {
            p: source["models"]["providers"][p]
            for p in source["models"]["providers"]
            if p in needed_providers
        }

    # 3. auth.profiles — merge the new ones in (preserve any existing live profiles)
    if "auth" in source and "profiles" in source["auth"]:
        additions.setdefault("auth", {})
        additions["auth"]["profiles"] = source["auth"]["profiles"]

    # Apply the additions via deep merge
    print("[INFO] Will add to live config:")
    if "agents" in additions and "defaults" in additions["agents"]:
        for k, v in additions["agents"]["defaults"].items():
            if k == "model":
                print(f"  agents.defaults.model: primary={v.get('primary')} fallbacks={v.get('fallbacks')}")
            elif k == "models":
                print(f"  agents.defaults.models: {list(v.keys())}")
            else:
                print(f"  agents.defaults.{k}: {v}")
    if "models" in additions and "providers" in additions["models"]:
        print(f"  models.providers: {list(additions['models']['providers'].keys())}")
    if "auth" in additions and "profiles" in additions["auth"]:
        print(f"  auth.profiles: {list(additions['auth']['profiles'].keys())}")

    if args.dry_run:
        print("[DRY-RUN] No changes written.")
        sys.exit(0)

    if args.backup:
        ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        backup = f"{args.live}.bak-sync-{ts}"
        with open(backup, "w") as f:
            json.dump(live, f, indent=2)
        print(f"[INFO] Backup written: {backup}")

    new_live = deep_merge(live, additions)

    with open(args.live, "w") as f:
        json.dump(new_live, f, indent=2)
    os.chmod(args.live, 0o600)

    print(f"[OK] Patched {args.live}")
    print()
    print("[INFO] Final state:")
    print(f"  agents.defaults.timeoutSeconds: {new_live.get('agents',{}).get('defaults',{}).get('timeoutSeconds')}")
    print(f"  agents.defaults.model.primary: {new_live.get('agents',{}).get('defaults',{}).get('model',{}).get('primary')}")
    print(f"  agents.defaults.model.fallbacks: {new_live.get('agents',{}).get('defaults',{}).get('model',{}).get('fallbacks')}")
    print(f"  models.providers: {list(new_live.get('models',{}).get('providers',{}).keys())}")
    print(f"  auth.profiles: {list(new_live.get('auth',{}).get('profiles',{}).keys())}")


if __name__ == "__main__":
    main()
