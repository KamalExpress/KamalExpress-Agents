# System Architecture & Technical Specification

**Kamal Express AI Platform** — Multi-Agent Travel & Visa Appointment Booking Platform.

---

## 1. High-Level Architectural Overview

```mermaid
flowchart TD
    StaffWorkstation([👤 Staff Workstations]) <--> UI[💻 shadcn/ui Dashboard + Lucide Icons]
    UI <--> API[⚡ FastAPI Control Plane]

    subgraph MultiAccountFleet ["🛡️ Multi-Account GVC Portal Fleet & Booker Workers"]
        API <--> FM[Fleet Manager Singleton]
        FM <--> Worker1[🤖 Account Worker #1 (ISB - Type 26)]
        FM <--> Worker2[🤖 Account Worker #2 (KHI - Type 26)]
        FM <--> WorkerN[🤖 Account Worker #N (LHE - Type 0)]
        Worker1 <--> Sim1[📱 SIM Phone 1]
        Worker2 <--> Sim2[📱 SIM Phone 2]
    end

    subgraph EventBus ["⚡ Universal OTP Event Bus & Webhook"]
        AndroidApp[📱 Android SMS Forwarder Apps] -->|HTTP POST JSON / Form| Webhook[/api/otp/webhook]
        Webhook --> Sanitizer[Leading-Zero JSON Sanitizer]
        Sanitizer --> RegexExtractor[Universal 4-8 Digit OTP Extractor]
        RegexExtractor --> OTPCache[(🗄️ SQLite otp_records + In-Memory Event Waiters)]
        OTPCache --> FM
    end

    subgraph ResProxyPool ["🇵🇰 Pakistan Residential Proxy Engine"]
        ProxyMgr[Proxy Manager] <--> ProxyDB[(🗄️ SQLite proxies)]
        ProxyDB --> AutoExpire[Auto-Expire Quarantine Helper]
        ProxyMgr --> CurlCffi[curl_cffi TLS Impersonator]
    end

    subgraph AgentTriage ["🧠 LangGraph Multi-Agent Engine"]
        API <--> Orchestrator[Triage Orchestrator Graph]
        Orchestrator --> ApptAgent[📅 Appointments Specialist]
        Orchestrator --> VisaAgent[🛂 Visa Specialist]
        Orchestrator --> UmrahAgent[🕋 Umrah & Hajj Specialist]
        Orchestrator --> HotelAgent[🏨 Hotel Specialist]
    end

    subgraph DatabaseLayer ["🗄️ SQLite WAL Thread-Safe Persistence"]
        DB[(kamal_express.db)]
        DB --- client_queue
        DB --- gvc_portal_accounts
        DB --- proxies
        DB --- users
        DB --- otp_records
        DB --- system_settings
        DB --- gvc_sessions
    end

    subgraph ExternalServices ["🌐 External Integrations"]
        CurlCffi --> GVC[🇬🇷 Greece GVC World Portal]
        FM --> CapSolver[🤖 CapSolver / 2Captcha API]
    end
```

---

## 2. Core Functional Subsystems

### A. Multi-Account Portal Fleet & Parallel Workers (`agents/appointments/fleet_manager.py`)
- **Multi-Tenant Staff Accounts:** Each staff member registers their own dedicated GVC World portal accounts (`gvc_portal_accounts` table).
- **Parallel Autonomous Execution:** Each account operates an independent `AccountWorkerInstance` worker loop:
  - Maintains dedicated JWT Bearer tokens and cookies.
  - Monitors designated VAC center (Islamabad, Karachi, Lahore) and visa type (Type 26 Seasonal/Work, Type 0 Schengen C, Type 2 National D).
  - Claims queued applicants atomically from `client_queue`.
  - Dispatches OTP to its dedicated SIM phone number (`otp_phone_number`).
  - Intercepts OTP from event bus and submits final booking within <1 second.
- **Worker Controls:**
  - **Login:** Triggers CapSolver automated solving for that specific account.
  - **Pause / Start:** Pauses or resumes the worker's polling loop without revoking the token.
  - **Logout:** Completely invalidates the session and resets authentication state.
  - **Token Sync:** Allows manual 1-click bookmarklet token pasting.

### B. Universal OTP Event Bus & Webhook (`api/main.py` & `agents/appointments/otp.py`)
- **Endpoint:** `GET / POST /api/otp/webhook`
- **Universal Payload Parsing:**
  - Ingests JSON, Form Data, Query Params, or Raw SMS text forwarded from any Android app (SMS Forwarder, MacroDroid, Tasker, SMS Gateway).
  - **Resilient JSON Sanitizer:** Automatically fixes unquoted leading-zero phone numbers (e.g. `"to": 03345112969` $\to$ `"to": "03345112969"`).
  - **Universal Regex Code Extraction:** Extracts 4-8 digit codes matching Gerrys/GVC patterns:
    - `"The OTP for your GVCW Appointment is: 99910"` $\to$ `99910`
    - `"GERRYS - This OTP number is valid for 5 mins..."`
- **Persistent SQLite Audit Table:** `otp_records` maintains complete historical logs (timestamp, recipient phone, sender, code, raw message, raw payload, client IP).

### C. Residential Proxy Pool & Auto-Quarantine Engine (`agents/appointments/proxy.py` & `db.py`)
- **Persistent SQLite Catalog:** `proxies` table tracks proxy URLs, failover counters, last errors, and quarantine timestamps.
- **Quarantine Auto-Expiry:**
  - When proxies fail against Imperva WAF, they are quarantined for 300 seconds (`status = 'QUARANTINED'`, `quarantined_until = now + 300s`).
  - `_auto_expire_quarantined_proxies(conn)` automatically executes on read queries (`get_all_proxies`, `get_active_proxies`, `get_proxy_stats`), resetting expired records back to `status = 'ACTIVE'` and `quarantined_until = NULL`.

### D. Full System Administrative Backup & Data Export (`agents/appointments/db.py` & `api/main.py`)
- **Endpoint:** `GET /api/admin/export-data` (Admin only)
- **Unified JSON Export Package:**
  - `staff_accounts`: All registered users and roles.
  - `gvc_portal_accounts`: All portal accounts, credentials, VAC targets, and worker stats.
  - `proxies`: Complete proxy catalog with health statistics.
  - `capsolver_keys`: Active solver configuration and API keys.
  - `gvc_credentials`: Configured master solver credentials.
  - `otp_messages_log`: Complete incoming OTP SMS/WhatsApp logs.
  - `client_queue`: Applicant queue profiles and booked references.
  - `system_settings`: Key-value system configuration.
### E. Anti-Bot TLS & Browser Header Fingerprinting Standard (`agents/appointments/portals/gvc.py`)
- **Imperva WAF / Anti-Bot Fingerprint Matching:**
  - Modern WAFs (such as Imperva Incapsula on `pk-gr-services.gvcworld.eu`) analyze HTTP/2 Client Hints and header consistency.
  - When requests send `Sec-Fetch-*` headers (`Sec-Fetch-Dest: empty`, `Sec-Fetch-Mode: cors`, `Sec-Fetch-Site: same-origin`), modern Chromium heuristics require valid **Client Hints** (`sec-ch-ua`, `sec-ch-ua-mobile`, `sec-ch-ua-platform`) and an explicit desktop `User-Agent`.
  - Missing Client Hints or mismatched headers trigger bot detection (403 Forbidden or endless CAPTCHA loops).
- **Centralized Header Factory (`_get_headers()`):**
  - Standardized across all portal requests (slot inspection, login, booking submission, OTP confirmation):
    ```python
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
    "Origin": self.base_url,
    "Referer": f"{self.base_url}/?lang=en_US",
    "X-Requested-With": "XMLHttpRequest",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    ```
### F. Dual-Pool Fleet Coordination & 6-Stage Booking Pipeline
- **Dedicated Architecture Document:** For complete operational and technical specifications, see [07-dual-pool-fleet-and-drop-planner.md](file:///e:/Alamia/KamalExpress-Agents/.ai/permanent/architecture/07-dual-pool-fleet-and-drop-planner.md).
- **Capability Segregation:** GVC portal accounts operate in dedicated roles (`SLOT_CHECKER`, `BOOKER`, or `HYBRID`):
  - *Slot Checkers:* Poll `/dates` and `/periods` and update `SlotDiscoveryCache` (3-5 min TTL). Auto-halt upon slot detection to conserve rate limits.
  - *Dedicated Bookers:* Pre-staged in hot standby with pre-warmed sessions and keep-alive connections.
- **6-Stage Deterministic Booking Execution:**
  1. *Hot Standby Initialization:* Sessions pre-warmed, proxies validated, and SIM phone mapped.
  2. *Slot Trigger & Atomic Lock:* `claim_next_client()` atomically locks client profile with strict precedence for client VAC/Visa preferences.
  3. *OTP Dispatch Initiation:* Booker fires verification request to GVC and listens on Universal OTP Event Bus.
  4. *Real-Time Webhook OTP Interception:* Android SMS forwarder relays SMS to `/api/otp/webhook` (<5ms regex extraction and zero-delay coroutine wakeup).
  5. *Zero-Delay Final Submission:* Complete JSON payload submitted via `curl_cffi` (`chrome120` impersonation).
  6. *Confirmation & Audit Broadcast:* Booking reference extracted, `client_queue` updated to `BOOKED`, and `SUCCESS` event broadcast to UI Activity Stream.

---

## 3. Database Schema Overview (SQLite WAL)

| Table | Purpose |
| :--- | :--- |
| `client_queue` | Visa applicants, passport details, destination, status, booking reference, and worker locking columns. |
| `gvc_portal_accounts` | Staff-owned GVC portal accounts, target VACs, visa types, proxy bindings, tokens, role (`SLOT_CHECKER`/`BOOKER`), and worker active flags. |
| `proxies` | Residential proxy pool, credentials, health stats, fail counts, and quarantine timestamps. |
| `users` | Staff and Admin credentials (PBKDF2-HMAC-SHA256 salted hashes), roles, and active states. |
| `otp_records` | Rolling and persisted SMS/WhatsApp OTP payloads ingested via webhook. |
| `system_logs` | Centralized system event audit trail, categorized logging (`BOOKING`, `AUTH`, `OTP`, etc.), and daily rotation. |
| `gvc_sessions` | Active and historical GVC authentication session tokens. |
| `system_settings` | Key-value settings (CapSolver keys, auth modes, solver credentials). |
| `visa_rules` | Requirements, embassy fees, processing times for Greece, Schengen, KSA, UAE, UK. |
| `hotels` & `hotel_bookings`| Hotel inventory and confirmed provisional reservation vouchers. |
