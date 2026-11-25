# Current Sprint State & Active Objectives

> **Current Sprint:** Live Slot-Drop Readiness, Mission Control Cockpit & Operational Hardening  
> **Last Updated:** 2026-11-25  
> **Active Branch:** `main` (`2374866`, synced with `origin/main` and deployed live via Portainer)

---

## 1. Verified Live System State (`https://keportal.alamiaconnect.com`)
* **Fleet & Auth:** **15 / 16** GVC operator accounts are `ACTIVE`, `[AUTHENTICATED]`, and scanning December 2026 (`12/2026`) with zero errors. Account `#1` (`amr.shah@gmail.com`) is intentionally kept `DISABLED`.
* **Client Queue:** **18** `QUEUED` clients (`#13`–`#33`), all scoped by `owner_username` (`admin`, `talha`, `zeeshan`) with `preferred_date_start = 01/12/2026`.
* **CapSolver Quota:** **\$5.81** (`ok: True`), zero redundant login spend thanks to per-email login lock & session reuse (`33f213c`).
* **Pakistan Residential Proxy Pool:** **438 / 496** active fresh proxies (`0` quarantined, `58` permanently retired). `ProxyManager.ensure_healthy_proxy()` is the sole authority for distinct per-worker proxy allocation (`52f874f`).
* **Official GVC Center Invariant:** Enforced across backend, UI, and reports: **`137 = Islamabad VAC`**, **`138 = Lahore VAC`** (Karachi and `139` completely purged).
* **Persistent Operational Calendar:** `OperationalCalendarService` (`agents/appointments/calendar_service.py`) persists precomputed month calendars in SQLite (`operational_calendar`) and atomic JSON (`data/operational_calendar.json`). Type 26 = Mon–Fri (23 dates in Dec 2026), Type 2 = Thu–Fri (9 dates in Dec 2026), Type 0 = Disabled.
* **Live Agent Monitor & Reports:** `/agent-monitor` (`api/static/monitor.html`) linked from main dashboard, showing live Tier-A Fleet Rollup, Coverage Gap Warning Banner, 60s Staff-Scoped OTP Latency Alerts, and Tier-B Per-Client 8-Step Reports with timestamped `.md` downloads and `data/reports/` archival.
* **Mission Control Cockpit:** Dedicated `cockpit/` CLI (`health`, `doctor`, `watch`, `fleet`, `logs`, `slots`, `proxies`, `clients`, `report`, `otp`), `agents/mcp_server.py` (16 MCP tools), and `/cockpit` skill (`.agents/skills/cockpit/SKILL.md`).

---

## 2. Test Verification Status
* **Full Test Suite (`pytest tests/`):** **65+ tests passing** (including `test_mcp_cockpit.py`, `test_booking_reports_and_flexible_vac.py`, `test_operational_calendar_service.py`, `test_proxy_quarantine.py`).
* **Live OTP Webhook (Test 1):** `13 / 13` registered SIM numbers passed (`python -m cockpit.cli otp test-all`).
* **Documentation Hub:** Synchronized via `python scripts/sync_docs.py` (`docs/docs_data.js`).

---

## 3. Immediate Next Objectives
1. **Test 2 (Physical Android SMS Forwarder Verification):**
   * Verify real GVC-formatted manual SMS (`GERRYS - The OTP for your GVCW Appointment is: 55123`) sent to staff Android phones arrives as `[GVC OTP]` via `python -m cockpit.cli otp watch`.
2. **Live December Slot-Drop Piloting:**
   * Run `python -m cockpit.cli watch --interval 30 --auto-recover --otp-alert-seconds 60` during drop window.
   * Monitor `/agent-monitor` for 8-step progression through booking confirmation and printable `.md` archival.
3. **Optional Staff Review:**
   * Coverage Gap Banner currently advises that Client `#28 FAKHAR JUNAIZ` prefers `VAC 137 (Islamabad) / Type 2` while 0 operators are assigned to `137 / Type 2` (covered via Flexible VAC fallback if `138 / Type 2` opens).
