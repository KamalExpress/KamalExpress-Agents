# Playbook: Imperva Incapsula WAF Challenge Resolution

* **Category:** Operational Troubleshooting Playbook  
* **Applies To:** GVC Portal Authentication & Residential Proxy Routing  
* **Last Verified:** 2026-11-20  
* **Related Code:** `agents/appointments/portals/gvc_auth.py`, `standalone_workers/checker.py`

---

## 1. Problem Overview & Symptoms
When autonomous workers or the dashboard attempt to log into GVC World (`pk-gr-services.gvcworld.eu`), the login attempt fails.
* **Error Signatures:**
  * `HTTP 472 Unknown Reason`
  * `HTTP 403 Forbidden`
  * Response body starts with `<html` or contains `<iframe id="main-iframe" src="/_Incapsula_Resource?SWUDNSAI=31...">`
  * Terminal/App log: `[gvc_auth] [WARNING] Imperva Incapsula anti-bot challenge detected on target origin.`

---

## 2. Root Cause Analysis
Imperva Incapsula WAF monitors IP reputation, request rates, and TLS fingerprints. If an IP or residential proxy subnet has high traffic or bot signatures, Imperva intercepts the request with an anti-bot challenge wrapper embedding an hCaptcha verification gate (`sitekey=dd6e16a7-972e-47d2-93d0-96642fb6d8de`). 

Because direct REST POST requests cannot execute client-side JS or complete the challenge, GVC responds with HTTP 472 / 403.

---

## 3. Automated System Behavior
1. **Circuit Breaker:** `agents/appointments/portals/gvc_auth.py` checks for `iframe#main-iframe` within 2 seconds of loading `/?lang=en_US`.
2. **Credit Protection:** If the challenge is present, the solver halts immediately **before** calling CapSolver, saving the user's reCAPTCHA solving balance ($0.002 per solve).
3. **Proxy Quarantine:** The affected proxy IP is marked failed in SQLite and quarantined for 5 minutes (`quarantined_until = now + 300s`).
4. **Structured Failure:** The function returns `{"is_waf_challenge": True, "reason": "WAF_CHALLENGE"}`.

---

## 4. Standard Operational Resolution (SOP)
When the entire residential proxy pool is challenged by Imperva:

### Step 1: Open GVC World in Standard Desktop Browser
Open Chrome, Edge, or Brave on your workstation and navigate to:
`https://pk-gr-services.gvcworld.eu/?lang=en_US`

### Step 2: Complete Login Manually
1. Enter your GVC username (`email`) and password.
2. Solve the on-page reCAPTCHA v2 checkbox.
3. Click **Log in**. The browser will legitimately clear the challenge and reach `/dashboard`.

### Step 3: Extract Session Token or Cookies
* **Option A (Console Snippet):** Press `F12` to open DevTools, switch to the **Console** tab, paste the 1-line sync snippet provided on the Kamal Express dashboard, and press Enter.
* **Option B (Copy Cookie / Token):** In DevTools > Application > Cookies, copy the `auth_token` value (or copy the `Authorization: Bearer <JWT>` header from any network request).

### Step 4: Import via Dashboard Modal
1. Open the Kamal Express Dashboard at `http://localhost:8080`.
2. Navigate to **Multi-Account Fleet Engine** > Click **Sync Session** on the target account.
3. Paste the token or cookies and click **Sync Session**.
4. The system updates `gvc_portal_accounts`, sets `is_authenticated = 1`, and all autonomous workers immediately resume monitoring and booking without hitting login WAF blocks.
