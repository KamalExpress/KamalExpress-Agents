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


def build_fleet_rollup_data(db_path: Path = DB_PATH) -> Dict[str, Any]:
    """Assemble live Tier-A Fleet & Client Queue rollup telemetry."""
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    try:
        from .fleet_manager import fleet_manager
        fleet_tel = fleet_manager.get_telemetry(owner_username="admin", is_admin=True)
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
        governance = get_gvc_accounts_telemetry_summary(owner_username="admin", is_admin=True, db_path=db_path)
    except Exception:
        governance = {"summary_text": "N/A"}

    clients = get_all_clients(db_path=db_path)
    counts = {"total": len(clients), "QUEUED": 0, "IN_PROGRESS": 0, "BOOKED": 0, "FAILED": 0, "PAUSED": 0}
    client_summaries = []
    for c in clients:
        st = (c.status or "QUEUED").upper()
        counts[st] = counts.get(st, 0) + 1
        steps = get_client_booking_steps(c.id, db_path=db_path) if c.id else []
        last_step = steps[-1] if steps else None
        client_summaries.append({
            "id": c.id,
            "full_name": f"{c.first_name} {c.last_name}".strip(),
            "passport_number": c.passport_number,
            "destination": c.destination,
            "visa_type": c.visa_type,
            "preferred_vac_id": c.vac_id,
            "preferred_vac_label": _vac_label(c.vac_id),
            "status": st,
            "booking_reference": c.booking_reference or "",
            "booked_date": c.booked_date or "",
            "booked_time": c.booked_time or "",
            "booked_by_worker_name": c.booked_by_worker_name or "",
            "last_step_number": last_step["step_number"] if last_step else 0,
            "last_step_name": last_step["step_name"] if last_step else "Queued (Awaiting Slot)",
            "last_step_status": last_step["status"] if last_step else "WAITING",
            "notes": c.notes or "",
        })

    return {
        "generated_at_utc": now_utc,
        "agents": fleet_tel.get("accounts", []),
        "governance": governance,
        "queue_counts": counts,
        "clients": client_summaries,
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
        else:
            default_status = "WAITING" if client.status == "QUEUED" and num <= 3 else "N/A"
            default_summary = "Awaiting open slot discovery" if client.status == "QUEUED" and num == 3 else "—"
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
    filename = f"client_{client_id}_{passport}_{status}.md"
    filepath = reports_dir / filename
    filepath.write_text(md, encoding="utf-8")
    logger.info(f"[reports] Archived client booking report to {filepath}")
    return str(filepath).replace("\\", "/")


def render_all_reports_markdown(db_path: Path = DB_PATH) -> str:
    """Render combined Fleet Rollup + every individual client's 8-step report for bulk printing."""
    rollup = build_fleet_rollup_data(db_path=db_path)
    sections = [render_fleet_rollup_markdown(rollup)]
    for c in rollup.get("clients", []):
        c_data = build_client_report_data(c["id"], db_path=db_path)
        if c_data:
            sections.append("---\n\n" + render_client_report_markdown(c_data))
    return "\n\n".join(sections)
