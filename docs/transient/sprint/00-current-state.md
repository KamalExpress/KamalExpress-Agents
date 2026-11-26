# Current Sprint State & Active Objectives

> **Current Sprint:** Centralized Unified Booking Engine, Atomic 8-Step Guardrails & Live Staging Verification  
> **Last Updated:** 2026-11-26 (`19:28 PKT` / `14:28 UTC` — Pre-Merge Verified Baseline)  
> **Active Branch:** `feat/unified-booking-engine` (`1833911`, deployed & verified live on Portainer; ready to merge into `main`)  
> **Production Rollback Anchor:** `main` (`849ade7`, untouched) | **Isolated Experimental Branch:** `standalone-flow` (`a24266b`, untouched)

---

## 1. Verified Live System State (`https://keportal.alamiaconnect.com`)
* **Platform & Readiness:** Cockpit Doctor score **`100/100 [READINESS: READY TO STRIKE]`** (`ACTIVE SCANNING`), zero JWT isolation violations, zero active proxy warnings, endpoint `OK` (Cloudflare), admin session `VALID`.
* **Centralized Architecture Live (`1833911`):**
  * Single booking pipeline (`agents/appointments/booking_engine.py:execute_booking()`) shared by `_loop()`, `trigger_client_booking()`, `quick_book_slot()`, and `monitor.py`.
  * Single Stealth Playwright + Proxy + Bandwidth-Saver engine (`agents/appointments/portals/playwright_client.py`) shared by `gvc.py` (search, OTP, submit, cookie refresh) and `gvc_auth.py` (pre-flight, login).
  * Strict per-worker JWT/cookie/proxy isolation (`GVCPortalDriver(strict_isolation=True)`) verified live: `#6` (`bound_jwt_subject='Gulmuhammad85'`) and `#12` (`bound_jwt_subject='tayyab16287@gmail.com'`) hold distinct JWTs with zero leakage to paused `#15` (`None`).
  * Mode-aware scan pacing (`scan_mode`: `normal` vs `drop_window`) and `priority_plus_periodic_sweep` date strategy live-verified via `python -m cockpit.cli fleet --scan-mode drop_window|normal`.
* **Live Proxy Pool:** **`13 / 20` Active** (3 `gr.decodo.com` sticky 60m + 10 `gate.decodo.com:10001..10010`), `0` quarantined, `7` disabled. Confirmed Decodo Greece proxies require `user-<user>-sessionduration-60` sticky credentials to pass Imperva login cleanly.
* **CapSolver Quota:** **\$5.58** (`Status: OK`).
* **Client Queue:** **24 `QUEUED`**, `0` `IN_PROGRESS`, `0` `BOOKED`, `0` `FAILED` across `VAC 138 / Type 2`, `VAC 137 / Type 26`, and `VAC 137 / Type 5`.
* **Live Background Scout (`#13 Imran Nazir`):** Active `POLLING_SLOTS (12/2026)` (`Scans=10`), discovering both `UNRELEASED` (`21/12`, `22/12`, `24/12`, `25/12`) and `BOOKED_OUT` (`03/12`, `04/12`, `31/12`) dates via `priority_plus_periodic_sweep`.

---

## 2. Verification Status
* **Full Test Suite (`pytest tests/ -q`):** **`78 / 78` passing (`100%`)**, including:
  * AST undefined-name gate across `agents/appointments/` and `cockpit/`
  * Strict multi-worker JWT/proxy isolation, `base64` JWT subject decode, actual-slot `FALLBACK_VAC` precedence, zero WAF cache poisoning, atomic slot reservation + `<100ms` `asyncio.Event` booker wakeup, fresh-only `min_timestamp` OTP capture, Playwright WAF fallback on OTP & submit, transient error re-queueing
  * **1-Worker Golden 8-Step E2E** and **5-Worker Simultaneous Scale E2E** (`tests/test_golden_worker_e2e.py`)
* **Live OTP Webhook (`python -m cockpit.cli otp test-all`):** **`24 / 24` registered SIMs `[PASSED]` (`100%`, `GVC? = YES`)**.
* **Live Parallel Book Now (`SAFE_MODE`):** Client `#40` (`#6 Daniel Gill`) and Client `#39` (`#15 Tariq Mehmood 2`) executed concurrently without collision, `\$0.00` CapSolver spend, `0` OTP spam.
* **Detailed Changelog:** See [12-session-handoff-unified-booking-engine-and-atomic-guardrails.md](file:///e:/Alamia/KamalExpress-Agents/docs/transient/handoffs/12-session-handoff-unified-booking-engine-and-atomic-guardrails.md).
* **Documentation Hub:** Synchronized via `python scripts/sync_docs.py` (`docs/docs_data.js`).

---

## 3. Critical Operational Rules & Next Objectives
1. **Sticky Proxy Credentials Rule:** Always ingest Decodo residential proxies with `user-<user>-sessionduration-60` so Imperva Incapsula sessions do not break across login/slot queries.
2. **Pre-Drop Checklist:**
   * Expand active sticky residential proxy pool (50–100+ ports) before the next monthly drop window.
   * Switch to `python -m cockpit.cli fleet --scan-mode drop_window` (`5s` negative cache, `3–6s` scout jitter, `0.3–0.6s` date spacing) before the drop window and restore `--scan-mode normal` afterward.
   * Run `python -m cockpit.cli doctor` to confirm `100/100` with distinct `bound_jwt_subject` per active worker.
3. **Immediate Next Step:**
   * Merge `feat/unified-booking-engine` into `main`, push `origin/main`, and switch Portainer back to `refs/heads/main`.
