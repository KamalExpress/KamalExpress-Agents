# Coding Standards & Engineering Invariants

> **Status:** Active Standard  
> **Applies To:** Full Repository (Agents, API, Workers, UI, Scripts)

---

## 1. Professional Standard: Zero Emojis (Strict Invariant)
* **Never use emoji characters anywhere in the platform:**
  * No emojis in frontend markup (`api/static/index.html`, `docs/index.html`).
  * No emojis in log output (`logger.info`, `logger.warning`, `print`).
  * No emojis in exception strings, flash notices, tooltips, or alerts.
* **Standard Replacements:**
  * In User Interfaces: Use clean **Lucide SVG icons** (`<svg ...>` or `<i data-lucide="...">`).
  * In Log Streams & Activity Messages: Use clean uppercase bracketed status indicators:
    * `[SUCCESS]` (Replaced checkmarks / celebration emojis)
    * `[ERROR]` (Replaced cross / red circle emojis)
    * `[WARNING]` (Replaced warning triangle emojis)
    * `[INFO]` (Replaced info / clipboard emojis)
    * `[HALTED]` (Replaced stop sign emojis)
    * `[SLOTS FOUND]` (Replaced siren / bell emojis)
    * `[OTP TRIGGER]` (Replaced lightning bolt emojis)
    * `[OTP RECEIVED]` (Replaced mobile phone emojis)

---

## 2. Code Quality & Architecture
* **Strict Type Hints:** All Python functions and tool definitions must use type annotations (`from typing import Optional, List, Dict, Any`).
* **Explicit Resource Cleanup:** All HTTP clients (`httpx`, `curl_cffi`) and headless browsers (`playwright`) must be explicitly closed or used within context managers.
* **Resilient Error Handling:** Catch concrete exceptions; never silently pass without logging the failure cause.
* **Auditability (HAR Recording):** Live transactional worker operations against GVC must record network traces using `LiveHarRecorder` to `logs/hars/`.

---

## 3. Security, RBAC & Client Privacy (PII Compliance)
* **Client Confidentiality:** In accordance with immigration and consular standards, client data (passport numbers, phone numbers) must remain masked in UI tables and public logs.
* **Unmasking Window:** Phone numbers are unmasked only during an active 180-second OTP verification strike window.
* **Credential Protection:** Never hardcode passwords, API keys, or JWT tokens in source code. All secrets are read from environment variables or SQLite settings.

---

## 4. Testing & Verification Protocol
* **Zero Regression Rule:** The test suite (`pytest tests/`) must pass with 100% success before merging changes or concluding an engineering turn.
* **Deprecation Notice:** Use timezone-aware UTC datetime representations: `datetime.now(datetime.UTC)` over deprecated `datetime.utcnow()`.
* **Centralization & Isolation Invariants (Enforced by `tests/test_golden_worker_e2e.py`):**
  * **Single Booking Pipeline:** All booking entrypoints (`AccountWorkerInstance._loop()`, `trigger_client_booking()`, `quick_book_slot()`, `AutonomousSlotMonitor`) MUST delegate to `agents/appointments/booking_engine.py:execute_booking()`. Never copy-paste OTP/captcha/submit logic.
  * **Single Stealth Playwright Engine:** All Playwright WAF probes, cookie refreshes, and in-browser `fetch()` fallbacks MUST use `agents/appointments/portals/playwright_client.py`.
  * **Strict Per-Worker Session Isolation:** Every fleet worker constructs `GVCPortalDriver(strict_isolation=True)` and binds its own `gvc_portal_accounts` JWT, cookies, and sticky proxy via `bind_account_session()`. Fleet workers MUST NEVER read the legacy global `gvc_sessions` table.
  * **Zero Negative-Cache Poisoning:** `WAF_CHALLENGE`, `RATE_LIMITED`, and `ERROR` responses MUST NEVER write empty `[]` entries to `SlotDiscoveryCache`.
  * **AST Undefined-Name Gate:** Every module under `agents/appointments/` and `cockpit/` must pass the automated AST undefined-name check with zero unresolved globals.

