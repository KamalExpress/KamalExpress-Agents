# ADR-001: Decoupling of Scout and Booker into a Dual-Pool Architecture

* **Status:** Accepted  
* **Deciders:** Kamal Express Engineering Team  
* **Date:** 2026-09-30  
* **Related Specs:** `docs/permanent/architecture/07-dual-pool-fleet-and-drop-planner.md`, `standalone_workers/WORKFLOW_SPECIFICATION.md`

---

## 1. Context and Problem Statement
When consular appointment drops occur on GVC World, dozens of appointment slots open simultaneously across multiple calendar dates, but remain available for only a brief 2–5 minute window before being claimed by other agencies. 

Early prototypes coupled slot discovery and booking into a single sequential loop. When an open slot was found, the thread stopped monitoring to trigger OTP and solve captchas. This created severe drawbacks:
1. If the booking failed or the slot was claimed by another party, the system lost visibility of all other open slots that had dropped on other dates.
2. High-frequency queries by the booking thread triggered Imperva HTTP 429 rate limits, burning the session before booking could finalize.
3. False positives in slot discovery caused premature OTP dispatches to applicants, frustrating clients.

---

## 2. Decision Drivers
* **Zero False Positives:** Must strictly verify `id > 0`, `isavailable == true`, and `numofavailableslots > 0`.
* **Zero Blind Spots:** Monitoring must continue uninterrupted even while a booking strike is underway.
* **Imperva Incapsula Rate Limit Protection:** Pacing must be jittered (`3.0s` to `12.0s`) with automatic backoff.

---

## 3. Considered Options
* **Option A: Monolithic Search & Book Worker:** Single process that checks calendar dates and immediately attempts booking on discovery.
* **Option B: Distributed Message Queue (RabbitMQ / Redis):** Heavy enterprise message broker coordinating separate containers.
* **Option C: Decoupled Dual-Pool Architecture (Scout Pool + Booker Pool):**
  * **Pool 1 (Scouts / `checker.py`):** Dedicated read-only workers scanning designated VAC centers and operational dates with jittered pacing. Emits discovered slots to persistent state.
  * **Pool 2 (Bookers / `booker.py`):** High-priority execution workers that ingest discovered candidate slots and execute the dynamic form scrape, parallel reCAPTCHA solve, and single-OTP submission loop.

---

## 4. Decision Outcome
Chosen option: **Option C (Decoupled Dual-Pool Architecture)**.

### Positive Consequences
* Continuous monitoring: Scout workers never stall waiting for human OTP input.
* Independent proxy pools: Scout queries and Booker transactions use separate residential IPs, isolating rate limits.
* Parallel reCAPTCHA v2 solving: Booker can solve captchas in the background while awaiting SMS OTP, reducing booking strike latency from ~45s to ~15s.

### Negative Consequences / Trade-offs
* Requires shared session and slot persistence state between workers.
* Addressed via SQLite in WAL mode and unified session manager.
