# Sprint Plan: Dual-Pool Fleet, Drop War Room & Persistent Live Activity Stream

**Sprint Start:** 2026-09-30  
**Target Architecture Document:** [`.ai/permanent/architecture/07-dual-pool-fleet-and-drop-planner.md`](file:///e:/Alamia/KamalExpress-Agents/.ai/permanent/architecture/07-dual-pool-fleet-and-drop-planner.md)  
**Status:** Architecture Designed & Approved for Execution.

---

## Sprint Task Breakdown

### Phase 1: Unified Activity Stream & Persistent Logs
- [ ] Create `system_logs` SQLite table in `agents/appointments/db.py`.
- [ ] Implement central logging helper (`log_system_event`) with daily rotating file writer (`data/logs/activity_YYYY-MM-DD.log`) and 200-event in-memory buffer.
- [ ] Add REST endpoints in `api/main.py`: `GET /api/logs/stream`, `GET /api/logs/export`, `DELETE /api/logs/clear`.
- [ ] Update UI `#log-feed` in `api/static/index.html`:
  - [ ] Add Configurable Refresh Speed dropdown (`3s`, `5s`, `15s`, `30s`, `1m`, `5m`, `Paused`).
  - [ ] Add `[ ⏸ Pause Stream ]` and `[ ⬇ Export Logs ]` buttons.
  - [ ] Format color highlights: Red for `AUTH` / `LOGIN FAILED`, Green for `BOOKED`, Blue for `SLOTS FOUND`, Orange for `OTP`.

### Phase 2: Dual-Pool Account Roles & Shared Slot Cache
- [ ] Add `account_role` (`SLOT_CHECKER`, `BOOKER`, `HYBRID`) and `worker_persona_name` to `gvc_portal_accounts` table.
- [ ] Implement thread-safe `SlotDiscoveryCache` (configurable 3–5 min TTL).
- [ ] Implement Auto-Halt on slot discovery (stops redundant checking once slots are found) + UI button to "Reschedule Checks".

### Phase 3: Drop War Room & "Arm Booker" Hot Standby
- [ ] Implement "Arm All Bookers" (Hot Standby) trigger in `fleet_manager.py`.
- [ ] Add Scheduled Drop Time countdown timer on dashboard.
- [ ] Add Admin Mission Plan briefing card:
  - Total clients vs. active bookers.
    - Multi-wave booking forecast (e.g. 10 clients across 5 bookers = 2 waves).
  - Staff operational checklist (Android SMS forwarder, SIM status, proxy health).

### Phase 4: Bulk CSV / TXT Queue Upload
- [ ] Add `POST /api/clients/bulk-upload` endpoint supporting CSV & formatted TXT files.
- [ ] Add "Upload Bulk CSV/TXT" modal and file dropzone in UI.
- [ ] Verify multi-wave FIFO claiming across workers.

### Phase 5: Remote Worker Personas
- [ ] Assign Pakistani names (*Tariq Mehmood*, *Yaqoob Masih*, *Hamza Malik*, *Daniel Gill*, etc.) to workers.
- [ ] Display persona badges on Fleet cards in dashboard.
