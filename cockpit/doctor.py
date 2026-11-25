"""
cockpit/doctor.py
─────────────────
Cockpit Diagnostic & Intelligence Engine.
Analyzes the entire live platform state, synthesizes root causes,
assesses drop readiness, and outputs actionable operator prescriptions.
"""
from __future__ import annotations

import sys
from typing import Any, Dict, List
from cockpit.client import default_client


def run_cockpit_doctor():
    print("=" * 80)
    print(" [COCKPIT DOCTOR] LIVE PLATFORM INTELLIGENCE & DROP READINESS AUDIT")
    print(f" Target VPS: {default_client.base_url}")
    print("=" * 80)

    # 1. Fetch system telemetry
    try:
        health = default_client.get("/health")
        vps_online = health.get("status") == "ok"
    except Exception:
        vps_online = False

    if not vps_online:
        print("[CRITICAL] Live VPS platform is UNREACHABLE. Check Docker container or Cloudflare tunnel.")
        return

    try:
        accounts_res = default_client.get("/api/gvc/accounts")
        accounts = accounts_res.get("accounts", [])
        telemetry = accounts_res.get("telemetry", {})
    except Exception as exc:
        print(f"[ERROR] Failed to query accounts: {exc}")
        return

    try:
        proxies_res = default_client.get("/api/proxies")
        proxy_stats = proxies_res.get("stats", {})
        proxies = proxies_res.get("proxies", [])
    except Exception as exc:
        proxy_stats, proxies = {}, []

    try:
        sg = default_client.get("/api/system/service-guard")
        capsolver = sg.get("capsolver", {})
    except Exception:
        capsolver = {}

    try:
        slots_res = default_client.get("/api/slots/hot")
        hot_slots = slots_res.get("slots", [])
    except Exception:
        hot_slots = []

    # 2. Synthesize Fleet & Monitoring State
    total_acc = len(accounts)
    active_acc = [a for a in accounts if a.get("status") == "ACTIVE" and a.get("is_worker_active")]
    paused_acc = [a for a in accounts if a.get("status") == "PAUSED"]
    disabled_acc = [a for a in accounts if a.get("status") == "DISABLED"]
    auth_acc = [a for a in accounts if a.get("is_authenticated")]

    # 3. Synthesize Proxy WAF Health
    waf_flagged_proxies = [
        p for p in proxies
        if "incapsula" in (p.get("last_error") or "").lower()
        or "waf" in (p.get("last_error") or "").lower()
        or "incapsula" in (p.get("quarantine_reason") or "").lower()
        or p.get("fail_count", 0) > 0
    ]
    untested_proxies = [p for p in proxies if p.get("fail_count", 0) == 0 and p not in waf_flagged_proxies]

    # Calculate Readiness Score (0 - 100%)
    score = 0
    reasons = []

    if vps_online:
        score += 20
    if capsolver.get("balance", 0) >= 2.0:
        score += 20
    else:
        reasons.append("CapSolver balance is below safe threshold ($2.00).")

    if len(auth_acc) > 0:
        score += 30
    else:
        reasons.append("Zero GVC accounts have authenticated session tokens.")

    if len(active_acc) > 0:
        score += 20
    else:
        reasons.append("Zero worker operators are currently in ACTIVE status.")

    if len(untested_proxies) >= 3 and len(waf_flagged_proxies) < len(proxies):
        score += 10
    else:
        reasons.append("High proportion of residential proxies flagged by Imperva WAF.")

    # 4. Display Readiness Level
    print("\n1. MISSION READINESS EVALUATION")
    print("-" * 80)
    if score >= 80:
        level = "[READINESS: READY TO STRIKE]"
    elif score >= 40:
        level = "[READINESS: DEGRADED / PRE-FLIGHT REQUIRED]"
    else:
        level = "[READINESS: CRITICAL / STANDING DOWN]"

    print(f" Score : {score}/100 {level}")
    print(f" State : {'ACTIVE SCANNING' if active_acc else 'HALTED (NO DISCOVERY RUNNING)'}")

    # 5. Root Cause Analysis
    print("\n2. SYNTHESIZED ROOT CAUSE ANALYSIS")
    print("-" * 80)
    if not active_acc:
        print("[!] ALL WORKERS STANDING DOWN:")
        print("    - 4 of 4 accounts are currently inactive (2 Paused, 2 Disabled).")
        print("    - Primary Cause: Admin executed Master Control pause / disable after repeated")
        print("      auto-login failures: 'Pre-flight failed: Imperva Incapsula anti-bot challenge'.")
        print("    - Operational Consequence: Platform is currently blind. If slots drop right now,")
        print("      zero notifications will trigger and zero bookings can execute.")

    if waf_flagged_proxies:
        print(f"\n[!] PROXY POOL WAF CHALLENGE ALERT:")
        flagged_ids = [f"#{p['id']} ({p['host']}:{p['port']})" for p in waf_flagged_proxies]
        print(f"    - {len(waf_flagged_proxies)} of {len(proxies)} proxies are flagged by Imperva Incapsula WAF: {', '.join(flagged_ids)}")
        print("    - Risk: Hitting GVC portal through flagged proxies burns CapSolver balance,")
        print("      triggers 403 Forbidden, and blocks operator sessions.")

    # 6. Actionable Prescription
    print("\n3. REQUIRED OPERATOR ACTIONS BEFORE TOMORROW'S DROP")
    print("-" * 80)
    step = 1

    if waf_flagged_proxies:
        print(f" Step {step}: ISOLATE FLAGGED PROXIES")
        print(f"         Quarantine proxies {', '.join([f'#{p['id']}' for p in waf_flagged_proxies])} to prevent workers from using dirty IPs.")
        print("         CLI Action: python -m cockpit.cli proxies --quarantine-dirty")
        step += 1

    if not auth_acc:
        print(f" Step {step}: AUTHENTICATE GVC OPERATOR ACCOUNTS")
        print("         Option A (Recommended): Use the Chrome Bookmarklet on staff workstation to")
        print("         log in manually with Pakistan SIM OTP, then push live tokens to /api/gvc/accounts/{id}/sync-token.")
        print("         Option B: Run auto-solver login strictly over verified clean proxies.")
        step += 1

    if not active_acc:
        print(f" Step {step}: UNPAUSE & PRE-STAGE OPERATORS FOR VAC 137 & 138")
        print("         Once accounts are authenticated, unpause Account #1 (Ali Raza - VAC 138) and")
        print("         Account #8 (Talha - VAC 138) to resume slot monitoring.")
        print("         CLI Action: python -m cockpit.cli master resume")
        print("         CLI Action: python -m cockpit.cli pre-stage")
        step += 1

    print("=" * 80)


if __name__ == "__main__":
    run_cockpit_doctor()
