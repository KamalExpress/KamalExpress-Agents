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

---

## 2. Standard Greek VAC & Visa Category Codes

| Code | Name / Category | Default |
| :--- | :--- | :--- |
| `138` | Islamabad VAC | ✅ Default |
| `137` | Karachi VAC | — |
| `139` | Lahore VAC | — |
| `26` | Long-Term Type D (Seasonal/Dependent Employment) | ✅ Default |
| `0` | Submission Schengen Visa (Short term – Type C) | — |
| `2` | National visa (Long term - type D) | — |
| `5` | Premium Lounge | Optional Service |
| `6` | Prime Time | Optional Service |
