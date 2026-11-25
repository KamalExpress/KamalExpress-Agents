"""
scripts/cockpit/fleet.py
────────────────────────
Live fleet overview: Inspect all GVC accounts, worker personas, assigned VACs, and error telemetry.
"""
from __future__ import annotations

import sys
from cockpit.client import default_client


def run_fleet_overview():
    print("=" * 80)
    print(" [COCKPIT] GVC WORKER FLEET STATUS")
    print("=" * 80)

    try:
        data = default_client.get("/api/gvc/fleet/status")
    except Exception as exc:
        print(f"[ERROR] Failed to fetch fleet status: {exc}")
        return

    accounts = data.get("accounts", [])
    if not accounts:
        print("[INFO] No GVC operator accounts registered.")
        return

    print(f"{'ID':<4} {'LABEL':<14} {'PERSONA':<16} {'VAC':<6} {'VISA':<6} {'STATUS':<10} {'AUTH':<8} {'PHONE'}")
    print("-" * 80)
    for acc in accounts:
        acc_id = acc.get("account_id", "-")
        label = (acc.get("account_label") or "")[:13]
        persona = (acc.get("worker_persona_name") or "")[:15]
        vac = acc.get("vac_id", "-")
        visa = acc.get("visa_type", "-")
        status = acc.get("last_status", "STOPPED")
        is_auth = "[YES]" if acc.get("is_authenticated") else "[NO]"
        phone = acc.get("otp_phone_masked", "N/A")
        print(f"{acc_id:<4} {label:<14} {persona:<16} {vac:<6} {visa:<6} {status:<10} {is_auth:<8} {phone}")

        last_err = acc.get("last_error")
        if last_err and status != "RUNNING":
            print(f"     [DIAGNOSTIC] Last Error: {last_err[:70]}...")

    print("-" * 80)
    gov = data.get("governance", {})
    print(f" Governance: {gov.get('summary_text', 'N/A')} | Total: {data.get('total_accounts', 0)}")
    print("=" * 80)


if __name__ == "__main__":
    run_fleet_overview()
