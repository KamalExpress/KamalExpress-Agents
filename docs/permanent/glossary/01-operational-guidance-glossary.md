# Operational Guidance & Terminology (EDR Standard)

Standardized Explain, Diagnose & Recover (EDR) guide for Kamal Express AI Platform operations.

---

## 1. Operational Events & Terminology

### GVC Multi-Account Portal Fleet (`gvc_portal_accounts`)
- **Explain:** A multi-tenant architecture allowing each staff member to register multiple Greece GVC World portal logins. Each portal account runs an isolated parallel worker with a distinct mobile phone number for OTP receipt.
- **Diagnose:** Check the **GVC Auth & Sessions** tab in the dashboard. Inspect the status badges (`AUTHENTICATED` vs `UNAUTHENTICATED`, `ACTIVE` vs `PAUSED`).
- **Recover:**
  - If `UNAUTHENTICATED`: Click **Login** to trigger CapSolver, or use **Manual Token Sync**.
  - If `PAUSED`: Click the **Play** button to resume autonomous worker polling.
  - If invalid session: Click **Logout** to clear tokens and re-login.

### Universal OTP Event Bus & Webhook (`/api/otp/webhook`)
- **Explain:** Ingestion gateway receiving automated SMS/WhatsApp forwarding payloads from physical Android devices via HTTP POST. Extracts 4-8 digit verification codes and resolves in-flight appointment booking requests in real-time.
- **Diagnose:** Check the **OTP & Webhook** tab. Verify the rolling stream table displays received messages. If phone numbers show unquoted JSON syntax errors (`"to": 0334...`), verify the regex sanitizer handled it.
- **Recover:**
  - Use the built-in **"Simulate Test OTP"** tool to confirm the event bus wake-up mechanism.
  - Verify the Android forwarding app payload matches `{ "from": "%from%", "text": "%text%", "to": "%to%" }`.

### Proxy Auto-Quarantine & Recovery (`_auto_expire_quarantined_proxies`)
- **Explain:** Automatic fault tolerance mechanism for Pakistan residential proxies. Proxies encountering Imperva WAF blocks or timeouts are quarantined for 300 seconds. Upon time expiry, read operations automatically restore them to `ACTIVE`.
- **Diagnose:** Check the **Residential Proxies** tab. If quarantined count does not decrease after 5 minutes, check server time synchronization.
- **Recover:** Click **"Reset Cooldowns"** on the Proxy tab to immediately un-quarantine all proxies.

### Full Administrative Data Export (`GET /api/admin/export-data`)
- **Explain:** One-click disaster recovery and data audit feature accessible by Administrators. Dumps the complete SQLite database across 8 tables (staff, GVC accounts, proxies, OTP logs, client queue, solver keys) into a portable JSON backup file.
- **Diagnose:** Check the **Staff & Roles** tab under "Complete System Data Backup & Export".
- **Recover:** Click **"Inspect Data"** for an on-screen summary or **"Export All Data (.JSON)"** to download the archive.

### WAF Client Hints & Browser Header Fingerprinting (`_get_headers`)
- **Explain:** Standardized Chromium HTTP headers (`User-Agent`, `sec-ch-ua`, `sec-ch-ua-mobile`, `sec-ch-ua-platform`, `Sec-Fetch-*`) that match real Google Chrome 120 on Windows to bypass Imperva Incapsula anti-bot detection without triggering CAPTCHA loops.
- **Diagnose:** If portal requests return `403 Forbidden` or challenge HTML responses, check whether custom API calls omit `_get_headers()` or TLS impersonation in `curl_cffi`.
- **Recover:** Ensure all HTTP requests route through `GVCPortalDriver` or `curl_cffi.requests.AsyncSession(impersonate="chrome120")` using `driver._get_headers()`.

### Client Queue Name Mapping (`first_name` & `last_name` / `surname`)
- **Explain:** Client intake forms explicitly ask for "First Name" and "Surname" (matching passport and GVC portal UI). The database schema stores them in `first_name` and `last_name`, and the booking driver maps them to GVC REST payload keys `"firstname"` and `"lastname"`.
- **Diagnose:** If a client record fails validation, ensure both `first_name` and `last_name` (or `surname` alias) are populated.
- **Recover:** The `ClientProfile` Pydantic model automatically aliases `surname` $\leftrightarrow$ `last_name`, allowing backwards and forwards compatibility without database migrations.

### Dynamic Hidden Entity Extraction (`#otpuser` Extraction)
- **Explain:** Server-side rendered hidden input field on `POST /appointments/add` containing the serialized user entity string (`User{id=..., username=..., ...}`). GVC requires this exact string in `POST /api/v1/appointments`.
- **Diagnose:** Booking fails with backend error or rejection when only calling REST APIs without loading `POST /appointments/add`.
- **Recover:** Ensure worker warms up session by calling `POST /appointments/add` and extracting `#otpuser` via regex.

### Single-OTP Multi-Slot Traversal Loop (`SLOT_TRAVERSAL_RECOVERY`)
- **Explain:** Sequential attempt across ordered timetable slots reusing a single active session OTP. Prevents rate-limiting and invalidation caused by multi-OTP dispatch.
- **Diagnose:** First slot attempt returns `SLOT_UNAVAILABLE` or `CAPACITY_EXCEEDED`.
- **Recover:** Do not request new OTP. Worker immediately iterates to next available candidate slot in memory and resubmits `POST /api/v1/appointments` with existing OTP.

### Slot-Drop Strike Mode (`SLOT_DROP_STRIKE`)
- **Explain:** Zero-latency direct execution mode triggered when quota drop is announced. Bypasses polling loops, fetches target timetable once, selects candidate, and fires OTP immediately.
- **Diagnose:** Timetable query returns 0 slots or target date violates weekday rule.
- **Recover:** Verify date against `GVC_VISA_DAY_RULES`. If 0 slots, transition to Scout Mode or verify center ID.

### 1-Click Desktop & VPS Launchers (`START_CHECKER.bat`, `START_BOOKER.bat`)
- **Explain:** Zero-CLI wrappers located inside `standalone_workers/` allowing non-technical office staff to launch workers without memorizing terminal commands, typing CLI flags, or interacting with Python. All operational parameters (VAC ID, Visa Type, Month, Date, Preferences) are pre-loaded from `config.json`.
- **Diagnose:** If launcher exits immediately, check if Python is on system `PATH` or if virtual environment activation is missing.
- **Recover:** Double-click `START_CHECKER.bat` for continuous scouting, or `START_BOOKER.bat` for drop booking. For Linux VPS, use `start_checker.sh` and `start_booker.sh`.

### Scout Polling Cadence & Multi-Date Scan Pacing (`SCOUT_POLL_CADENCE`)
- **Explain:** When monitoring a full month (e.g., 22 operational weekdays in October 2026), the scout sequentially queries each date with a 300ms polite throttle to prevent proxy rate-limiting. A 22-date cycle takes $\approx 18$ seconds, followed by the configured quiet interval (default 15s). The next poll banner appears after $\approx 33$ seconds total.
- **Diagnose:** Staff may perceive the scout as "stuck" between poll banners while it is actively traversing dates or sleeping through the quiet interval.
- **Recover:** Inspect console timestamps to confirm normal progress. To accelerate feedback on confirmed drop days, set `target_date: "07/10/2026"` in `config.json` to monitor a single high-priority day in $<1$ second instead of scanning the full month.

### Automated CapSolver Engine (`CAPSOLVER_SOLVER_ENGINE`)
- **Explain:** Autonomous background captcha solver integrated across both the FastAPI control plane and standalone worker execution plane. Resolves reCAPTCHA v2 tokens for GVC session login and final booking submissions (`g-recaptcha-response`).
- **Diagnose:** Check CapSolver API balance (`https://api.capsolver.com/getBalance`). If balance reaches zero, solving fails with `ERROR_ZERO_BALANCE`.
- **Recover:** Ensure `CAPSOLVER_API_KEY` is present in `standalone_workers/.env` and root `.env` with a positive account balance. Both systems auto-detect and refresh this key dynamically without server restarts.

---

## 2. Standard Greek VAC & Visa Category Codes

### Official GVC Pakistan Centers (Strict Invariant)
* **`137`**: **Islamabad Visa Application Center for Greece** (Primary / Default)
* **`138`**: **Lahore Visa Application Center for Greece**
* **`139`**: **Document Verification Office** (Reserved for future document verification phase)
* **Karachi Center**: **Does NOT exist** for Greece in GVC World.

| Code | Name / Category | City / Status | Default |
| :--- | :--- | :--- | :--- |
| `137` | Islamabad Visa Application Center for Greece | Islamabad (Active) | ✅ Default |
| `138` | Lahore Visa Application Center for Greece | Lahore (Active) | — |
| `139` | Document Verification Office | Verification Phase (Future) | — |
| `26` | Long-Term Type D (Seasonal/Dependent Employment) | Mon, Tue, Wed, Thu, Fri | ✅ Default |
| `0` | Submission Schengen Visa (Short term – Type C) | None (Workers do not query) | — |
| `2` | National visa (Long term - type D) | Thu, Fri | — |
| `5` | Premium Lounge | Mon, Tue, Wed, Thu, Fri | Optional Service |
| `6` | Prime Time | Mon, Tue, Wed, Thu, Fri | Optional Service |
