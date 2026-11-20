# Portal Account Governance & Zero-Cost Pre-Flight Quota Guard

> **Document ID:** `10-portal-account-governance-and-quota-guard`  
> **Status:** APPROVED / IN PROGRESS  
> **Author:** Antigravity AI Engineering Team  
> **Last Updated:** 2026-11-20  
> **Category:** Architecture Specification & Operational Governance  

---

## 1. Executive Summary & Incident Diagnostics

Analysis of production telemetry recorded in `testing/terminal-logs.txt` revealed a critical architectural gap in authentication governance, account lifecycle management, and third-party quota consumption.

### Production Telemetry Breakdown (`terminal-logs.txt`)
* **CapSolver Tasks Submitted:** 33 requests
* **CapSolver Tasks Solved & Billed:** 26 tokens
* **Imperva Incapsula WAF Blocks Post-Solve:** 17 tokens discarded (65.4%)
* **Proxy Connection Failures Post-Solve:** 8 tokens discarded (30.8%)
* **Net Quota Wastage Rate:** **96.1%** (25 out of 26 paid tokens discarded without successful authentication)
* **Proxy Quarantines:** 26 rotations
* **Server Restarts via Reloader:** 9 cycles

```
[Candidate Proxy Selected]
          │
          ▼
[Solve reCAPTCHA v2 ($0.003 Paid)]  <── INVERTED STEP (Burns Quota First)
          │
          ▼
[Open Headless Browser via Proxy]
          │
    ┌─────┴────────────────────────┐
    ▼                              ▼
[Proxy Dead / Timed Out]      [Imperva WAF Challenge]
(Token Discarded: $0.003 Lost)  (Token Discarded: $0.003 Lost)
```

---

## 2. Identified Vulnerabilities & Weak Controls

### Vulnerability 1: Inverted Pre-Flight Order (Zero-Cost Verification Deficit)
In `agents/appointments/portals/gvc_auth.py`, `login_with_credentials()` called `solve_recaptcha_v2()` before opening a network socket to the consular portal. If the assigned residential proxy was unroutable, dead, or flagged by Imperva Incapsula, the solved token was discarded.

### Vulnerability 2: Isolated Auto-Solver Bypass of Account Pool
The background worker `GVCSolverWorker` in `agents/appointments/solver_worker.py` was implemented as a single-account daemon that fetched credentials exclusively from `system_settings` (`gvc_account_email` = `amr.shah@gmail.com`). 
* **The Bypass:** Even when operators manually paused all portal accounts in `gvc_portal_accounts` (`is_worker_active = 0`), `GVCSolverWorker` continued executing in the background, repeatedly hammering `amr.shah@gmail.com`.
* **Zero Pool Awareness:** The worker had no awareness of other configured portal accounts and could not rotate or distribute load.

### Vulnerability 3: Missing Account Lifecycle States (`DISABLED` / `COOLDOWN`)
The database schema for `gvc_portal_accounts` only supported binary active/inactive flags (`is_worker_active` 0 or 1). There was no explicit status to represent:
1. `DISABLED`: Permanently decommissioned accounts that must never be touched by workers or background solvers.
2. `COOLDOWN`: Temporarily paused accounts cooling down after authentication failures or OTP timeouts.

### Vulnerability 4: Hyper-Aggressive Retry Cadence
When 3 login attempts failed, `GVCSolverWorker` slept for only 30 seconds before re-initiating another 3-attempt cycle. In the presence of persistent WAF blocks, this resulted in the consumption of 6 to 9 CAPTCHA tokens per minute.

### Vulnerability 5: Mock and Dummy Proxies in Persistent Database
Inspection of the SQLite `proxies` table revealed unroutable placeholder domains (`test.proxy.io:8080`, `1.2.3.4:9000`, `5.6.7.8:1234`). When the proxy manager selected these entries, connection timeouts were guaranteed post-solve.

---

## 3. Target Governance & Architectural Design

### 3.1 Strict 4-State Account Lifecycle
Every account in `gvc_portal_accounts` must transition through four explicit states:

```
                  ┌───────────────┐
                  │   DISABLED    │  (Hard Prohibition: Never Selected)
                  └───────▲───────┘
                          │ Manual Disable
                          │
  Manual Pause    ┌───────┴───────┐   Manual Resume
  ───────────────►│    PAUSED     │◄────────────────
  ◄───────────────│ (Admin Guard) │────────────────►
                  └───────┬───────┘                 │
                          │                         │
                          ▼                         ▼
                  ┌───────────────┐         ┌───────────────┐
                  │    ACTIVE     │◄───────►│   COOLDOWN    │
                  │ (Eligible)    │         │ (Temp Cooldown│
                  └───────────────┘         │  after error) │
                                            └───────────────┘
```

1. **`ACTIVE`**:
   * Eligible for autonomous scheduling, session renewal, and booking execution.
   * Assigned to fleet workers and selected by the rotation manager.
2. **`PAUSED`**:
   * Temporarily suspended by operator from the web dashboard.
   * **Absolute Prohibition:** All background workers, auto-solvers, and fleet processes must immediately ignore paused accounts.
3. **`DISABLED`**:
   * Permanently excluded from operation (e.g. invalid credentials, expired registration, terminated operator).
   * Excluded from all queries and selection algorithms.
4. **`COOLDOWN`**:
   * Automatically placed here when an authentication attempt fails.
   * Cooldown period: 15–30 minutes with exponential backoff on subsequent failures.
   * Automatically rotates to the next available `ACTIVE` account in the pool.

### 3.2 Pre-Flight Zero-Cost Probe (Two-Phase Clearance)
Before invoking CapSolver, the system must execute a lightweight, non-billable pre-flight probe using Playwright with the candidate proxy:

```
[Candidate Proxy & Account]
             │
             ▼
   [Phase 1: Pre-Flight Probe]
   (Navigate to pk-gr-services.gvcworld.eu)
             │
     ┌───────┴────────────────────────┐
     ▼                                ▼
[Network Failure / WAF Challenge]   [200 OK & Clean DOM]
     │                                │
     ├─► Quarantine Proxy (30-60m)    ├─► Phase 2: Solve reCAPTCHA v2 (CapSolver)
     ├─► Zero Dollars Expended        ├─► Phase 3: Submit POST /api/v1/auth/login
     └─► Rotate to Next Proxy         └─► Phase 4: Save Verified Session Token
```

* **Step 1 (Pre-Flight):** Navigate to `https://pk-gr-services.gvcworld.eu/?lang=en_US` using the assigned proxy.
* **Step 2 (Integrity Verification):** Verify HTTP status is 200 and confirm DOM does not contain `iframe#main-iframe` or `_incapsula_resource`.
* **Step 3 (Early Exit):** If connection fails or WAF challenge is detected, quarantine proxy immediately and rotate. **CapSolver API is never called.**
* **Step 4 (Targeted Solve):** Only on a verified clean DOM, request reCAPTCHA solution and submit credentials.

### 3.3 Dynamic Account Pool Rotation
Instead of hardcoding a single account email in `system_settings`:
1. `GVCSolverWorker` queries `gvc_portal_accounts` for accounts where `status = 'ACTIVE'`.
2. If an account's session is expired, it is scheduled for renewal.
3. If an account fails authentication, it is transitioned to `COOLDOWN` and the next `ACTIVE` account is selected.
4. **All-Accounts-Halted Guard:** If zero accounts are in `ACTIVE` state, the worker enters `IDLE (All Accounts Paused/Disabled)` and executes zero network requests.

### 3.4 Circuit Breaker & Exponential Backoff
* **Backoff Strategy:** Replace the 30-second sleep with exponential backoff:
  $$\text{Interval} = \min(300 \text{ s}, 30 \times 2^{\text{failures}})$$
* **Circuit Breaker:** If 3 consecutive cycles fail across all active accounts, auto-solver trips to `TRIPPED_ERROR`, dispatches an alert to the activity stream, and halts execution until manual operator reset.

---

## 4. Implementation Invariants
1. **Zero-Cost Invariant:** No CAPTCHA API request may be executed without prior successful HTTP/DOM clearance through the assigned proxy on the target origin.
2. **Account Governance Invariant:** No background process may execute an authentication request against an account in `PAUSED` or `DISABLED` status.
3. **Empty Pool Invariant:** When all accounts are paused or disabled, all authentication workers must transition to zero-load standby.
4. **Proxy Sanity Invariant:** Proxies matching reserved, private, or mock hostnames (`test.proxy.io`, `1.2.3.4`, `5.6.7.8`, `localhost`) must be purged from database storage and rejected on creation.
