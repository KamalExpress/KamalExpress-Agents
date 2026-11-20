# Current Sprint State & Active Objectives

> **Current Sprint:** Platform Hardening, Account Governance & WAF Resilience  
> **Last Updated:** 2026-11-20  
> **Active Branch:** `standalone-flow` (in strict parity with `main`)

---

## 1. Verified Live System State
* **CapSolver Quota:** Confirmed positive balance of **\$5.94** (`ok: True`). reCAPTCHA v2 solving takes 15–20s.
* **Pakistan Residential Proxy Pool:** Decodo Pakistan residential proxies active (**102/105 healthy**).
* **Imperva Incapsula Early Detection:** Added to `agents/appointments/portals/gvc_auth.py`. Detects WAF anti-bot challenges (`iframe#main-iframe` / `_incapsula_resource` / `HTTP 472`) in <2s, halts execution before burning CapSolver balance, quarantines proxy for 5m, and returns `is_waf_challenge: True`.
* **Sanctioned Operator Workflow:** Active GVC sessions are imported/synced via the **Sync GVC Account** modal in the dashboard.
* **Zero-Emoji Compliance:** Completed platform-wide purge across all backend logs, fleet manager, and dashboard index.
* **Documentation Hub:** In-browser documentation viewer active at `docs/index.html` (and `/documentation`), rendering Markdown via client-side `marked.min.js` and Highlight.js with dark/light themes.
* **Alamia Documentation Skill:** Universal, project-agnostic skill created in `Alamia-Skills-System` and pushed to upstream repository.

---

## 2. Test Verification Status
* **Test Suite:** `pytest tests/test_service_guard.py tests/test_otp_gvc_spam_filtering.py tests/test_fleet_and_activity_stream.py`
* **Result:** **16/16 Passed** (100% success rate).
* **Alamia-Skills-System Test Suite:** `pytest E:\Alamia\Alamia-Skills-System\tests\`
* **Result:** **28/28 Passed** (100% success rate).

---

## 3. High-Priority Sprint Objectives (ADR-004 Implementation)

### Priority 1: Zero-Cost Pre-Flight Quota Guard (Critical)
* Invert authentication order in `agents/appointments/portals/gvc_auth.py`.
* Implement **Phase 1 Pre-Flight Probe**: test proxy connectivity and confirm absence of Imperva WAF challenge (`iframe#main-iframe`) at zero cost.
* Only trigger CapSolver (**Phase 2**) after the proxy and DOM are verified healthy, eliminating the 96% quota waste rate.

### Priority 2: Account Governance & Strict 4-State Lifecycle
* Add explicit `status` (`ACTIVE`, `PAUSED`, `DISABLED`, `COOLDOWN`) and `cooldown_until` to `gvc_portal_accounts`.
* Update web dashboard to support `DISABLED` and `PAUSED` toggles for all accounts.
* Connect `GVCSolverWorker` to the dynamic account pool, deprecating the isolated `amr.shah@gmail.com` bypass.
* Ensure all workers strictly ignore `PAUSED` and `DISABLED` accounts.

### Priority 3: Dynamic Account Failover & Circuit Breaker
* When an account fails authentication, mark it in `COOLDOWN` and automatically fail over to the next available `ACTIVE` account.
* If zero accounts are active, halt worker execution with zero network calls.
* Add exponential backoff and 3-cycle circuit breaker to `GVCSolverWorker`.

### Priority 4: Database Proxy Pool Purge
* Purge invalid placeholder/dummy proxies (`test.proxy.io`, `1.2.3.4`, `5.6.7.8`) from SQLite.
