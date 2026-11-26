"""
agents/appointments/reports.py
──────────────────────────────
Live & Archived Booking Report Generator (JSON + Printable Markdown).

Produces:
  1. Tier A: Fleet & Queue Rollup Report (active agents + overall queue status).
  2. Tier B: Individual Per-Client 8-Step Booking Lifecycle Report.
  3. Permanent Markdown archival under data/reports/ for client printing.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .db import (
    DB_PATH,
    get_all_clients,
    get_client_booking_steps,
    get_client_by_id,
    get_gvc_accounts_telemetry_summary,
    get_proxy_stats,
    mask_phone_pii,
)
from .portals.gvc import GVC_VACS, GVC_VISA_TYPES

logger = logging.getLogger(__name__)

REPORTS_DIR = Path("data/reports")

CANONICAL_STEPS = [
    (1, "Governance & Pre-Flight"),
    (2, "Session / Auth"),
    (3, "Slot Availability Check"),
    (4, "Booking Slot Selection"),
    (5, "Client Queue Claim"),
    (6, "OTP Dispatch & Capture"),
    (7, "Captcha & Booking Submit"),
    (8, "Confirmation Capture"),
]


def _vac_label(vac_id: Optional[str]) -> str:
    vid = str(vac_id or "").strip()
    if not vid:
        return "Any VAC"
    meta = GVC_VACS.get(vid)
    if meta:
        return f"VAC {vid} ({meta.get('city', meta.get('name', vid))})"
    return f"VAC {vid}"


def _visa_label(visa_type: Optional[str]) -> str:
    vt = str(visa_type or "26").strip()
    desc = GVC_VISA_TYPES.get(vt, f"Type {vt}")
    return f"Type {vt}: {desc}"


def _format_utc_time(iso_str: Optional[str]) -> str:
    if not iso_str:
        return "—"
    s = str(iso_str).replace("Z", "")
    if "T" in s:
        return s.split("T", 1)[1][:8]
    if " " in s:
        return s.split(" ", 1)[1][:8]
    return s[:8]


def build_fleet_rollup_data(
    owner_username: str = "admin",
    is_admin: bool = True,
    db_path: Path = DB_PATH,
) -> Dict[str, Any]:
    """Assemble live Tier-A Fleet & Client Queue rollup telemetry (staff-scoped unless admin)."""
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    try:
        from .fleet_manager import fleet_manager
        fleet_tel = fleet_manager.get_telemetry(owner_username=owner_username, is_admin=is_admin)
    except Exception:
        fleet_tel = {"accounts": [], "active_workers_count": 0, "authenticated_accounts_count": 0}

    try:
        from .service_guard import service_guard
        sg = service_guard.get_status()
    except Exception:
        sg = {"capsolver": {"balance": 0.0, "ok": False}, "can_proceed": True}

    try:
        proxy_stats = get_proxy_stats(db_path=db_path)
    except Exception:
        proxy_stats = {"total": 0, "active": 0, "quarantined": 0, "disabled": 0}

    try:
        governance = get_gvc_accounts_telemetry_summary(owner_username=owner_username, is_admin=is_admin, db_path=db_path)
    except Exception:
        governance = {"summary_text": "N/A"}

    clients = get_all_clients(owner_username=owner_username, is_admin=is_admin, db_path=db_path)
    counts = {"total": len(clients), "QUEUED": 0, "IN_PROGRESS": 0, "BOOKED": 0, "FAILED": 0, "PAUSED": 0}
    fleet_accounts = fleet_tel.get("accounts", [])
    sg_ok = bool(sg.get("can_proceed", True))
    client_summaries = []
    for c in clients:
        st = (c.status or "QUEUED").upper()
        counts[st] = counts.get(st, 0) + 1
        steps = get_client_booking_steps(c.id, db_path=db_path) if c.id else []
        last_step = steps[-1] if steps else None

        if last_step:
            step_num = last_step["step_number"]
            step_name = last_step["step_name"]
            step_status = last_step["status"]
        else:
            # Synthesize real live step (1-4) from fleet & Service Guard telemetry (no "Step 0")
            matching = [
                a for a in fleet_accounts
                if str(a.get("visa_type")) == str(c.visa_type) and a.get("is_authenticated") and a.get("is_worker_active")
            ]
            if not matching:
                matching = [a for a in fleet_accounts if a.get("is_authenticated") and a.get("is_worker_active")]

            if matching:
                scans = max(int(a.get("total_scans") or 0) for a in matching)
                if scans > 0:
                    step_num, step_name, step_status = 3, "Slot Availability Check (Awaiting Open Slot)", "PASSED"
                else:
                    step_num, step_name, step_status = 2, "Session / Auth", "PASSED"
            else:
                step_num = 1
                step_name = "Governance & Pre-Flight"
                step_status = "WAITING" if sg_ok else "BLOCKED"

        client_summaries.append({
            "id": c.id,
            "full_name": f"{c.first_name} {c.last_name}".strip(),
            "passport_number": c.passport_number,
            "owner_username": getattr(c, "owner_username", "admin") or "admin",
            "phone_masked": mask_phone_pii(c.phone_number),
            "preferred_date_start": c.preferred_date_start or "",
            "destination": c.destination,
            "visa_type": c.visa_type,
            "preferred_vac_id": c.vac_id,
            "preferred_vac_label": _vac_label(c.vac_id),
            "status": st,
            "booking_reference": c.booking_reference or "",
            "booked_date": c.booked_date or "",
            "booked_time": c.booked_time or "",
            "booked_by_worker_name": c.booked_by_worker_name or "",
            "last_step_number": step_num,
            "last_step_name": step_name,
            "last_step_status": step_status,
            "notes": c.notes or "",
        })

    # Compute staff-scoped Step 6 OTP latency alerts (>= 60s wait on IN_PROGRESS clients)
    otp_alerts = []
    now_dt_utc = datetime.now(timezone.utc)
    for c in clients:
        if (c.status or "").upper() != "IN_PROGRESS" or not c.id:
            continue
        steps = get_client_booking_steps(c.id, db_path=db_path)
        has_otp_passed = any(s.get("step_number") == 6 and s.get("status") == "PASSED" for s in steps)
        if has_otp_passed:
            continue
        # Measure elapsed wait since Step 5 (Client Queue Claim) or earliest step
        ref_step = next((s for s in reversed(steps) if s.get("step_number") in (5, 6)), None)
        if not ref_step or not ref_step.get("created_at"):
            continue
        try:
            ref_dt = datetime.fromisoformat(str(ref_step["created_at"]).replace("Z", "+00:00"))
            if ref_dt.tzinfo is None:
                ref_dt = ref_dt.replace(tzinfo=timezone.utc)
            wait_sec = int((now_dt_utc - ref_dt).total_seconds())
        except Exception:
            continue
        if wait_sec >= 60:
            owner = getattr(c, "owner_username", "admin") or "admin"
            masked = mask_phone_pii(c.phone_number)
            otp_alerts.append({
                "client_id": c.id,
                "full_name": f"{c.first_name} {c.last_name}".strip(),
                "owner_username": owner,
                "notify_targets": [owner, "admin"] if owner != "admin" else ["admin"],
                "phone_masked": masked,
                "wait_seconds": wait_sec,
                "message": (
                    f"[OTP LATENCY ALERT -> Notify: {owner} & admin] Client #{c.id} "
                    f"({c.first_name} {c.last_name}) awaiting OTP on {masked} for {wait_sec}s. "
                    f"Submit manually via Dashboard OTP tab or 'python -m cockpit.cli otp submit'."
                ),
            })

    # Compute coverage-gap advisories (queued clients whose preferred VAC + visa_type has 0 active authenticated scouts)
    # Any active authenticated SLOT_CHECKER dynamically scouts all queued (vac_id, visa_type) pairs.
    has_universal_checker = any(
        a.get("is_authenticated")
        and a.get("is_worker_active")
        and str(a.get("account_role", "HYBRID")).upper() == "SLOT_CHECKER"
        for a in fleet_accounts
    )
    covered_pairs = {
        (str(a.get("vac_id") or ""), str(a.get("visa_type") or ""))
        for a in fleet_accounts
        if a.get("is_authenticated") and a.get("is_worker_active")
    }
    queued_by_pair: Dict[tuple[str, str], int] = {}
    for c in clients:
        if (c.status or "QUEUED").upper() == "QUEUED":
            pair = (str(c.vac_id or "137"), str(c.visa_type or "26"))
            queued_by_pair[pair] = queued_by_pair.get(pair, 0) + 1

    coverage_gaps = []
    for (q_vac, q_visa), q_count in sorted(queued_by_pair.items()):
        if not has_universal_checker and (q_vac, q_visa) not in covered_pairs:
            coverage_gaps.append({
                "vac_id": q_vac,
                "vac_label": _vac_label(q_vac),
                "visa_type": q_visa,
                "visa_label": _visa_label(q_visa),
                "queued_clients_count": q_count,
                "message": (
                    f"{q_count} queued client(s) prefer {_vac_label(q_vac)} / Type {q_visa}, "
                    f"but 0 active authenticated operators are currently assigned to that center+visa. "
                    f"(Flexible VAC fallback will still cover them if another center opens for Type {q_visa}.)"
                ),
            })

    return {
        "generated_at_utc": now_utc,
        "agents": fleet_accounts,
        "date_states": fleet_tel.get("date_states", {}),
        "governance": governance,
        "queue_counts": counts,
        "clients": client_summaries,
        "coverage_gaps": coverage_gaps,
        "otp_alerts": otp_alerts,
        "capsolver": sg.get("capsolver", {}),
        "proxy_stats": proxy_stats,
        "service_guard_ok": sg.get("can_proceed", True),
    }


def build_client_report_data(client_id: int, db_path: Path = DB_PATH) -> Optional[Dict[str, Any]]:
    """Assemble live Tier-B per-client 8-step lifecycle report data."""
    client = get_client_by_id(client_id, db_path=db_path)
    if not client:
        return None

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    raw_steps = get_client_booking_steps(client_id, db_path=db_path)

    # Map latest recorded entry per step number 1..8
    latest_by_num: Dict[int, dict] = {}
    actual_vac_id = None
    match_tier = ""
    assigned_worker = client.booked_by_worker_name or ""
    assigned_account_id = client.booked_by_account_id

    for entry in raw_steps:
        num = int(entry.get("step_number", 0))
        if 1 <= num <= 8:
            latest_by_num[num] = entry
        if entry.get("worker_name") and not assigned_worker:
            assigned_worker = entry["worker_name"]
        if entry.get("account_id") and not assigned_account_id:
            assigned_account_id = entry["account_id"]
        det = entry.get("details") or {}
        if det.get("actual_vac_id"):
            actual_vac_id = str(det["actual_vac_id"])
        elif det.get("vac_id") and num in (4, 5, 7, 8):
            actual_vac_id = str(det["vac_id"])
        if det.get("match_tier"):
            match_tier = str(det["match_tier"])

    # For QUEUED clients without per-client step rows yet, synthesize live Steps 1-3 from real DB & fleet telemetry
    live_step_overrides: Dict[int, dict] = {}
    client_unreleased_dates: List[str] = []
    client_booked_out_dates: List[str] = []
    try:
        from .fleet_manager import fleet_manager as _fm_ds
        pair_key = f"VAC {client.vac_id or '138'} / Type {client.visa_type or '26'}"
        pair_states = (_fm_ds.get_date_states(vac_id=str(client.vac_id or "138"), visa_type=str(client.visa_type or "26")) or {}).get(pair_key, {})
        client_unreleased_dates = sorted(d for d, info in pair_states.items() if info.get("state") == "UNRELEASED")
        client_booked_out_dates = sorted(d for d, info in pair_states.items() if info.get("state") == "BOOKED_OUT")
    except Exception:
        pass

    if client.status == "QUEUED" and not latest_by_num:
        try:
            from .fleet_manager import fleet_manager
            from .service_guard import service_guard
            from .calendar_service import operational_calendar

            sg = service_guard.get_status()
            pstats = get_proxy_stats(db_path=db_path)
            cap_bal = float((sg.get("capsolver") or {}).get("balance", 0.0))
            sg_ok = bool(sg.get("can_proceed", True))
            type_enabled = operational_calendar.is_visa_type_enabled(client.visa_type or "26")
            valid_dates, target_month = operational_calendar.resolve_worker_scan_dates(
                visa_type=client.visa_type or "26",
                fallback_start_date=client.preferred_date_start,
                max_dates=0,
            )
            halt_reason = sg.get("halt_reason") or ""

            # Step 1: Real Governance & Pre-Flight telemetry (target-month calendar, not "today")
            s1_ok = sg_ok and type_enabled and len(valid_dates) > 0 and pstats.get("active", 0) > 0
            s1_err = (
                halt_reason
                or ("" if type_enabled else f"Visa Type {client.visa_type} disabled by policy")
                or ("" if valid_dates else f"No valid operational dates for Type {client.visa_type} in {target_month}")
                or ("" if pstats.get("active", 0) > 0 else "No active proxies in pool")
            )
            live_step_overrides[1] = {
                "status": "PASSED" if s1_ok else "BLOCKED",
                "time_utc": _format_utc_time(sg.get("last_checked_at") or now_utc),
                "summary": f"CapSolver ${cap_bal:.2f}, Proxies {pstats.get('active', 0)}/{pstats.get('total', 0)}, Type {client.visa_type}: {len(valid_dates)} valid date(s) in {target_month or 'target month'}",
                "error_message": s1_err or "None",
            }

            # Step 2 & 3: Real active agent covering this client's visa type
            fleet_accounts = fleet_manager.get_telemetry(owner_username="admin", is_admin=True).get("accounts", [])
            matching_agents = [
                a for a in fleet_accounts
                if str(a.get("visa_type")) == str(client.visa_type) and a.get("is_authenticated") and a.get("is_worker_active")
            ]
            if not matching_agents:
                matching_agents = [a for a in fleet_accounts if a.get("is_authenticated") and a.get("is_worker_active")]

            if matching_agents:
                agent = matching_agents[0]
                if not assigned_worker:
                    assigned_worker = f"{agent.get('worker_persona_name')} (Scanning)"
                if not assigned_account_id:
                    assigned_account_id = agent.get("account_id")

                live_step_overrides[2] = {
                    "status": "PASSED",
                    "time_utc": _format_utc_time(agent.get("last_checked") or now_utc),
                    "summary": f"Operator '{agent.get('worker_persona_name')}' (#{agent.get('account_id')}, {agent.get('email')}) authenticated & active",
                    "error_message": agent.get("last_error") or "None",
                }
                scans = int(agent.get("total_scans") or 0)
                checked_dates = agent.get("last_checked_dates") or []
                last_probe_date = agent.get("last_checked_date") or (checked_dates[-1] if checked_dates else None)
                checked_times = agent.get("last_checked_times") or []
                if len(checked_dates) > 1:
                    date_desc = f"{checked_dates[0]}..{checked_dates[-1]} ({len(checked_dates)} dates, last: {last_probe_date})"
                elif last_probe_date:
                    date_desc = str(last_probe_date)
                else:
                    date_desc = agent.get("target_date_from") or "Pending"
                times_desc = ", ".join(checked_times[:5]) if checked_times else "None open"
                if not client_unreleased_dates and agent.get("unreleased_dates"):
                    client_unreleased_dates = list(agent.get("unreleased_dates") or [])
                if not client_booked_out_dates and agent.get("booked_out_dates"):
                    client_booked_out_dates = list(agent.get("booked_out_dates") or [])
                unrel_desc = f" | Unreleased (Expected to Drop): {', '.join(client_unreleased_dates)}" if client_unreleased_dates else ""
                booked_desc = f" | Booked-Out: {', '.join(client_booked_out_dates)}" if client_booked_out_dates else ""
                live_step_overrides[3] = {
                    "status": "PASSED" if scans > 0 else "WAITING",
                    "time_utc": _format_utc_time(agent.get("last_checked")),
                    "summary": (
                        f"Scans={scans} on VAC {agent.get('vac_id')} (Type {agent.get('visa_type')}) | "
                        f"Date(s): {date_desc} | Slot Time(s): {times_desc}{unrel_desc}{booked_desc} | {agent.get('last_status', 'IDLE')}"
                    ),
                    "error_message": agent.get("last_error") or "None",
                }
            else:
                live_step_overrides[2] = {
                    "status": "BLOCKED",
                    "time_utc": "—",
                    "summary": f"No active authenticated operator assigned for Type {client.visa_type}",
                    "error_message": "All operators for this visa type are unauthenticated or paused/disabled",
                }
                live_step_overrides[3] = {
                    "status": "BLOCKED",
                    "time_utc": "—",
                    "summary": "Slot scanning paused until an operator is authenticated",
                    "error_message": "No active authenticated operator",
                }
        except Exception as live_err:
            logger.debug(f"[reports] Live telemetry synthesis fallback for client #{client_id}: {live_err}")

    normalized_steps = []
    for num, canonical_name in CANONICAL_STEPS:
        rec = latest_by_num.get(num)
        if rec:
            normalized_steps.append({
                "step_number": num,
                "step_name": canonical_name,
                "status": rec["status"],
                "time_utc": _format_utc_time(rec.get("created_at")),
                "summary": rec.get("summary") or "—",
                "error_message": rec.get("error_message") or "None",
                "details": rec.get("details") or {},
            })
        elif num in live_step_overrides:
            ov = live_step_overrides[num]
            normalized_steps.append({
                "step_number": num,
                "step_name": canonical_name,
                "status": ov["status"],
                "time_utc": ov["time_utc"],
                "summary": ov["summary"],
                "error_message": ov["error_message"],
                "details": {},
            })
        else:
            default_status = "WAITING" if client.status == "QUEUED" and num == 4 else "N/A"
            default_summary = "Awaiting genuine open slot (id>0, capacity>0)" if client.status == "QUEUED" and num == 4 else "—"
            normalized_steps.append({
                "step_number": num,
                "step_name": canonical_name,
                "status": default_status,
                "time_utc": "—",
                "summary": default_summary,
                "error_message": "None",
                "details": {},
            })

    return {
        "generated_at_utc": now_utc,
        "client": {
            "id": client.id,
            "full_name": f"{client.first_name} {client.last_name}".strip(),
            "passport_number": client.passport_number,
            "phone_masked": mask_phone_pii(client.phone_number),
            "destination": client.destination,
            "visa_type": client.visa_type,
            "visa_label": _visa_label(client.visa_type),
            "preferred_vac_id": client.vac_id,
            "preferred_vac_label": _vac_label(client.vac_id),
            "actual_vac_id": actual_vac_id or "",
            "actual_vac_label": _vac_label(actual_vac_id) if actual_vac_id else "Pending",
            "match_tier": match_tier or "Pending",
            "status": client.status,
            "booking_reference": client.booking_reference or "",
            "booked_date": client.booked_date or "",
            "booked_time": client.booked_time or "",
            "target_dates": getattr(client, "target_dates", "") or "",
            "target_times": getattr(client, "target_times", "") or "",
            "unreleased_dates": client_unreleased_dates,
            "booked_out_dates": client_booked_out_dates,
            "raw_confirmation_path": client.raw_confirmation_path or "",
            "notes": client.notes or "",
        },
        "assigned_worker": assigned_worker or "Unassigned",
        "assigned_account_id": assigned_account_id,
        "steps": normalized_steps,
        "history_count": len(raw_steps),
    }


def render_fleet_rollup_markdown(data: Dict[str, Any]) -> str:
    """Render Tier-A Fleet & Queue Rollup as clean, printable Markdown (zero emojis)."""
    counts = data.get("queue_counts", {})
    cap = data.get("capsolver", {})
    pstats = data.get("proxy_stats", {})
    agents = data.get("agents", [])
    clients = data.get("clients", [])

    lines = [
        f"# Fleet & Queue Rollup Report — {data.get('generated_at_utc', '')}",
        "",
        "## 1. Queue & Resource Summary",
        "",
        "| Total Clients | QUEUED | IN_PROGRESS | BOOKED | FAILED | CapSolver Balance | Healthy Proxies |",
        "| :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
        f"| {counts.get('total', 0)} | {counts.get('QUEUED', 0)} | {counts.get('IN_PROGRESS', 0)} | "
        f"{counts.get('BOOKED', 0)} | {counts.get('FAILED', 0)} | "
        f"${float(cap.get('balance', 0.0)):.2f} | {pstats.get('active', 0)}/{pstats.get('total', 0)} |",
        "",
        "## 2. Active Agents",
        "",
        "| Account ID | Persona | Email | Target | Status | Auth | Scans | Last Check |",
        "| :---: | :--- | :--- | :--- | :--- | :---: | :---: | :--- |",
    ]

    for a in agents:
        auth_tag = "[YES]" if a.get("is_authenticated") else "[NO]"
        target = f"VAC {a.get('vac_id', '-')} / Type {a.get('visa_type', '-')}"
        last_chk = a.get("last_checked") or "Never"
        lines.append(
            f"| #{a.get('account_id')} | {a.get('worker_persona_name', '-')} | {a.get('email', '-')} | "
            f"{target} | `{a.get('last_status', 'IDLE')}` | `{auth_tag}` | {a.get('total_scans', 0)} | {last_chk} |"
        )

    gaps = data.get("coverage_gaps", [])
    if gaps:
        lines.extend(["", "## Coverage Gap Advisories (For Staff Review)", ""])
        for g in gaps:
            lines.append(f"- **[ADVISORY]** {g['message']}")

    date_states = data.get("date_states") or {}
    if date_states:
        lines.extend([
            "",
            "## Probed Date States (Unreleased / Expected to Drop vs Booked-Out)",
            "",
            "| Center / Visa Type | Unreleased (Expected to Drop) | Booked-Out (Full) | Open (Live Slots) |",
            "| :--- | :--- | :--- | :--- |",
        ])
        for pair_key, dates_map in sorted(date_states.items()):
            if not isinstance(dates_map, dict):
                continue
            unrel = sorted(d for d, info in dates_map.items() if info.get("state") == "UNRELEASED")
            bkd = sorted(d for d, info in dates_map.items() if info.get("state") == "BOOKED_OUT")
            opn = sorted(d for d, info in dates_map.items() if info.get("state") == "OPEN")
            lines.append(
                f"| **{pair_key}** | {', '.join(unrel) if unrel else '—'} | "
                f"{', '.join(bkd) if bkd else '—'} | {', '.join(opn) if opn else '—'} |"
            )

    lines.extend([
        "",
        "## 3. Client Queue Status",
        "",
        "| Client ID | Applicant Name | Passport | Preferred VAC | Visa Type | Status | Last Step | Reference |",
        "| :---: | :--- | :--- | :--- | :---: | :--- | :--- | :--- |",
    ])
    for c in clients:
        lines.append(
            f"| #{c['id']} | {c['full_name']} | `{c['passport_number']}` | {c['preferred_vac_label']} | "
            f"Type {c['visa_type']} | `[{c['status']}]` | Step {c['last_step_number']}: {c['last_step_name']} | "
            f"{c['booking_reference'] or '—'} |"
        )

    lines.append("")
    return "\n".join(lines)


def render_client_report_markdown(data: Dict[str, Any]) -> str:
    """Render Tier-B Per-Client 8-Step Booking Report as clean, printable Markdown (zero emojis)."""
    c = data["client"]
    lines = [
        f"# Client Booking Report — Client #{c['id']}: {c['full_name']} ({c['passport_number']})",
        "",
        "| Field | Value |",
        "| :--- | :--- |",
        f"| **Generated At** | `{data.get('generated_at_utc', '')}` |",
        f"| **Applicant** | `#{c['id']}` — {c['full_name']} (Passport: `{c['passport_number']}`, SIM: `{c['phone_masked']}`) |",
        f"| **Target Visa** | {c['destination']} — {c['visa_label']} |",
        f"| **VAC Preference** | Preferred: `{c['preferred_vac_label']}` / Actual Slot VAC: `{c['actual_vac_label']}` (Match: `{c['match_tier']}`) |",
        f"| **Assigned Agent** | {data.get('assigned_worker', 'Unassigned')} (Account: `#{data.get('assigned_account_id') or '—'}`) |",
        f"| **Overall Status** | `[{c['status']}]` |",
        f"| **Booking Reference** | `{c['booking_reference'] or 'Pending'}` |",
        f"| **Booked Slot** | `{(c['booked_date'] + ' ' + c['booked_time']).strip() if c['booked_date'] else 'Pending'}` |",
        f"| **Target Dates / Times** | `{c.get('target_dates') or 'Auto'} / {c.get('target_times') or 'Any'}` |",
        f"| **Unreleased (Expected to Drop)** | `{', '.join(c.get('unreleased_dates') or []) or 'None observed yet'}` |",
        f"| **Booked-Out Dates** | `{', '.join(c.get('booked_out_dates') or []) or 'None observed yet'}` |",
        "",
        "## 8-Step Booking Lifecycle",
        "",
        "| # | Step | Status | Time (UTC) | Output / Evidence | Issues / Failure Reason |",
        "| :-: | :--- | :--- | :--- | :--- | :--- |",
    ]

    for s in data.get("steps", []):
        lines.append(
            f"| {s['step_number']} | **{s['step_name']}** | `[{s['status']}]` | `{s['time_utc']}` | "
            f"{s['summary']} | {s['error_message']} |"
        )

    lines.extend([
        "",
        "## Notes & Confirmation Artifact",
        "",
        f"* **Confirmation File:** `{c['raw_confirmation_path'] or 'Not generated yet'}`",
        f"* **Operator Notes:** {c['notes'] or 'None'}",
        "",
    ])
    return "\n".join(lines)


def archive_client_report_markdown(client_id: int, db_path: Path = DB_PATH, reports_dir: Path = REPORTS_DIR) -> str:
    """Persist a permanent printable Markdown report for a client under data/reports/."""
    data = build_client_report_data(client_id, db_path=db_path)
    if not data:
        return ""
    md = render_client_report_markdown(data)
    reports_dir.mkdir(parents=True, exist_ok=True)
    passport = "".join(ch for ch in (data["client"].get("passport_number") or "NOPASS") if ch.isalnum())
    status = (data["client"].get("status") or "STATUS").upper()
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"client_{client_id}_{passport}_{status}_{ts}.md"
    filepath = reports_dir / filename
    filepath.write_text(md, encoding="utf-8")
    logger.info(f"[reports] Archived client booking report to {filepath}")
    return str(filepath).replace("\\", "/")


def render_all_reports_markdown(
    owner_username: str = "admin",
    is_admin: bool = True,
    db_path: Path = DB_PATH,
) -> str:
    """Render combined Fleet Rollup + every accessible client's 8-step report for bulk printing."""
    rollup = build_fleet_rollup_data(owner_username=owner_username, is_admin=is_admin, db_path=db_path)
    sections = [render_fleet_rollup_markdown(rollup)]
    for c in rollup.get("clients", []):
        c_data = build_client_report_data(c["id"], db_path=db_path)
        if c_data:
            sections.append("---\n\n" + render_client_report_markdown(c_data))
    return "\n\n".join(sections)
