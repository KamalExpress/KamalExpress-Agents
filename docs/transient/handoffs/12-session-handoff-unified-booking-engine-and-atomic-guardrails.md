# Session Handoff 12: Centralized Unified Booking Engine & Atomic 8-Step Guardrails

> **Date:** 2026-11-26  
> **Feature Branch:** `feat/unified-booking-engine` (`1833911`, branched cleanly from `main` at `849ade7`)  
> **Branch Hygiene:** `main` (`849ade7`) and `standalone-flow` (`a24266b`) untouched during development and live staging validation.

---

## 1. Post-Drop Forensic Root Causes Resolved

Following the 2026-11-26 live slot drop, a zero-assumption audit of all 8 booking steps uncovered 10+ structural bugs caused primarily by duplicated execution paths across `fleet_manager.py`, `monitor.py`, `gvc.py`, `gvc_auth.py`, and `otp.py`. Every bug was resolved **once** in centralized, reusable modules:

1. **Broken Per-Worker Session Isolation:** `AccountWorkerInstance._loop()` never bound each account's own JWT/cookies/proxy onto `self._driver`, causing all workers to fall back to the legacy single-row `gvc_sessions` table.
2. **Missing `import base64` in `gvc.py`:** Silently broke `_get_token_subject()` inside `submit_booking()`, causing `applicant.email` to be sent instead of the JWT subject email.
3. **WAF Negative-Cache Poisoning:** `WAF_CHALLENGE`, `RATE_LIMITED`, and `ERROR` responses fell through to `slot_cache.set(..., slots=[], ttl=180)`, locking all scouts out of a bucket for 3 minutes after a failed probe.
4. **4–14 Minute Blind Windows:** 180s negative cache + 30–180s random sleep + 2.5s inter-date sleep blinded the 2-scout fleet during 30–90s drops.
5. **Target-Dates-Only Blindness:** Exclusive probing of 2–3 guessed `target_dates` missed other December drop dates (`03/12`, `04/12`, `24/12`, `31/12` confirmed `BOOKED_OUT` once full-month sweep ran).
6. **N-Booker Slot Collision on `slots[0]`:** Concurrent bookers raced on `slots[0]` because `available_capacity -= 1` only occurred after the 90s OTP wait.
7. **3–8s Booker Wakeup Latency:** Bookers polled `slot_cache` via sleep instead of instant event-driven wakeup.
8. **Stale OTP Recycling:** `wait_for_otp(..., max_age_seconds=270.0)` could return a pre-trigger stale OTP or recycle an expired attempt-1 OTP on retry attempt 2/3.
9. **`FALLBACK_VAC` Mismatch in `submit_booking()`:** Checked `applicant.vac_id` before the actual open slot's `vac_id`, mismatching `periodslotid`.
10. **Missing Playwright WAF Fallback on OTP & Booking Submit:** Only `search_slots()` had in-browser Playwright WAF fallback; `trigger_booking_otp()` and `submit_booking()` relied solely on `curl_cffi`.
11. **Permanent `FAILED` on Transient Drop Errors:** `Slot Taken` / `WAF` / timeout errors permanently marked clients `FAILED` instead of reverting to `QUEUED`.

---

## 2. Exact File-by-File Changes (`1833911`)

### New Centralized Modules
* **`agents/appointments/portals/playwright_client.py` `[NEW]`:**
  * Single reusable Stealth Playwright + Proxy + Bandwidth-Saver (`>95%` traffic cut) engine: `parse_proxy_for_playwright()`, `install_bandwidth_saver_route()`, `probe_origin_waf()`, `refresh_origin_cookies()`, and `run_stealth_browser_fetch()`.
  * Eliminated 4 duplicated Playwright launch/stealth/proxy blocks across `gvc.py` and `gvc_auth.py`.
* **`agents/appointments/booking_engine.py` `[NEW]`:**
  * Single reusable `execute_booking()` function implementing Steps 4–8 (concurrent reCAPTCHA v2, `min_timestamp` fresh-only 3-attempt OTP capture, actual-slot VAC submission, ARN capture, raw confirmation persistence, 8-step lifecycle recording, `.md` report archival, and `is_transient_booking_error()` -> `QUEUED` revert vs permanent `FAILED`).
  * Eliminated ~400 lines of duplicated booking logic across `_loop()`, `trigger_client_booking()`, `quick_book_slot()`, and `monitor.py`.

### Core Engine & Driver Updates
* **`agents/appointments/portals/gvc.py` `[MODIFIED]`:**
  * Added `import base64` and `decode_jwt_subject()`.
  * Added `strict_isolation: bool = False`, `bind_account_session()`, and `get_bound_jwt_subject()`; strict drivers physically refuse to read legacy `gvc_sessions`.
  * Delegated cookie refresh and WAF `fetch()` fallback in `search_slots()`, `trigger_booking_otp()`, and `submit_booking()` to `playwright_client.py`.
  * Fixed `submit_booking()` VAC precedence (`vac_id or applicant.vac_id`) and unified phone normalization onto `otp.normalize_phone()`.
* **`agents/appointments/portals/gvc_auth.py` `[MODIFIED]`:**
  * Delegated `_preflight_waf_probe_sync()` and `_browser_login_sync()` to `playwright_client.py`.
  * Persisted `assigned_proxy_url` per account in `gvc_portal_accounts` and stopped fleet logins (`account_id != None`) from overwriting legacy global `gvc_sessions`.
* **`agents/appointments/otp.py` `[MODIFIED]`:**
  * Added `min_timestamp: Optional[float] = None` to `wait_for_otp()` to reject pre-trigger stale OTPs while still capturing fresh SMS arriving during `trigger_booking_otp()`.
* **`agents/appointments/fleet_manager.py` `[MODIFIED]`:**
  * Constructed `AccountWorkerInstance._driver = GVCPortalDriver(strict_isolation=True)` and bound per-account JWT, cookies, and sticky proxy on every `_loop()` iteration; exposed `bound_jwt_subject` in telemetry.
  * Upgraded `SlotDiscoveryCache` with loop-safe `asyncio.Event` `<10ms` wakeup (`wait_for_open()`) and lock-protected atomic `reserve_slot()` / `reserve_any_open_slot()` / `release_slot()`.
  * Stopped caching `[]` on `WAF_CHALLENGE`, `RATE_LIMITED`, or `ERROR`.
  * Added mode-aware `get_scan_pacing_profile()`: `drop_window` (`5s` cache, `3–6s` jitter, `0.3–0.6s` date spacing, full sweep every 3rd scan) vs `normal` (`45s` cache, `15–30s` jitter, `1.0s` date spacing, full sweep every 4th scan), with `priority_plus_periodic_sweep` default.
  * Delegated `_loop()`, `trigger_client_booking()`, and `quick_book_slot()` booking execution to `booking_engine.execute_booking()`.
* **`agents/appointments/monitor.py` `[MODIFIED]`:**
  * Delegated booking execution to `booking_engine.execute_booking()`.

### Control Plane, Cockpit CLI & Guardrails
* **`api/main.py` `[MODIFIED]`:** Added `scan_mode` (`normal` | `drop_window`) and `scan_date_strategy` (`priority_plus_periodic_sweep` | `target_dates_only`) to `/api/settings/operational-mode`.
* **`cockpit/fleet.py` & `cockpit/cli.py` `[MODIFIED]`:** Added `python -m cockpit.cli fleet --scan-mode drop_window|normal` and `--scan-strategy priority_plus_periodic_sweep|target_dates_only`.
* **`cockpit/doctor.py` `[MODIFIED]`:** Added live per-worker JWT isolation & uniqueness audit (`[CRITICAL]` alert on duplicate or mismatched `bound_jwt_subject`) and excluded already-retired `DISABLED` proxies from active WAF alerts.
* **`tests/test_golden_worker_e2e.py` `[NEW]` & `tests/test_fleet_and_activity_stream.py` `[MODIFIED]`:**
  * Added 10 new tests: AST undefined-name gate (`agents/appointments/` & `cockpit/`), strict worker isolation, `drop_window`/`normal` pacing, atomic slot reservation & `<100ms` wakeup, JWT `sub` email + actual slot VAC + Playwright WAF fallback (OTP & submit), stale OTP rejection, transient re-queueing, 1-Worker Golden 8-Step E2E, and 5-Worker Simultaneous Scale E2E.
* **`docs/permanent/standards/01-coding-standards.md` `[MODIFIED]`:** Codified centralization, strict worker isolation, zero negative-cache poisoning, and AST gate invariants.

---

## 3. Verification Summary (Automated + Live Staging on Portainer)

* **Automated Test Suite (`pytest tests/ -q`):** **`78 / 78` passed (`100%`)**, including all 10 new atomic/E2E/AST tests.
* **Live Portainer Deployment (`refs/heads/feat/unified-booking-engine`):**
  * Clean startup (`21` accounts loaded, `0` runtime errors).
  * **Multi-Account Login & Isolation:** `#12` (`tayyab16287@gmail.com` -> `bound_jwt_subject = 'tayyab16287@gmail.com'`) and `#6` (`managerzayan@gmail.com` -> `bound_jwt_subject = 'Gulmuhammad85'`) both authenticated on Attempt 1/3 over sticky 60m Greece proxies with distinct JWT subjects; paused `#15` remained `bound_jwt_subject = None`.
  * **Parallel Manual Book Now (`SAFE_MODE`):** Client `#40` (Lahore Type 2 via `#6`) and Client `#39` (Islamabad Type 26 via `#15`) executed in parallel without worker collision, `$0.00` CapSolver spend, `0` OTP spam, and remained safely `QUEUED`.
  * **Background Scout (`#13 Imran Nazir`):** Active `POLLING_SLOTS (12/2026)` (`Scans=10`), discovering both `UNRELEASED` and `BOOKED_OUT` dates via `priority_plus_periodic_sweep`.
  * **OTP Webhook Bus (`python -m cockpit.cli otp test-all`):** **`24 / 24` SIMs `[PASSED]` (`100%`, `GVC? = YES`)**.
  * **`cockpit doctor`:** **`100/100 [READINESS: READY TO STRIKE]`**, zero JWT isolation alerts, zero active proxy warnings.
  * **Proxy Finding Verified Live:** Decodo Greece proxies require sticky session credentials (`user-<user>-sessionduration-60`) to avoid Imperva IP-rotation WAF blocks during login.
