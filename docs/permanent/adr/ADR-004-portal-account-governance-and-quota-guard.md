# ADR-004: Portal Account Governance, Strict Lifecycle States & Zero-Cost Pre-Flight Quota Guard

> **Status:** APPROVED  
> **Date:** 2026-11-20  
> **Author:** Antigravity AI Engineering Team  
> **Deciders:** Platform Architect, Autonomous Systems Lead, Operations Coordinator  

---

## Context & Problem Statement

Production telemetry from `testing/terminal-logs.txt` revealed a 96.1% waste rate on CapSolver balance (25 out of 26 solved tokens discarded). 

Two major architectural flaws caused this behavior:
1. **Inverted Authentication Pipeline:** CapSolver API was invoked prior to validating whether the assigned residential proxy was routable or blocked by Imperva Incapsula WAF. When proxies timed out or encountered `iframe#main-iframe`, the solved CAPTCHA was discarded unused.
2. **Account Pool Disconnect:** The background solver worker bypassed the `gvc_portal_accounts` database table, reading a legacy single account (`amr.shah@gmail.com`) directly from `system_settings`. Consequently, even when an operator manually paused all accounts from the dashboard, background workers continued hammering the same account, with zero rotation and zero respect for paused states.

---

## Decision Drivers

* **Financial Governance:** Eliminate billing against CapSolver for dead proxies or WAF-blocked IP addresses.
* **Operational Control:** Ensure that when an operator pauses or disables an account in the dashboard, background services strictly halt all activity for that account.
* **Resilience & Fair Sharing:** Rotate across the account pool upon failure rather than repeatedly targeting a single failing account.
* **Safety Thresholds:** Prevent runaway retry loops through exponential backoff and circuit breaking.

---

## Considered Options

1. **Option 1: Status Quo with Longer Sleep:** Increase retry delay from 30s to 300s without changing pre-flight order or account resolution.
   * *Rejected:* Does not prevent quota waste on bad proxies; still bypasses dashboard account pause controls.
2. **Option 2: Strict Pre-Flight Probe with Single Hardcoded Account:** Invert order (probe proxy first, then solve), but keep single account setting.
   * *Rejected:* Fails to utilize fleet account pool; single account failure blocks entire system.
3. **Option 3 (Chosen): Unified Account Pool Governance with 4-State Lifecycle & Two-Phase Pre-Flight Clearance:**
   * Deprecate isolated single-account auto-solver settings.
   * Implement strict 4-state account lifecycle: `ACTIVE`, `PAUSED`, `DISABLED`, `COOLDOWN`.
   * Implement two-phase pre-flight probe: Phase 1 validates network connectivity and absence of Imperva WAF challenge at zero cost; Phase 2 executes CapSolver solve only on verified clean DOM.
   * Automatically rotate failing accounts into `COOLDOWN` and proceed to the next `ACTIVE` account.
   * If zero accounts are `ACTIVE`, all workers transition to zero-load standby.

---

## Decision Outcome

**Chosen: Option 3.**

### Architectural Changes:
1. **Schema Expansion:** Add explicit `status` (`ACTIVE`, `PAUSED`, `DISABLED`, `COOLDOWN`) and `cooldown_until` columns to `gvc_portal_accounts`.
2. **Unified Worker Pool:** `GVCSolverWorker` and `FleetManager` consume a single shared query: `SELECT * FROM gvc_portal_accounts WHERE status = 'ACTIVE'`.
3. **Pre-Flight Zero-Cost Probe:** In `gvc_auth.py`, navigate Playwright to `pk-gr-services.gvcworld.eu/?lang=en_US` before solving CAPTCHA. If WAF is triggered or connection fails, quarantine proxy and exit with zero CapSolver spend.
4. **Circuit Breaker:** Trip solver loop into standby after 3 consecutive cycle failures.

---

## Consequences

### Positive
* **95%+ Cost Reduction:** Zero CAPTCHA balance spent on unreachable proxies or Imperva Incapsula challenge pages.
* **Strict Admin Sovereignty:** Toggling an account to `PAUSED` or `DISABLED` immediately halts all worker operations on that account.
* **Automated Failover:** When an account hits authentication limits, the system seamlessly transitions it to `COOLDOWN` and rotates to the next active operator account.

### Negative / Trade-offs
* Pre-flight navigation adds 1.5–2.5 seconds of latency per authentication attempt to verify DOM cleanliness before invoking the solver.
* Requires database migration on `gvc_portal_accounts` to add `status` and `cooldown_until` fields.

---

## Invariants & Compliance Rules
1. Never call `solve_recaptcha_v2` before proxy connection and WAF status are verified.
2. Never execute login attempts against accounts in `PAUSED` or `DISABLED` status.
3. If all accounts are `PAUSED` or `DISABLED`, background workers must remain in zero-load idle.
