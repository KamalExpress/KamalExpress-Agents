# Current Sprint State & Active Objectives

> **Current Sprint:** Live Slot-Drop Readiness, Mission Control Cockpit & Operational Hardening  
> **Last Updated:** 2026-11-25 (End of Day — Pre-Drop)  
> **Active Branch:** `main` (`671d4e0`, synced with `origin/main` and deployed live via Portainer)

---

## 1. Verified Live System State (`https://keportal.alamiaconnect.com`)
* **Fleet & Auth:** **16 / 18** GVC operator accounts are `ACTIVE`, `[AUTHENTICATED]`, and scanning December 2026 (`12/2026`) — including Account `#1` (`amr.shah@gmail.com` / `Tariq Mehmood 1`, which authenticated cleanly after `671d4e0` and logging out of the conflicting personal browser session). Only 2 accounts (`#15 Tariq Mehmood 2`, `#20 perosonal`) are in routine WAF cooldown.
* **Client Queue:** **20** `QUEUED` clients across `admin` (3), `jawad` (2), `kamal` (5), `talha` (4), and `zeeshan` (6), all with `preferred_date_start = 01/12/2026`. Duplicate SIM collision on `#13`/`#14` resolved (`#13 UMER FAROOQ` updated to verified SIM `3329813727`).
* **CapSolver Quota:** **\$5.77** (`ok: True`), protected by per-email login lock (`33f213c`), pre-flight WAF guard, and hard `[GOVERNANCE GUARD]` blocking disabled/paused accounts (`671d4e0`).
* **Pakistan Residential Proxy Pool:** **865 / 1000** active fresh proxies (`0` quarantined, `135` permanently retired). `ProxyManager.ensure_healthy_proxy()` is the sole authority for distinct per-worker proxy allocation (`52f874f`), and WAF login retries now reuse `pending_next_proxy` without double-burn (`671d4e0`).
* **Official GVC Center Invariant:** Enforced across backend, UI, and reports: **`137 = Islamabad VAC`**, **`138 = Lahore VAC`** (Karachi and `139` completely purged).
* **Persistent Operational Calendar:** `OperationalCalendarService` (`agents/appointments/calendar_service.py`) persists precomputed month calendars in SQLite (`operational_calendar`) and atomic JSON (`data/operational_calendar.json`). Type 26 = Mon–Fri (23 dates in Dec 2026), Type 2 = Thu–Fri (9 dates in Dec 2026), Type 0 = Disabled.
* **Live Agent Monitor & Reports:** `/agent-monitor` (`api/static/monitor.html`) linked from main dashboard, showing live Tier-A Fleet Rollup, Coverage Gap Warning Banner, 60s Staff-Scoped OTP Latency Alerts, and Tier-B Per-Client 8-Step Reports with timestamped `.md` downloads and `data/reports/` archival.
* **Mission Control Cockpit:** Dedicated `cockpit/` CLI (`health`, `doctor`, `watch`, `fleet`, `logs`, `slots`, `proxies`, `clients`, `report`, `otp`), `agents/mcp_server.py` (16 MCP tools), and `/cockpit` skill (`.agents/skills/cockpit/SKILL.md`).

---

## 2. Test Verification Status
* **Full Test Suite (`pytest tests/`):** **65+ tests passing** (`test_booking_reports_and_flexible_vac.py`, `test_mcp_cockpit.py`, `test_operational_calendar_service.py`, `test_proxy_quarantine.py`).
* **Live OTP Webhook (Test 1 — Synthetic):** `13 / 13` registered SIM numbers passed (`python -m cockpit.cli otp test-all`).
* **Live OTP Physical Android Forwarder (Test 2 — Real Manual SMS):** **14 real GVC-formatted SMS intercepted** (`[GVC OTP]`, `CODE=55123`, `FROM=+923334376174`):
  * **Verified Active Account SIMs (13):** `#1` (`3345112969`), `#6` (`3000590935`), `#7` (`3455232924`), `#8` (`3331504304`), `#10` (`3325072886`), `#11` (`3239840521`), `#12` (`3357888703`), `#13` (`3295027335`), `#15` (`3088828401`), `#17` (`3259512123`), `#18` (`3461431452`), `#19` (`3498203484`), `#24` (`3486241251`), plus client `#13 UMER FAROOQ` SIM (`3329813727`).
  * **Pending Physical Verification (5 active accounts + 1 client):** `#9` (`03345496524`), `#20` (`03325594191`), `#21` (`03365043802`), `#22` (`03095043332`), `#23` (`03335567824`), and Client `#33 MIAN MIASHAL` (`03365705443`).
* **Manual OTP Fallback Test:** `[PASSED]` (`SIM=3335567824`, `CODE=123456`, `FROM=MANUAL_ENTRY_ADMIN`). Routed strictly by normalized 10-digit client phone number (`OTP_WAITERS[clean_phone]`).
* **Documentation Hub:** Synchronized via `python scripts/sync_docs.py` (`docs/docs_data.js`).

---

## 3. Critical Operational Rules & Deferred Improvements
1. **No Concurrent Personal Browser GVCW Logins (Confirmed Invariant):**
   * Never keep a fleet GVC World account logged in inside a separate personal browser; active external browser sessions cause GVCW portal auth to fail for that worker account.
2. **Manual OTP Rescue Rule:**
   * Always enter **both** the client's phone number and the OTP code in `/api/otp/submit` (or `cockpit.cli otp submit`). Leaving the phone field blank will not wake a phone-keyed booking worker.
3. **Deferred Post-Drop Optimization (Do NOT touch before drop):**
   * Unify `_preflight_waf_probe_sync` and `_browser_login_sync` in `agents/appointments/portals/gvc_auth.py` into a single persistent Playwright browser session so Imperva clearance cookies (`incap_ses` / `reese84`) are preserved across the CapSolver solve. (Originally split only because sync Playwright pre-flight was bolted on ahead of async CapSolver.)

---

## 4. Immediate Next Objectives (Tomorrow's Drop)
1. **Pre-Drop Check:**
   * Run `python -m cockpit.cli watch --once` and confirm `18/18` active workers authenticated (verify `#15` and `#20` recovered from cooldown) and check if the 5 remaining Android forwarder SIMs were tested.
2. **Live December Slot-Drop Piloting:**
   * Run `python -m cockpit.cli watch --interval 30 --auto-recover --otp-alert-seconds 60` during the drop window.
   * Monitor `/agent-monitor` for 8-step progression through booking confirmation and printable `.md` archival.
3. **Post-Drop Hardening:**
   * Implement single-session Playwright pre-flight + login unification in `gvc_auth.py` with full test verification.
