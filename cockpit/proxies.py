"""
cockpit/proxies.py
──────────────────
Live proxy pool inspector, quarantine reset, and bulk import tool.
"""
from __future__ import annotations

import argparse
import sys
from cockpit.client import default_client


def run_inspect_proxies():
    print("=" * 85)
    print(" [COCKPIT] RESIDENTIAL PROXY POOL CATALOG")
    print("=" * 85)

    try:
        res = default_client.get("/api/proxies")
        stats = res.get("stats", {})
        proxies = res.get("proxies", [])

        print(f" Total: {stats.get('total', len(proxies))} | Active: {stats.get('active', 0)} | Quarantined: {stats.get('quarantined', 0)} | Disabled: {stats.get('disabled', 0)}")
        print("-" * 85)
        print(f"{'ID':<6} {'HOST:PORT':<26} {'CC':<4} {'STATUS':<12} {'FAILS':<6} {'LAST ERROR / NOTES'}")
        print("-" * 85)

        for p in proxies:
            pid = f"#{p.get('id', '-')}"
            host_port = f"{p.get('host', '-')}:{p.get('port', '-')}"
            cc = p.get("country", "PK")
            status = p.get("status", "ACTIVE")
            fails = p.get("fail_count", 0)
            err = (p.get("quarantine_reason") or p.get("last_error") or "—")[:32]
            print(f"{pid:<6} {host_port:<26} {cc:<4} {status:<12} {fails:<6} {err}")

        print("=" * 85)
    except Exception as exc:
        print(f"[ERROR] Failed to inspect proxy pool: {exc}")


def quarantine_dirty_proxies():
    print("=" * 70)
    print(" [COCKPIT] QUARANTINING WAF-FLAGGED PROXIES")
    print("=" * 70)
    try:
        res = default_client.get("/api/proxies")
        proxies = res.get("proxies", [])
        flagged = [
            p for p in proxies
            if "incapsula" in (p.get("last_error") or "").lower()
            or "waf" in (p.get("last_error") or "").lower()
            or p.get("fail_count", 0) > 0
        ]
        if not flagged:
            print("[INFO] No dirty or WAF-flagged proxies detected.")
            return

        for p in flagged:
            pid = p.get("id")
            q_res = default_client.post(f"/api/proxies/{pid}/quarantine", json={"duration_minutes": 120, "reason": "Flagged by Imperva Incapsula WAF challenge"})
            print(f"[QUARANTINE] Proxy #{pid} ({p.get('host')}:{p.get('port')}) quarantined for 120m.")
        print("[SUCCESS] All flagged proxies isolated from active rotation.")
    except Exception as exc:
        print(f"[ERROR] Failed to quarantine proxies: {exc}")
    print("=" * 70)


def reset_cooldowns():
    print("=" * 70)
    print(" [COCKPIT] RESETTING ALL PROXY COOLDOWNS")
    print("=" * 70)
    try:
        res = default_client.post("/api/proxies/reset-cooldowns")
        if res.get("success"):
            print(f"[SUCCESS] {res.get('message', 'Reset proxies back to active.')}")
        else:
            print(f"[WARNING] Reset response: {res}")
    except Exception as exc:
        print(f"[ERROR] Failed to reset cooldowns: {exc}")
    print("=" * 70)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Live proxy pool inspector.")
    parser.add_argument("--reset", action="store_true", help="Reset all quarantined proxies back to active")
    args = parser.parse_args()

    if args.reset:
        reset_cooldowns()
    else:
        run_inspect_proxies()
