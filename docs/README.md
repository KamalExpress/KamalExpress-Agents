# Kamal Express Knowledge Base Master Index

Welcome to the unified documentation and AI knowledge base for the **Kamal Express AI Platform**.
This repository provides a single, synchronized source of truth for both human engineers and autonomous AI agents.

---

## The AI Bootstrap Read Order
If you are an AI Agent entering a fresh conversation, you **must** read the following documents in order before inspecting the source code:

1. `README.md` (You are here: Master Index)
2. `permanent/architecture/01-system-architecture.md` (High-level system design & invariants)
3. `indexes/repository.md` (Concept-to-code mapping)
4. `permanent/standards/01-coding-standards.md` (Conventions, testing, zero-emoji rules)
5. `transient/sprint/00-current-state.md` (Current sprint objectives & active status)

---

## In-Browser Documentation Viewer
To browse this entire knowledge base with interactive search, instant Table of Contents, and syntax highlighting:
* **Offline / Serverless:** Double-click [`docs/index.html`](file:///e:/Alamia/KamalExpress-Agents/docs/index.html) in any web browser. Zero server process or internet connection required.
* **Via Running Server:** Open `http://localhost:8080/documentation` (or `/docs-viewer/`).
* **Instant Sync Command:** Whenever any `.md` file is edited or added, run:
  ```powershell
  python scripts/sync_docs.py
  ```

---

## Directory Architecture

### 1. Permanent Knowledge (Lives for Years)
* **[`permanent/architecture/`](file:///e:/Alamia/KamalExpress-Agents/docs/permanent/architecture/)**: High-level system design, multi-account fleet engine, portainer/VPS deployments, and portal invariants.
  * `01-system-architecture.md`: Control plane, LangGraph multi-agent triage, SQLite WAL persistence.
  * `07-dual-pool-fleet-and-drop-planner.md`: Dual-pool Scout/Booker fleet coordination.
  * `08-failproof-autonomous-workers-specification.md`: Official GVC Pakistan center invariants (`137` ISB, `138` LHE).
  * `09-umrah-package-catalog-and-pricing-specification.md`: Umrah hotel tiers, transport rates, package formulas.
  * `portainer-cloudflare.md`: Container stack & Zero Trust tunnel deployment.
  * `hetzner-vps.md`: Production VPS setup, environment secrets, and systemd units.
* **[`permanent/adr/`](file:///e:/Alamia/KamalExpress-Agents/docs/permanent/adr/)**: Architecture Decision Records.
  * `ADR-001-dual-pool-scout-booker-decoupling.md`: Decoupling Scout (`checker.py`) and Booker (`booker.py`).
  * `ADR-002-sqlite-wal-local-agent-persistence.md`: SQLite WAL with in-memory waiters for multi-operator concurrency.
  * `ADR-003-browser-context-waf-clearance.md`: In-browser stealth execution for Imperva WAF cookie handshakes.
* **[`permanent/glossary/`](file:///e:/Alamia/KamalExpress-Agents/docs/permanent/glossary/)**: Consular codes, visa categories, GVC endpoint definitions.
* **[`permanent/standards/`](file:///e:/Alamia/KamalExpress-Agents/docs/permanent/standards/)**: Coding standards, zero-emoji rule, PII confidentiality, and test protocols.

### 2. Transient Knowledge (Lives for Days / Weeks)
* **[`transient/sprint/`](file:///e:/Alamia/KamalExpress-Agents/docs/transient/sprint/)**: Active milestone and immediate blockers (`00-current-state.md`).
* **[`transient/handoffs/`](file:///e:/Alamia/KamalExpress-Agents/docs/transient/handoffs/)**: Chronological record of all sprints from project inception to current date:
  * `01-session-handoff-dual-auth-shadcn.md` (Dual auth, shadcn UI)
  * `02-session-handoff-fleet-otp-export.md` (Fleet accounts, OTP export)
  * `03-sprint-plan-dual-pool-and-activity-stream.md` (Dual-pool sprint plan)
  * `04-session-handoff-autonomous-workers-and-worker-pool.md` (Worker pool)
  * `05-session-handoff-live-scout-capsolver-and-1click-launchers.md` (CapSolver integration)
  * `06-session-handoff-standalone-workers-workflow-verified.md` (Live verified runs)
  * `07-session-handoff-dashboard-autonomous-scout-and-autologin.md` (Dashboard autologin)
  * `08-session-handoff-cloudflare-ai-streaming-and-package-hardening.md` (AI streaming fix)
  * `09-session-handoff-live-fleet-gvc-auth-waf-and-docs-viewer.md` (WAF early detection, zero-emoji purge, docs hub)
* **[`transient/backlog/`](file:///e:/Alamia/KamalExpress-Agents/docs/transient/backlog/)**: Active backlog and future enhancement roadmap.

### 3. History & Lessons
* **[`history/`](file:///e:/Alamia/KamalExpress-Agents/docs/history/)**: Architecture evolution and timeline.
* **[`lessons/`](file:///e:/Alamia/KamalExpress-Agents/docs/lessons/)**: Operational troubleshooting playbooks and incident post-mortems:
  * `gvc-imperva-waf-troubleshooting.md`: Diagnosing and recovering from HTTP 472 / Incapsula challenges.
  * `sms-otp-forwarder-sanitization.md`: Sanitizing Android mobile forwarder payloads.
  * `ai-streaming-gateway-fix.md`: Resolving Cloudflare AI Gateway token streaming.
* **[`indexes/`](file:///e:/Alamia/KamalExpress-Agents/docs/indexes/)**: `repository.md` concept-to-code mapping.
