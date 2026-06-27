#!/usr/bin/env python3
"""Migrate auth.profiles in the live openclaw.json from v2026.5.x format
to v2026.5.27+ format.

Migration:
  - 'mode' field is removed in favor of 'type'
  - 'mode' value 'api_key'  → 'type' = 'api_key'
  - 'mode' value 'oauth'    → 'type' = 'oauth'
  - 'mode' value 'aws-sdk'  → 'type' = 'aws-sdk'
  - 'mode' value 'token'    → 'type' = 'token'

Also adds 'models' to custom model providers that lack them (required by
the v2026.5.27+ schema for non-bundled providers).
"""
import argparse
import json
import os
import sys
import time

MODE_TO_TYPE = {
    "api_key": "api_key",
    "oauth": "oauth",
    "aws-sdk": "aws-sdk",
    "token": "token",
}


def migrate(cfg: dict) -> tuple[dict, list[str]]:
    """Migrate a config in-place. Returns (cfg, list_of_changes).

    The v2026.6.10 schema accepts TWO auth profile formats depending on the
    provider type:
      - OAuth providers (e.g. openai-codex): legacy 'mode' field, e.g.
        {"provider": "openai-codex", "mode": "oauth", "email": "..."}
      - API-key providers (e.g. minimax-portal): new 'type' + 'keyRef' format, e.g.
        {"type": "api_key", "provider": "minimax-portal", "keyRef": {...}}

    This script only migrates 'mode' → 'type' for non-OAuth profiles. OAuth
    profiles keep their legacy 'mode' field (the v2026.6.10 schema rejects
    'type' on OAuth profiles).
    """
    changes = []
    auth = cfg.get("auth", {}).get("profiles", {})
    for name, prof in auth.items():
        if not isinstance(prof, dict):
            continue
        provider = prof.get("provider", name.rsplit(":", 1)[0])
        is_oauth = (
            "mode" in prof and prof["mode"] == "oauth"
        ) or provider in ("openai-codex", "openai")

        if "type" in prof and is_oauth:
            # OAuth profile had 'type' added (from a previous incorrect migration);
            # remove it and keep 'mode: oauth' for v2026.6.10 compatibility.
            prof.pop("type", None)
            if "mode" not in prof:
                prof["mode"] = "oauth"
            changes.append(f"  auth.profiles.{name}: removed 'type' (OAuth profile uses legacy 'mode' field)")
        elif "mode" in prof and not is_oauth:
            # API-key profile still on legacy 'mode' field; migrate to 'type'
            old_mode = prof.pop("mode")
            prof["type"] = MODE_TO_TYPE.get(old_mode, "api_key")
            changes.append(f"  auth.profiles.{name}: mode='{old_mode}' → type='{prof['type']}'")

    # Add models to custom providers that lack them
    providers = cfg.get("models", {}).get("providers", {})
    # Chain reference → provider → add the model to that provider
    chain_models = []
    defaults = cfg.get("agents", {}).get("defaults", {})
    if "model" in defaults:
        m = defaults["model"]
        if m.get("primary"):
            chain_models.append(m["primary"])
        chain_models.extend(m.get("fallbacks") or [])
    for entry in chain_models:
        if "/" not in entry:
            continue
        prov, model_id = entry.split("/", 1)
        if prov not in providers:
            continue
        pdata = providers[prov]
        if "models" not in pdata:
            pdata["models"] = []
        existing_ids = {m.get("id") for m in pdata["models"]}
        if model_id not in existing_ids:
            # Default model entry — pick a reasonable api
            api = pdata.get("api", "openai-responses")
            pdata["models"].append({
                "id": model_id,
                "name": model_id,
                "api": api,
                "contextWindow": 262144,
            })
            changes.append(f"  models.providers.{prov}: added model entry for {model_id} (api={api})")

    return cfg, changes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--backup", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with open(args.config) as f:
        cfg = json.load(f)

    cfg, changes = migrate(cfg)

    if not changes:
        print("[OK] No migration needed.")
        sys.exit(0)

    print("[INFO] Migration changes:")
    for c in changes:
        print(c)

    if args.dry_run:
        print("[DRY-RUN] No changes written.")
        sys.exit(0)

    if args.backup:
        ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        backup = f"{args.config}.bak-migrate-{ts}"
        with open(backup, "w") as f:
            with open(args.config) as orig:
                f.write(orig.read())
        print(f"[INFO] Backup written: {backup}")

    with open(args.config, "w") as f:
        json.dump(cfg, f, indent=2)
    os.chmod(args.config, 0o600)
    print(f"[OK] Migrated {args.config}")


if __name__ == "__main__":
    main()
