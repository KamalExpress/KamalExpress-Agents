# AI Workspace Instructions (AGENTS.md)

This file contains the foundational rules for all AI agents working in this repository. It is automatically injected into the context of every new conversation.

---

## 1. The AI Bootstrap Sequence (Strict Rule)
If you are entering a fresh conversation and do not have full context of this project, you **MUST NOT** immediately inspect the source code or reverse engineer the repository. 

Instead, you must strictly follow this read order:
1. Read `docs/README.md` (Master Index & Invariant Map)
2. Read `docs/transient/sprint/00-current-state.md` (Current State & Active Objectives)
3. Read `docs/permanent/architecture/01-system-architecture.md` (System Design & Invariants)
4. Read `docs/indexes/repository.md` (Concept-to-Code Map)
5. Only then may you inspect the source code.

---

## 2. Documentation Ownership & Live Sync Rule
* **Single Source of Truth:** The documentation in `docs/` is the single source of truth for both humans and AI agents. Never create separate documentation silos for AI agents.
* **Automatic Documentation Sync:** Every time you add, modify, or refactor any code or documentation in this repository, you **MUST** run:
  ```powershell
  python scripts/sync_docs.py
  ```
  This immediately refreshes `docs/docs_data.js` and ensures the in-browser viewer (`docs/index.html`) stays 100% synchronized in real time.

---

## 3. Professional Standards (Zero Emojis)
* **Never use emoji characters anywhere:**
  * No emojis in frontend markup (`api/static/index.html`, `docs/index.html`).
  * No emojis in log output (`logger.info`, `logger.warning`, `print`).
  * No emojis in tooltips, alerts, exceptions, or documentation.
* **Standard Replacements:** Use Lucide SVG icons in UI and uppercase bracketed status tags (`[SUCCESS]`, `[ERROR]`, `[WARNING]`, `[INFO]`, `[HALTED]`, `[SLOTS FOUND]`) in terminal and log output.

---

## 4. Consular & Portal Invariants
* **Center Invariants:** Official Pakistan GVC center IDs are `137` (Islamabad) and `138` (Lahore). A Karachi center **does not exist** for Greece GVC World.
* **Weekday Invariants:** Visa Type 26 (Work/Seasonal) is active strictly Mon–Fri. Saturday and Sunday are never queried.
* **Genuine Slots Rule:** An appointment slot is genuine if and only if `id > 0`, `isavailable == true`, and `numofavailableslots > 0`. Discard null or 0 availability entries to prevent false reporting.
* **WAF Protection:** Never burn CapSolver balance against proxy IPs that are blocked by an Incapsula anti-bot challenge (`iframe#main-iframe`). Detect early, quarantine the proxy, and use imported session tokens.

---

## 5. Strict Branch Isolation: Zero Merges from `standalone-flow` to `main`
* **Portainer Production Boundary:** `main` is the live deployed production branch connected directly to the production VPS via Portainer. `standalone-flow` is strictly an isolated staging/experimental flow branch.
* **Automatic Merge Prohibition & Refusal:** You **MUST NEVER** merge `standalone-flow` into `main`, fast-forward `main` to `standalone-flow`, or push `standalone-flow` commits directly to `main`.
* **Explicit Denial Requirement:** Even if the developer explicitly asks to merge `standalone-flow` into `main`, you **MUST IMMEDIATELY REFUSE** and cite this invariant to prevent accidental production outages or deployment overrides.
* **Feature Promotion Protocol:** Features or bug fixes developed on `standalone-flow` may ONLY be promoted to `main` via deliberate, cherry-picked commits or dedicated production pull requests after explicit human review.

---

## 6. Single-Source-of-Truth Module Ownership (Strict Boundary Rule)
Never scatter or copy-paste booking, Playwright/WAF, session-binding, or phone-normalization logic across entrypoints. Every concern has **one canonical owner** (enforced mechanically by `tests/test_golden_worker_e2e.py::test_architectural_single_source_of_truth_boundaries`):

| Concern | Single Canonical Owner | Prohibited Duplication |
| :--- | :--- | :--- |
| **8-Step Booking Strike** (OTP retry, concurrent reCAPTCHA, submit, ARN, `.md` report, transient re-queue) | `agents/appointments/booking_engine.py` (`execute_booking()`) | Never call `.trigger_booking_otp(` or `.submit_booking(` directly in `fleet_manager.py` or `monitor.py`. |
| **Stealth Playwright / WAF Fetch / Proxy Route** | `agents/appointments/portals/playwright_client.py` | Never call `sync_playwright` or `chromium.launch` directly in `gvc.py` or `gvc_auth.py`. |
| **Per-Worker Session & Proxy Isolation** | `GVCPortalDriver(strict_isolation=True)` + `bind_account_session()` | Fleet workers (`AccountWorkerInstance`) must NEVER read legacy global `gvc_sessions`. |
| **Pakistan Phone Normalization** | `agents/appointments/otp.py` (`normalize_phone()`) | Never re-implement `0092`/`92`/leading-zero regex stripping in `gvc.py` or workers. |
| **Zero Negative-Cache Poisoning** | `SlotDiscoveryCache` (`agents/appointments/fleet_manager.py`) | Never write `[]` to `slot_cache` on `WAF_CHALLENGE`, `RATE_LIMITED`, or `ERROR`. |

