"""
scripts/cockpit/health.py
─────────────────────────
Live platform health check: VPS status, Service Guard, proxy pool, and fleet readiness.
"""
from __future__ import annotations

import sys
from cockpit.client import default_client


def run_health_check():
    print("=" * 65)
    print(" [COCKPIT] LIVE PLATFORM HEALTH CHECK")
    print(f" Target VPS: {default_client.base_url}")
    print("=" * 65)

    # 1. Base Health
    try:
        health = default_client.get("/health")
        status = health.get("status", "UNKNOWN")
        provider = health.get("provider", "UNKNOWN")
        print(f"[HEALTH] Endpoint Status : {status.upper()} (Provider: {provider})")
    except Exception as exc:
        print(f"[HEALTH] [ERROR] Base endpoint unreachable: {exc}")
        return

    # 2. Authentication Check
    try:
        token = default_client.authenticate()
        print(f"[AUTH]   Admin Session   : VALID (Token: {token[:8]}...{token[-4:]})")
    except Exception as exc:
        print(f"[AUTH]   [ERROR] Login failed: {exc}")
        return

    # 3. Service Guard
    try:
        sg = default_client.get("/api/system/service-guard")
        capsolver = sg.get("capsolver", {})
        proxies = sg.get("proxies", {})
        print(f"[GUARD]  CapSolver Bal   : ${capsolver.get('balance', 0.0):.2f} (Active: {capsolver.get('is_active')})")
        print(f"[GUARD]  Proxy Pool      : {proxies.get('active_count', 0)} Active | {proxies.get('quarantined_count', 0)} Quarantined")
    except Exception as exc:
        print(f"[GUARD]  [WARNING] Service Guard query error: {exc}")

    # 4. Fleet Readiness
    try:
        fleet = default_client.get("/api/gvc/fleet/status")
        gov = fleet.get("governance", {})
        print(f"[FLEET]  Workers Total   : {fleet.get('total_accounts', 0)} Accounts ({gov.get('summary_text', 'N/A')})")
        print(f"[FLEET]  Standby Active  : {fleet.get('active_workers_count', 0)} Pre-Staged: {fleet.get('pre_staged_count', 0)}")
    except Exception as exc:
        print(f"[FLEET]  [WARNING] Fleet status query error: {exc}")

    print("=" * 65)


if __name__ == "__main__":
    run_health_check()
