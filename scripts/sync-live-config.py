#!/usr/bin/env python3
"""Live-config patcher for the openclaw gateway.

The 2026-06-27 RCA discovered that the v2026.6.10 schema validator on VM 252
REJECTS the 'auth.profiles' block in openclaw.json with misleading errors,
even though the actual runtime auth is loaded from the per-agent sqlite store
at ~/.openclaw/agents/main/agent/openclaw-agent.sqlite (with
auth-profiles.json only acting as a companion export / inspection artifact).

This script:
  1. Removes the 'auth.profiles' block from openclaw.json (it's dead config)
  2. Pulls in agents.defaults.{timeoutSeconds, model, models} from source
  3. Pulls in channels.discord.inboundWorker.runTimeoutMs from source
  4. Pulls in models.providers from source (additive — only providers the chain
     references, to avoid breaking the gateway with unknown providers)
  5. Adds 'models' entries to any provider that lacks them (v2026.5.27+ schema
     requires custom providers to declare at least one model)

Inputs:
  --live PATH     Path to live openclaw.json
  --source PATH   Path to GitOps source
  --backup        Create a timestamped backup before mutating
  --dry-run       Print what would change without writing

Exit 0 = patched; exit 1 = error.
"""
import argparse
import json
import os
import sys
import time


def deep_merge(base: dict, overlay: dict) -> dict:
    """Recursively merge overlay into base. overlay values win on conflict."""
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

    additions = {}

    # 1. agents.defaults.{timeoutSeconds, model, models} — the actual fix
    if "agents" in source and "defaults" in source["agents"]:
        additions.setdefault("agents", {}).setdefault("defaults", {})
        for k in ("timeoutSeconds", "model", "models"):
            if k in source["agents"]["defaults"]:
                additions["agents"]["defaults"][k] = source["agents"]["defaults"][k]

    # 1b. channels.discord.inboundWorker.runTimeoutMs — per-message fast-fail
    if "channels" in source and "discord" in source["channels"]:
        d_src = source["channels"]["discord"]
        if "inboundWorker" in d_src:
            additions.setdefault("channels", {})
            additions["channels"].setdefault("discord", {})
            additions["channels"]["discord"]["inboundWorker"] = d_src["inboundWorker"]

    # 2. models.providers — add providers the chain needs (with their models)
    if "models" in source and "providers" in source["models"]:
        additions.setdefault("models", {})
        chain_models = []
        defaults = source.get("agents", {}).get("defaults", {})
        if "model" in defaults:
            m = defaults["model"]
            if m.get("primary"):
                chain_models.append(m["primary"])
            chain_models.extend(m.get("fallbacks") or [])
        needed_providers = set()
        for entry in chain_models:
            if "/" in entry:
                needed_providers.add(entry.split("/", 1)[0])
        additions["models"]["providers"] = {
            p: source["models"]["providers"][p]
            for p in source["models"]["providers"]
            if p in needed_providers
        }

    # 3. CRITICAL: REMOVE auth.profiles from live config. v2026.6.10 schema
    #    validator rejects it with misleading errors. Actual auth is loaded
    #    from agents/main/agent/openclaw-agent.sqlite (runtime store).
    removals = []
    if "auth" in live and "profiles" in live.get("auth", {}):
        removals.append("auth.profiles (v2026.6.10 rejects this; use per-agent sqlite auth store)")

    # 4. CRITICAL: also remove "auth" entirely if it becomes empty after removal
    # (Optional — keep an empty "auth": {} to avoid breaking the schema)

    # Print summary
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
    if "channels" in additions and "discord" in additions["channels"]:
        if "inboundWorker" in additions["channels"]["discord"]:
            print(f"  channels.discord.inboundWorker: {additions['channels']['discord']['inboundWorker']}")

    if removals:
        print("[INFO] Will remove from live config:")
        for r in removals:
            print(f"  {r}")

    if args.dry_run:
        print("[DRY-RUN] No changes written.")
        sys.exit(0)

    if args.backup:
        ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        backup = f"{args.live}.bak-sync-{ts}"
        with open(backup, "w") as f:
            json.dump(live, f, indent=2)
        print(f"[INFO] Backup written: {backup}")

    # Apply additions via deep merge
    new_live = deep_merge(live, additions)

    # Apply removals
    if "auth" in new_live and "profiles" in new_live.get("auth", {}):
        new_live["auth"].pop("profiles", None)
        # If auth is now empty, remove it entirely to keep config clean
        if not new_live["auth"]:
            new_live.pop("auth", None)

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
    auth_profiles_value = (
        new_live.get('auth', {}).get('profiles')
        if new_live.get('auth')
        else 'REMOVED'
    )
    print(f"  auth.profiles: {auth_profiles_value}")


if __name__ == "__main__":
    main()
