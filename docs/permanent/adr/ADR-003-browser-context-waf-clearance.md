# ADR-003: Browser Execution Context for Imperva Incapsula Clearance & WAF Protection

* **Status:** Accepted  
* **Deciders:** Kamal Express Engineering Team  
* **Date:** 2026-10-01  
* **Related Specs:** `docs/permanent/architecture/08-failproof-autonomous-workers-specification.md`, `agents/appointments/portals/gvc_auth.py`

---

## 1. Context and Problem Statement
The Greece Consular Portal (`https://pk-gr-services.gvcworld.eu`) is protected by Imperva Incapsula Web Application Firewall (WAF). 

When HTTP client libraries (such as `requests`, `httpx`, or even TLS-spoofed `curl_cffi`) send direct POST requests to `/api/v1/auth/login`, Imperva intercepts the request with an anti-bot challenge (`_incapsula_resource` / `iframe#main-iframe`), returning `HTTP 472 Unknown Reason` or `HTTP 403 Forbidden`.

The challenge executes client-side JavaScript to compute browser fingerprints and cryptographic tokens before granting valid `visid_incap_*` and `incap_ses_*` cookies.

---

## 2. Decision Drivers
* **Session Viability:** GVC REST endpoints reject any request lacking valid Incapsula clearance cookies.
* **Credit Safeguard:** Prevent burning CapSolver balance ($0.002 per solve) against proxy IPs that are already blocked by an anti-bot challenge.
* **Safety & Compliance:** Strict adherence to security boundaries (never attempting automated bypasses of CAPTCHA gates).

---

## 3. Considered Options
* **Option A: Pure HTTP Requests with Cookie Replay:** Fails because Incapsula cookies are cryptographically tied to TLS fingerprints and client-side JS evaluation.
* **Option B: Playwright Stealth Chromium Handshake:** Launch headless Chromium with stealth evasion, let the page execute Incapsula JS clearance, and execute the login `fetch()` natively inside the origin page execution context.
* **Option C: Sanctioned Operator Session Import (Dashboard Modal):** Allow operators to log in legitimately via their desktop browser and import active tokens/cookies via the dashboard.

---

## 4. Decision Outcome
Chosen option: **Hybrid of Option B and Option C**.

1. **Autonomous Handshake (Option B):** Implemented in `_browser_login_sync`. Evaluates `fetch('/api/v1/auth/login')` directly inside the page context after origin clearance.
2. **Early Challenge Detection & Circuit Breaker:** Before solving captchas, the browser inspects the page for `iframe#main-iframe` or Incapsula challenge signatures. If detected, it immediately halts, quarantines the proxy IP, and prevents credit waste.
3. **Sanctioned Operator Sync (Option C):** When WAF challenges gate automated login across proxy pools, operators use the **Sync GVC Account** modal in the dashboard. Once saved to `gvc_portal_accounts`, autonomous monitoring and booking agents use the legitimate session tokens without triggering login gates.

### Positive Consequences
* Zero CapSolver credits burned against WAF-blocked IPs.
* Accurate diagnostics: operators know immediately whether an issue is an invalid credential or an anti-bot gate.
* Fleet continues running using imported active sessions.
