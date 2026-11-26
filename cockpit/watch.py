"""
cockpit/watch.py
────────────────
Proactive Mission Control Watchdog & Safe Auto-Remediation Loop.

Monitors the live Kamal Express VPS every N seconds for:
  1. Stalled active authenticated workers (>5m stale last_checked or 0 scans)
  2. Proxy burn-rate spikes (>=20 disabled in 10m) and healthy pool floor (<50)
  3. CapSolver burn-rate spikes (>$0.50 in 10m) and low balance floor (<$2.00)
  4. Session expiry / auth failure alerts
  5. Step 6 OTP latency alerts (>=60s default), routing to client owner staff + admin
  6. Stuck IN_PROGRESS client recovery (>5m locked -> auto-revert to QUEUED with --auto-recover)
  7. Automatic incident & outcome archival (BOOKED / FAILED) under logs/cockpit_incidents/
  8. Queue & coverage hygiene advisories (missing start dates, uncovered VAC+Visa pairs)
"""
from __future__ import annotations

import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Deque, Dict, List, Optional, Tuple

import httpx
from cockpit.client import default_client

INCIDENTS_DIR = Path("logs/cockpit_incidents")


def _parse_utc(ts_str: Optional[str]) -> Optional[datetime]:
    if not ts_str:
        return None
    clean = str(ts_str).strip().replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.fromisoformat(clean) if fmt is None else datetime.strptime(clean, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            continue
    return None


def _archive_incident(tag: str, client_id: Optional[int], reason: str, logs: List[dict]) -> str:
    INCIDENTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    fname = f"incident_{tag}_{f'client_{client_id}_' if client_id else ''}{ts}.md"
    out = INCIDENTS_DIR / fname

    lines = [
        f"# Cockpit Incident Snapshot — [{tag}] ({ts} UTC)",
        "",
        f"* **Reason:** {reason}",
        f"* **Client ID:** {client_id or 'N/A'}",
        "",
    ]

    if client_id:
        try:
            token = default_client.authenticate()
            r = httpx.get(
                f"{default_client.base_url}/api/reports/clients/{client_id}.md",
                headers={"Authorization": f"Bearer {token}"},
                timeout=15.0,
            )
            if r.status_code == 200:
                lines.append(r.text)
                lines.append("\n---\n")
        except Exception:
            pass

    lines.extend(["## Recent System Logs", "", "```text"])
    for l in logs[:25]:
        lines.append(f"[{l.get('time', '')}] [{l.get('level', '')}] [{l.get('category', '')}] ({l.get('worker_name') or '-'}): {l.get('message', '')}")
    lines.append("```")

    out.write_text("\n".join(lines), encoding="utf-8")
    return str(out).replace("\\", "/")


def run_watchdog(
    interval_seconds: int = 30,
    once: bool = False,
    auto_recover: bool = False,
    otp_alert_seconds: int = 60,
    stalled_worker_seconds: int = 300,
    stuck_client_seconds: int = 360,
    proxy_floor: int = 50,
    capsolver_floor: float = 2.0,
) -> Dict[str, List[str]]:
    """Run proactive watchdog checks once or continuously."""
    print("=" * 90)
    print(
        f" [COCKPIT WATCHDOG] Target: {default_client.base_url} | "
        f"Mode: {'ONE-SHOT' if once else f'POLL {interval_seconds}s'} | "
        f"OTP Alert: >={otp_alert_seconds}s | Auto-Recover: {'ON' if auto_recover else 'OFF'}"
    )
    print("=" * 90)

    # Sliding 10-minute history windows: (epoch_ts, value)
    proxy_disabled_hist: Deque[Tuple[float, int]] = deque(maxlen=60)
    capsolver_hist: Deque[Tuple[float, float]] = deque(maxlen=60)
    archived_terminal_clients: set[Tuple[int, str]] = set()
    in_progress_first_seen: Dict[int, float] = {}

    last_findings: Dict[str, List[str]] = {"alerts": [], "advisories": [], "actions": []}

    while True:
        now_epoch = time.time()
        now_utc = datetime.now(timezone.utc)
        alerts: List[str] = []
        advisories: List[str] = []
        actions: List[str] = []

        try:
            fleet = default_client.get("/api/reports/fleet")
            logs = default_client.get("/api/logs/stream", params={"limit": 35}).get("logs", [])
        except Exception as exc:
            print(f"[{now_utc.strftime('%H:%M:%S')}] [CRITICAL] Watchdog API unreachable: {exc}")
            if once:
                return {"alerts": [f"API unreachable: {exc}"], "advisories": [], "actions": []}
            time.sleep(interval_seconds)
            continue

        # 1. CapSolver balance & burn-rate guard
        cap_bal = float((fleet.get("capsolver") or {}).get("balance", 0.0))
        capsolver_hist.append((now_epoch, cap_bal))
        while capsolver_hist and now_epoch - capsolver_hist[0][0] > 600:
            capsolver_hist.popleft()
        if cap_bal < capsolver_floor:
            alerts.append(f"[CAPSOLVER LOW] Balance is ${cap_bal:.2f} (below safe floor ${capsolver_floor:.2f}).")
        if len(capsolver_hist) >= 2:
            burn = capsolver_hist[0][1] - cap_bal
            if burn > 0.50:
                alerts.append(f"[CAPSOLVER BURN] Spent ${burn:.2f} in the last 10m (check for login loops).")

        # 2. Proxy pool floor & burn-rate guard
        pstats = fleet.get("proxy_stats") or {}
        p_active = int(pstats.get("active", 0))
        p_disabled = int(pstats.get("disabled", 0))
        p_total = int(pstats.get("total", 0))
        proxy_disabled_hist.append((now_epoch, p_disabled))
        while proxy_disabled_hist and now_epoch - proxy_disabled_hist[0][0] > 600:
            proxy_disabled_hist.popleft()
        if p_total > 0 and p_active < proxy_floor:
            alerts.append(f"[PROXY FLOOR] Only {p_active}/{p_total} fresh proxies remaining (floor: {proxy_floor}). Import fresh proxies.")
        if len(proxy_disabled_hist) >= 2:
            p_delta = p_disabled - proxy_disabled_hist[0][1]
            if p_delta >= 20:
                alerts.append(f"[PROXY BURN SPIKE] {p_delta} proxies disabled in the last 10m (possible WAF wave).")

        # 3. Stalled active authenticated worker detector
        agents = fleet.get("agents") or []
        for a in agents:
            if not (a.get("is_worker_active") and a.get("is_authenticated")):
                continue
            scans = int(a.get("total_scans") or 0)
            last_dt = _parse_utc(a.get("last_checked"))
            persona = a.get("worker_persona_name") or f"Account #{a.get('account_id')}"
            if scans == 0 and last_dt is None:
                advisories.append(f"[WORKER WARMING] '{persona}' (#{a.get('account_id')}) authenticated with 0 scans completed yet.")
            elif last_dt is not None:
                age_s = int((now_utc - last_dt).total_seconds())
                if age_s > stalled_worker_seconds:
                    alerts.append(f"[STALLED WORKER] '{persona}' (#{a.get('account_id')}) last checked {age_s}s ago (>{stalled_worker_seconds}s).")

        # 4. Session expiry / recent error check
        for l in logs[:15]:
            msg = l.get("message") or ""
            lvl = (l.get("level") or "").upper()
            if lvl == "ERROR" or "SESSION_EXPIRED" in msg or "Authentication failed" in msg:
                alerts.append(f"[LOG {lvl}] ({l.get('worker_name') or '-'}): {msg[:110]}")

        # 5. Server-computed OTP latency alerts (>= 60s, routed to owner staff + admin)
        for oa in fleet.get("otp_alerts") or []:
            alerts.append(oa.get("message", "[OTP LATENCY ALERT]"))

        # 6. Client queue inspection: stuck IN_PROGRESS recovery, terminal archival, missing start dates
        clients = fleet.get("clients") or []
        missing_start_ids = []
        for cl in clients:
            cid = cl.get("id")
            st = (cl.get("status") or "").upper()
            owner = cl.get("owner_username") or "admin"
            full = cl.get("full_name") or f"Client #{cid}"

            if st == "QUEUED" and not cl.get("preferred_date_start"):
                missing_start_ids.append(f"#{cid}")

            if st in ("BOOKED", "FAILED") and (cid, st) not in archived_terminal_clients:
                archived_terminal_clients.add((cid, st))
                saved_path = _archive_incident(st, cid, f"Client #{cid} ({full}, owner={owner}) reached {st}", logs)
                actions.append(f"[ARCHIVED {st}] Saved incident/report snapshot for #{cid} ({full}) -> {saved_path}")

            if st == "IN_PROGRESS":
                if cid not in in_progress_first_seen:
                    in_progress_first_seen[cid] = now_epoch
                held_s = int(now_epoch - in_progress_first_seen[cid])
                if held_s >= otp_alert_seconds:
                    alerts.append(
                        f"[OTP / BOOKING WAIT -> Notify: {owner} & admin] Client #{cid} ({full}) "
                        f"in IN_PROGRESS for {held_s}s (SIM {cl.get('phone_masked', '-')}). "
                        f"Manual rescue: python -m cockpit.cli otp submit --phone <SIM> --code <OTP>"
                    )
                if held_s >= stuck_client_seconds:
                    if auto_recover:
                        try:
                            full_cl = default_client.get(f"/api/clients/{cid}")
                            full_cl["status"] = "QUEUED"
                            full_cl["notes"] = ((full_cl.get("notes") or "") + f" | Auto-recovered by Cockpit after {held_s}s stuck IN_PROGRESS.").strip(" |")
                            r = default_client.request("PUT", f"/api/clients/{cid}", json=full_cl)
                            if r.get("success"):
                                actions.append(f"[AUTO-RECOVERED] Client #{cid} ({full}, owner={owner}) reverted IN_PROGRESS -> QUEUED after {held_s}s.")
                                in_progress_first_seen.pop(cid, None)
                        except Exception as rec_err:
                            alerts.append(f"[RECOVERY FAILED] Could not revert Client #{cid}: {rec_err}")
                    else:
                        alerts.append(f"[STUCK CLIENT] Client #{cid} ({full}, owner={owner}) stuck IN_PROGRESS for {held_s}s (run with --auto-recover to revert).")
            else:
                in_progress_first_seen.pop(cid, None)

        if missing_start_ids:
            advisories.append(f"[MISSING START DATE] {len(missing_start_ids)} queued client(s) have no preferred_date_start: {', '.join(missing_start_ids[:10])}")

        # 7. Coverage gap advisories (read-only for staff review)
        for g in fleet.get("coverage_gaps") or []:
            advisories.append(f"[COVERAGE GAP] {g.get('message')}")

        # Print concise tick report
        q = fleet.get("queue_counts") or {}
        active_auth = sum(1 for a in agents if a.get("is_worker_active") and a.get("is_authenticated"))
        status_tag = "[ALERT]" if alerts else ("[ADVISORY]" if advisories else "[HEALTHY]")
        print(
            f"\n[{now_utc.strftime('%H:%M:%S UTC')}] {status_tag} "
            f"AuthWorkers={active_auth}/{len(agents)} | "
            f"Queue: Q={q.get('QUEUED', 0)} IP={q.get('IN_PROGRESS', 0)} B={q.get('BOOKED', 0)} F={q.get('FAILED', 0)} | "
            f"Proxies={p_active}/{p_total} | CapSolver=${cap_bal:.2f}"
        )
        for item in alerts:
            print(f"  {item}")
        for item in advisories:
            print(f"  {item}")
        for item in actions:
            print(f"  {item}")
        if not alerts and not advisories and not actions:
            print("  [OK] Zero anomalies detected. All active operators scanning cleanly.")

        last_findings = {"alerts": alerts, "advisories": advisories, "actions": actions}
        if once:
            break
        try:
            time.sleep(interval_seconds)
        except KeyboardInterrupt:
            print("\n[INFO] Cockpit watchdog stopped.")
            break

    return last_findings
