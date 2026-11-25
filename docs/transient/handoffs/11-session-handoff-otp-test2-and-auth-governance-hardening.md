# Session Handoff 11: OTP Test 2 Verification, Manual OTP Routing & Auth Governance Hardening

> **Date:** 2026-11-25 (Late Evening, PKT)  
> **Branch:** `main` (`671d4e0`, synced with `origin/main`, deployed live on `https://keportal.alamiaconnect.com`)  
> **Status:** `[READY FOR DROP]` — 16/18 active workers authenticated (including `#1 amr.shah@gmail.com`), 20 queued clients, 865/1000 fresh proxies, \$5.77 CapSolver.

---

## 1. Completed Work & Live Verification

### A. Test 2 — Physical Android SMS Forwarder Verification
Monitored live webhook ingestion (`python -u -m cockpit.cli otp watch`) as Kamal Express staff sent real manual GVC-formatted SMS (`GERRYS - This OTP number is valid for 5 mins. The OTP for your GVCW Appointment is: 55123`) from `+923334376174`:
* **14 real SMS intercepted cleanly**, all classified `[GVC OTP]` (`is_gvc=True`) with `CODE=55123` extracted:
  * Active Accounts Passed (13): `#1` (`3345112969`), `#6` (`3000590935`), `#7` (`3455232924`), `#8` (`3331504304`), `#10` (`3325072886`), `#11` (`3239840521`), `#12` (`3357888703`), `#13` (`3295027335`), `#15` (`3088828401`), `#17` (`3259512123`), `#18` (`3461431452`), `#19` (`3498203484`), `#24` (`3486241251`).
  * Extra Client SIM Passed (1): `3329813727` (now assigned to Client `#13 UMER FAROOQ`, resolving the earlier duplicate SIM collision between `#13` and `#14`).
* **Remaining SIMs not yet verified via real SMS (5 active accounts + 1 client):**
  * `#9` (`talha` / `03345496524`)
  * `#20` (`kamal` / `03325594191`)
  * `#21` (`kamal` / `03365043802`)
  * `#22` (`kamal` / `03095043332`)
  * `#23` (`jawad` / `03335567824`)
  * Client `#33 MIAN MIASHAL` (`admin` / `03365705443`)

### B. Manual OTP Fallback Test & Client Routing Audit
* Verified live manual OTP submission: `[2026-11-25 16:31:39] [GVC OTP] SIM=3335567824 CODE=123456 FROM=MANUAL_ENTRY_ADMIN`.
* Audited `agents/appointments/fleet_manager.py` and `agents/appointments/otp.py`:
  * Booking workers wait on `OTP_WAITERS[normalize_phone(target_phone)]` where `target_phone = client.phone_number or acc.get("otp_phone_number")`.
  * Manual submission wakes **only** the exact worker waiting on that normalized 10-digit phone number (no cross-client collision).
  * **Operational Rule:** Staff must always include the client's phone number when submitting a manual OTP; leaving phone blank will not wake a phone-specific booking worker.

### C. Auth Governance & Proxy Burn Bug Fixes (Commit `671d4e0`, Deployed & Verified Live)
While investigating why Account `#1` (`amr.shah@gmail.com`) was still failing auth even after being paused, we uncovered and fixed 3 live production bugs:
1. **Legacy 401 Fallback Bleed (`agents/appointments/portals/gvc.py`):** Removed parameterless `gvc_auth_solver.login_with_credentials()` on HTTP 401 in `search_slots()` which was falling back to legacy `gvc_credentials` (`amr.shah@gmail.com`) with `account_id=None`, bypassing `DISABLED` status and cooldowns. `FleetManager` now re-authenticates the actual owning worker account.
2. **Hard `[GOVERNANCE GUARD]` (`agents/appointments/portals/gvc_auth.py`, `agents/appointments/fleet_manager.py`):** `login_with_credentials()` now resolves `account_id` by email and refuses autonomous login for any `DISABLED`, `PAUSED`, or `BLOCKED` account (`reason: "ACCOUNT_DISABLED"`, zero CapSolver/proxy spend). Manual booking fallback in `fleet_manager.py` now strictly checks `status == 'ACTIVE'` and passes `account_id`.
3. **Imperva WAF Settle Race & Double Proxy Burn (`agents/appointments/portals/gvc_auth.py`):** Replaced premature `h1, title` selectors with real login form selectors (`input[name='username'], input[type='email'], input[name='email'], form, iframe#main-iframe`, 10s timeout) + `1500ms` cookie settle wait, and reused `pending_next_proxy` across WAF retries instead of discarding an extra proxy every attempt.

**Live Post-Deploy Verification (`671d4e0`):**
* Zero login attempts on `amr.shah@gmail.com` for 10 full minutes while `DISABLED` (`16:33:17`–`16:43:26 UTC`).
* Confirmed `pending_next_proxy` reuse (`10334`) and `[PRE-FLIGHT BLOCKED]` (`$0` CapSolver spent) on `10320`.
* After re-enabling `#1` and logging out the conflicting personal browser session, Account `#1` (`amr.shah@gmail.com`) authenticated `[YES]` and is actively `POLLING_SLOTS (12/2026)`.

---

## 2. Key Operational Findings Recorded for Tomorrow

1. **Active External GVCW Browser Session Causes Auth Failure:**
   * Keeping a fleet GVC World account logged in inside a separate personal browser prevents the autonomous worker from authenticating that account. All operators must log out of personal browser sessions for any account active in the fleet.
2. **Deferred Architecture Optimization — Single-Session Playwright Pre-Flight + Login:**
   * `gvc_auth.py` currently opens two separate sync Playwright browsers per attempt (`_preflight_waf_probe_sync` closes Browser #1, discarding Imperva cookies, then `_browser_login_sync` opens Browser #2 after CapSolver).
   * **Why it was built that way:** Historical accretion — `_preflight_waf_probe_sync` was bolted on ahead of async CapSolver using `asyncio.to_thread(sync_playwright)`.
   * **Decision:** Deliberately deferred until **after** tomorrow's drop (do not risk breaking a working 16/18 authenticated fleet at the 11th hour). Unify into a single persistent Playwright session cleanly tomorrow post-drop.

---

## 3. Resume Checklist for Tomorrow
1. Run `python -m cockpit.cli watch --once` to confirm full fleet health (`18/18` authenticated, proxy pool > 500, CapSolver balance > \$5.00).
2. Confirm with staff whether the 5 remaining SIMs (`#9`, `#20`, `#21`, `#22`, `#23`) and `#33` (`03365705443`) have SMS Forwarder active.
3. Launch the live drop watchdog:
   ```powershell
   python -m cockpit.cli watch --interval 30 --auto-recover --otp-alert-seconds 60
   ```
4. Post-drop: implement single-session Playwright unification in `agents/appointments/portals/gvc_auth.py`.
