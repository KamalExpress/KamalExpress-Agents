# Sprint Plan: Dual-Pool Fleet, Operations Coordinator & Persistent Live Activity Stream

**Sprint Start:** 2026-09-30  
**Target Architecture Document:** [`.ai/permanent/architecture/07-dual-pool-fleet-and-drop-planner.md`](file:///e:/Alamia/KamalExpress-Agents/.ai/permanent/architecture/07-dual-pool-fleet-and-drop-planner.md)  
**Status:** Enterprise Standards & Professional Terminology Approved.

---

## Sprint Task Breakdown

### Phase 1: Unified Activity Stream & Persistent Logs
- [ ] Create `system_logs` SQLite table in `agents/appointments/db.py`.
- [ ] Implement central logging helper (`log_system_event`) with daily rotating file writer (`data/logs/activity_YYYY-MM-DD.log`) and 200-event in-memory buffer.
- [ ] Add REST endpoints in `api/main.py`: `GET /api/logs/stream`, `GET /api/logs/export`, `DELETE /api/logs/clear`.
- [ ] Update UI `#log-feed` in `api/static/index.html`:
  - [ ] Add Configurable Refresh Speed dropdown (`3s`, `5s`, `15s`, `30s`, `1m`, `5m`, `Paused`).
  - [ ] Add `[ ⏸ Pause Stream ]` and `[ ⬇ Export Logs ]` buttons.
  - [ ] Format color highlights: Prominent Red for `AUTH` / `LOGIN FAILED` / `SESSION EXPIRED`, Bold Green for `BOOKED`, Vibrant Blue for `SLOTS FOUND`, Amber for `OTP`.

### Phase 2: Dual-Pool Account Roles & Shared Slot Cache
- [ ] Add `account_role` (`SLOT_CHECKER`, `BOOKER`, `HYBRID`) and `worker_persona_name` to `gvc_portal_accounts` table.
- [ ] Implement thread-safe `SlotDiscoveryCache` (configurable 3–5 min TTL).
- [ ] Implement Auto-Halt on slot discovery (halts availability checks once slots are found) + UI button to "Reschedule Slot Checks".

### Phase 3: Operations Coordinator & Fleet Pre-Staging
- [ ] Implement "Pre-Stage Active Booking Fleet" (Hot Standby) trigger in `fleet_manager.py`.
- [ ] Add Scheduled Release Window countdown timer on operations dashboard.
- [ ] Add Operations Plan & Readiness Briefing card:
  - Total clients vs. active bookers.
  - Multi-wave booking forecast (e.g. 10 clients across 5 bookers = 2 waves).
  - Staff operational readiness checklist (SMS forwarder, masked SIM status, proxy health).

### Phase 4: Bulk CSV / TXT Queue Upload & Example Template
- [ ] Add `GET /api/clients/template` endpoint for ready-to-use CSV template download.
- [ ] Add `POST /api/clients/bulk-upload` endpoint supporting CSV & formatted TXT files.
- [ ] Add "Upload Bulk CSV/TXT" modal with file dropzone and "Download Sample CSV Template" button in UI.
- [ ] Verify multi-wave FIFO claiming across workers.

### Phase 5: Remote Worker Personas & PII Masking
- [ ] Assign Pakistani operator names (*Tariq Mehmood*, *Yaqoob Masih*, *Hamza Malik*, *Daniel Gill*, etc.) to workers.
- [ ] Mask all phone numbers across UI and logs (`+92-334-***-2969`).
- [ ] Display professional operator badges on Fleet cards in dashboard.
