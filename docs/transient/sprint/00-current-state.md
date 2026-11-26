# Current Sprint State & Active Objectives

> **Current Sprint:** Live Slot-Drop Readiness, Dynamic Multi-Bucket Scouting & Operational Hardening  
> **Last Updated:** 2026-11-26 (`17:15 PKT` / `12:15 UTC` — Live Baseline Sync)  
> **Active Branch:** `main` (`849ade7`, synced with `origin/main` and deployed live via Portainer)

---

## 1. Verified Live System State (`https://keportal.alamiaconnect.com`)
* **Platform & Readiness:** Cockpit Doctor score **`100/100 [READINESS: READY TO STRIKE]`** (`ACTIVE SCANNING`), endpoint `OK` (Cloudflare), admin session `VALID`.
* **Fleet & Auth (21 Accounts Total):**
  * **17 Active & `[AUTHENTICATED]`:**
    * **2 Dynamic Scouts (`SLOT_CHECKER`):** `#10 Bilal Shah 2` (58 scans) and `#13 Imran Nazir` (59 scans), dynamically rotating across all 3 queued client buckets (`VAC 137 / Type 26`, `VAC 137 / Type 5`, `VAC 138 / Type 2`) via shared 3m discovery cache (`7a1f17c`).
    * **15 Pre-Staged Bookers (`HOT_STANDBY`):** `#9`, `#11`, `#12`, `#15`, `#17`, `#18`, `#19`, `#20`, `#21`, `#22`, `#23`, `#24`, `#25`, `#26`, `#27`, all `[YES]` authenticated and ready for cross-bucket wakeup (`7a1f17c`).
  * **4 Paused / Unauthenticated:** `#1` (`amr.shah@gmail.com` / `Tariq Mehmood 1`, intentionally disabled per protected account rule), `#6` (`managerzayan@gmail.com`), `#7` (`appointment.gerrys@gmail.com`), and `#8` (`tm9232565@gmail.com`).
* **Client Queue (24 `QUEUED`, `0` `IN_PROGRESS`, `0` `BOOKED`, `0` `FAILED`):**
  * `VAC 138 (Lahore) / Type 2`: 19 clients (`#13`–`#21`, `#24`–`#26`, `#28`, `#30`, `#32`, `#33`, `#35`, `#36`, `#40`).
  * `VAC 137 (Islamabad) / Type 26`: 4 clients (`#22`, `#23`, `#37`, `#39`).
  * `VAC 137 (Islamabad) / Type 5`: 1 client (`#34 ADEEL AHMAD`).
* **Probed Target Dates (`UNRELEASED` / Expected to Drop, `0` `BOOKED_OUT`, `0` Open):**
  * `VAC 137 / Type 26`: `21/12/2026`, `22/12/2026`.
  * `VAC 137 / Type 5`: `18/12/2026`, `21/12/2026`, `22/12/2026`.
  * `VAC 138 / Type 2`: `03/12`, `04/12`, `10/12`, `11/12`, `17/12`, `18/12`, `21/12`, `22/12`, `24/12`, `25/12`, `31/12/2026`.
* **CapSolver Quota:** **\$5.60** (`Status: OK`), protected by per-email login lock (`33f213c`), pre-flight WAF guard, and hard `[GOVERNANCE GUARD]` blocking disabled/paused accounts (`671d4e0`).
* **Pakistan Residential Proxy Pool:** **878 / 1000** active fresh proxies (`0` quarantined, `122` permanently disabled/retired on WAF challenge). Playwright WAF/auth flows now block heavy static assets and 18.7MB Angular bundles for >95% bandwidth reduction (`728d57b`).
* **Official GVC Center Invariant:** Enforced across backend, UI, and reports: **`137 = Islamabad VAC`**, **`138 = Lahore VAC`** (zero `139` / Karachi references).
* **Persistent Operational Calendar & Target-Date Probing:** `OperationalCalendarService` (`agents/appointments/calendar_service.py`) plus per-client `target_dates`/`target_times` priority probing, Tier-0 claiming, and `CLOSED_WEEKEND` vs `UNRELEASED` vs `BOOKED_OUT` disambiguation (`c8a9ba2`, `92a794d`, `91eda9d`).
* **OTP Wait Window & Rescue:** Extended to 4.5m (`3x90s`, 270s cache TTL) with watchdog stuck-client floor at 6m (`2fa8c41`).
* **Live Agent Monitor & Mission Control Cockpit:** `/agent-monitor` (`api/static/monitor.html`), `cockpit/` CLI, `agents/mcp_server.py` (16 MCP tools), and `/cockpit` skill (`.agents/skills/cockpit/SKILL.md`), with Step 3 probed date/time telemetry (`d04e811`) and `849ade7` monitor fix deployed.

---

## 2. Shipped Commits Since Previous Baseline (`671d4e0` -> `849ade7`)
* `849ade7`: Fix missing module-level `import random` in monitor and remove stray emoji.
* `7a1f17c`: Enable dynamic multi-VAC/multi-type scouting for `SLOT_CHECKER` and cross-bucket wakeup for `BOOKER` workers.
* `728d57b`: Block heavy static assets and 18.7MB Angular bundles in Playwright WAF/auth flows (>95% bandwidth cut).
* `91eda9d`, `92a794d`, `c8a9ba2`: Per-client `target_dates`/`target_times` priority probing, Tier-0 claiming, `CLOSED_WEEKEND` vs `UNRELEASED` vs `BOOKED_OUT` telemetry, and bulk targeting API/CLI.
* `e2915df`, `d04e811`, `dd75e52`: Fix `operational_calendar` import in `trigger_client_booking`, snap stale `blitz_target_date` to valid weekday, and include probed dates/times in Step 3 reports.
* `2fa8c41`: Extend OTP wait window to 4.5m (`3x90s`, 270s cache TTL) and watchdog stuck-client floor to 6m.
* `020d52b`, `dec0c13`, `51377da`, `728b7e4`: Fix `refresh_waf_cookies` URL, in-Playwright `PUT /api/v1/periodslot/slots` fallback on Imperva WAF, full-month worker spread, login proxy IP binding, stale Imperva cookie stripping on rotation, and WAF clearance settle fixes.
* `14fbec9`, `378518e`, `90280e1`, `c516fe2`: Randomized scout (30s–180s) and booker (5s–8s) intervals, auto-restore expired `COOLDOWN` accounts on read/restart, live UTC/PKT top-bar clocks, and auto-disable proxies with >3 failures.

---

## 3. Test Verification Status
* **Full Test Suite (`pytest tests/`):** Passing (`65+` tests across `test_booking_reports_and_flexible_vac.py`, `test_mcp_cockpit.py`, `test_operational_calendar_service.py`, `test_proxy_quarantine.py`, etc.).
* **Live OTP Webhook (Test 1 — Synthetic):** `13 / 13` registered SIM numbers passed (`python -m cockpit.cli otp test-all`).
* **Live OTP Physical Android Forwarder (Test 2 — Real Manual SMS):** 14 real GVC-formatted SMS intercepted (`13` active account SIMs + client `#13 UMER FAROOQ`).
  * **Pending Physical Verification:** Account SIMs `#9`, `#20`, `#21`, `#22`, `#23` (plus newly added accounts `#25`, `#26`, `#27`) and client `#33 MIAN MIASHAL` (plus newly queued clients `#35`–`#40`).
* **Manual OTP Fallback Test:** `[PASSED]` (`SIM=3335567824`, routed strictly by normalized 10-digit client phone number `OTP_WAITERS[clean_phone]`).
* **Documentation Hub:** Synchronized via `python scripts/sync_docs.py` (`docs/docs_data.js`).

---

## 4. Critical Operational Rules, Advisories & Deferred Improvements
1. **No Concurrent Personal Browser GVCW Logins (Confirmed Invariant):**
   * Never keep a fleet GVC World account logged in inside a separate personal browser; active external browser sessions cause GVCW portal auth to fail for that worker account.
2. **Manual OTP Rescue Rule:**
   * Always enter **both** the client's phone number and the OTP code in `/api/otp/submit` (or `cockpit.cli otp submit`). Leaving the phone field blank will not wake a phone-keyed booking worker.
3. **Duplicate Worker Persona Names (Hygiene Advisory):**
   * `Yousaf Bhatti` (`#11`, `#23`), `Rashid Minhas` (`#12`, `#24`), and `Imran Nazir` (`#13`, `#25`) share display names across account pairs; consider disambiguating after the drop for cleaner log filtering.
4. **Deferred Post-Drop Optimization (Do NOT touch before drop):**
   * Unify `_preflight_waf_probe_sync` and `_browser_login_sync` in `agents/appointments/portals/gvc_auth.py` into a single persistent Playwright browser session so Imperva clearance cookies (`incap_ses` / `reese84`) are preserved across the CapSolver solve.

---

## 5. Immediate Next Objectives
1. **Live December Slot-Drop Piloting:**
   * Run `python -m cockpit.cli watch --interval 30 --auto-recover --otp-alert-seconds 60` during the drop window.
   * Monitor `/agent-monitor` for 8-step progression through booking confirmation and printable `.md` archival.
2. **Post-Drop Hardening:**
   * Implement single-session Playwright pre-flight + login unification in `gvc_auth.py` with full test verification.
   * Disambiguate duplicate worker persona names (`#11/#23`, `#12/#24`, `#13/#25`) and complete physical SIM verification on remaining accounts/clients.
