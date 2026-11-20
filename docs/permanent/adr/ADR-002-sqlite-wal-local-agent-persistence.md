# ADR-002: SQLite in WAL Mode with In-Memory Waiters for Local Concurrency

* **Status:** Accepted  
* **Deciders:** Kamal Express Engineering Team  
* **Date:** 2026-09-29  
* **Related Specs:** `docs/permanent/architecture/01-system-architecture.md`, `agents/appointments/db.py`

---

## 1. Context and Problem Statement
The Kamal Express AI Platform coordinates multiple autonomous agents (Visa, Appointments, Hotels, Umrah), background slot monitors, a multi-account GVC operator fleet, and incoming mobile OTP webhook events. 

The storage layer must support:
1. High-frequency concurrent reads and writes from multiple Python threads and processes without lock contention.
2. Zero-latency in-memory notification when an SMS OTP arrives via the webhook endpoint (`/api/otp/webhook`).
3. Frictionless local portability: running on developer workstations, Windows desktops, and VPS Docker containers without configuring external database services (PostgreSQL/MySQL).

---

## 2. Decision Drivers
* **Zero Operational Overhead:** No external database daemon to configure, secure, or maintain.
* **Instant Concurrency:** Multi-reader, single-writer performance without `sqlite3.OperationalError: database is locked`.
* **Real-time Event Resolution:** Instant unblocking of active booking tasks awaiting an OTP without tight polling loops.

---

## 3. Considered Options
* **Option A: PostgreSQL with Redis:** Heavyweight client-server architecture with Redis Pub/Sub.
* **Option B: Plain SQLite (Default Rollback Journal):** Lightweight, but prone to lock timeouts under multi-threaded writes.
* **Option C: SQLite with Write-Ahead Logging (WAL) + In-Memory Asyncio Event Waiters:**
  * SQLite configured with `PRAGMA journal_mode=WAL;` and `PRAGMA busy_timeout=5000;`.
  * In-memory `asyncio.Event` registry in Python: active booking tasks register a future for `+92-XXX`; incoming webhooks resolve the event instantly in 0ms.

---

## 4. Decision Outcome
Chosen option: **Option C (SQLite WAL + In-Memory Event Waiters)**.

### Positive Consequences
* Reads do not block writes, and writes do not block reads.
* Complete system state resides in a single self-contained file (`data/kamal_express.db`).
* Sub-millisecond OTP dispatch from webhook ingestion to active booking submission.
* 1-Click full database backup and restore via `/api/admin/export-data` and `/api/admin/restore-data`.

### Negative Consequences / Trade-offs
* Multi-server clustering is not natively supported (solved by running as a single container stack with Portainer).
