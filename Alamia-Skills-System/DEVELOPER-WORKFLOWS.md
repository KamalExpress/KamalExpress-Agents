# Developer Workflows: Alamia-Skills-System in Action

> **A practical guide demonstrating how developers interact with the Alamia-Skills-System lifecycle operating model in any software project.**

---

## 1. The Interaction Model

Traditional AI pair programming forces developers into **tool and prompt micromanagement**:
- *"Read file A, check git status, see what we did in the last session."*
- *"Run our test runner on the test folder."*
- *"Review the code smells in this new function."*
- *"Write a handoff note for next time."*

With **Alamia-Skills-System**, developers communicate at the **lifecycle stage level**. You use 5 high-level commands, and the system autonomously resolves project state, discovers the stack, executes the correct test runner, and chains atomic skills:

```text
┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌─────────────────────┐     ┌──────────────┐
│  bootstrap   │ ──> │  implement   │ ──> │      qa      │ ──> │ architecture-review │ ──> │ session-end  │
│ (Kickoff/Ctx)│     │ (Coding/TDD) │     │ (Validation) │     │  (Drift Governance) │     │ (Next Handoff│
└──────────────┘     └──────────────┘     └──────────────┘     └─────────────────────┘     └──────────────┘
```

The system contains **zero hardcoded project assumptions**. Below is a concrete walkthrough demonstrating the developer experience using an illustrative web service (`payment-service`).

---

## Workflow 1: Starting a Work Session (`bootstrap`)

### The Developer's Input
```text
"Bootstrap this project"
```
*(or simply `"Start session"`)*

### Behind the Scenes: What Alamia-Skills-System Does
1. **Dynamic Stack Discovery (`core/adapters/stack_detector.py`):**
   - Inspects manifests (`package.json`, `composer.json`, `pyproject.toml`, `Cargo.toml`, etc.) to identify the language, frameworks, package managers, and container setups.
2. **Dynamic Test Runner Discovery (`core/adapters/test_detector.py`):**
   - Automatically determines the test runner and command (e.g. `pytest`, `npm test`, `npx vitest`, `./vendor/bin/pest`, `cargo test`).
3. **Session Memory Discovery (`core/adapters/memory_detector.py`):**
   - Scans for existing session memory across common conventions (`docs/handoffs/`, `.ai/transient/handoffs/`, `handoffs/`, `CHANGELOG.md`).
   - Parses the latest handoff file, detects its numbering sequence, and extracts active pending objectives.
4. **Git Inspection:**
   - Detects the active branch, latest commit, and uncommitted changes.
5. **Idempotent Reconciliation (`core/reconciler.py`):**
   - Generates or reconciles `.alamia/project.json` and connects lifecycle commands without touching project business code.

### The System's Response to the Developer
```markdown
# Alamia-Skills-System: Bootstrap & Reconciliation Report

**Project:** `payment-service` | **Status:** `Case D` (Configured & Reconciled)

---

### 1. Discovered Stack & Environment
- **Primary Language:** `typescript` (Frameworks: `express, prisma`)
- **Package Manager:** `pnpm` | **Containers:** `docker-compose`

### 2. Testing Configuration
- **Runner:** `vitest` | **Command:** `pnpm test` | **Dir:** `tests/`

### 3. Discovered Project Memory
- **Handoff Location:** `docs/handoffs/`
- **Latest Handoff:** `docs/handoffs/04-webhook-integration.md`
- **Active Pending Objectives:**
  1. Add HMAC-SHA256 signature verification to webhook endpoints
  2. Implement Redis-backed idempotency key cache
  3. Add retry backoff policy for failed provider notifications
  4. Integration test suite for webhook replays

### 4. Git State
- **Branch:** `feature/webhooks` (`9a14c2b add webhook route skeleton`)
- **Working Tree:** Clean

---
**Ready.** You can now say `implement`, `qa`, `architecture-review`, or `session-end`.
```

---

## Workflow 2: Active Feature Implementation (`implement`)

### Scenario A: Working on the Next Pending Objective
The developer does not need to copy-paste tasks from the previous session:

```text
"Implement the next pending objective"
```

#### What Alamia-Skills-System Does:
1. Consults `.alamia/project.json` $\rightarrow$ inspects the latest handoff $\rightarrow$ selects Task 1: **"Add HMAC-SHA256 signature verification to webhook endpoints"**.
2. Reads the relevant architectural specification from the project's detected architecture directory (e.g., `docs/architecture/02-security-boundaries.md`).
3. Invokes Matt Pocock's atomic skill `/tdd`:
   - Writes a unit test in `tests/webhooks/signature.test.ts` asserting that requests with invalid signatures receive HTTP 401, and valid signatures pass.
4. Implements the signature verification middleware in `src/middleware/verifyWebhook.ts`.
5. Executes the project's detected test runner (`pnpm test tests/webhooks/signature.test.ts`) to verify the green phase.
6. Hands control back to the developer:
   > *"Implemented HMAC-SHA256 webhook signature verification. Targeted unit tests passing. Ready for `qa`."*

---

### Scenario B: Working on a Custom User-Specified Feature
The developer specifies a custom task:

```text
"Implement Redis-backed idempotency key cache for checkout requests"
```

#### What Alamia-Skills-System Does:
1. Recognizes user-specified task overrides default handoff queue.
2. Reads architecture for caching patterns in `docs/architecture/`.
3. If requirements are ambiguous, invokes atomic skill `/to-spec` to clarify TTL and failure behavior before writing code.
4. Implements the cache middleware and updates the route handler.
5. Runs immediate test verification and prompts:
   > *"Idempotency middleware implemented. Ready for `qa`."*

---

## Workflow 3: Validation & Quality Assurance (`qa`)

### The Developer's Input
```text
"Run QA"
```
*(or `"Test changes"`, `"Validate"`)*

### Behind the Scenes: What Alamia-Skills-System Does
1. Checks `.alamia/project.json` for the discovered test runner command (e.g. `pnpm test`, `pytest`, `./vendor/bin/pest`, `cargo test`).
2. Executes the test suite against changed modules and full regressions.
3. Runs configured linters or typecheckers (e.g., `tsc --noEmit`, `eslint`, `mypy`).
4. **Failure Handling:** If a test fails, the orchestrator automatically invokes Matt Pocock's `/diagnosing-bugs` atomic skill, isolates the root cause, and proposes the minimal fix.

### The System's Response to the Developer
```markdown
## QA Verification Matrix

| Target | Tests Run | Passed | Failed | Duration |
| :--- | :---: | :---: | :---: | :---: |
| `tests/webhooks/signature.test.ts` | 6 | 6 | 0 | 0.38s |
| `tests/api/checkout.test.ts` | 14 | 14 | 0 | 0.82s |

- **Automated Tests:** PASSED
- **Type Checking:** 0 errors (`tsc --noEmit` clean)
- **Regressions:** None detected
- **Overall QA Status:** **PASSED (Green)**
```

---

## Workflow 4: Architectural Drift Governance (`architecture-review`)

### The Developer's Input
```text
"Review architecture"
```
*(or `"Check drift"`)*

### Behind the Scenes: What Alamia-Skills-System Does
1. Checks `.alamia/project.json` for `memory.architecture_dir`.
2. Inspects `git diff` against the base branch (`main` or merge-base).
3. Invokes Matt Pocock's two-axis `/code-review` atomic skill:
   - **Axis 1 (Standards):** Checks against Fowler smell baseline (Feature Envy, Data Clumps, Shotgun Surgery).
   - **Axis 2 (Spec & Architecture):** Validates against the project's documented architecture rules:
     - *Rule:* Does the middleware talk to raw redis clients directly or through the shared cache abstraction?
     - *Rule:* Are secret keys hardcoded or retrieved through the configuration manager?
     - *Rule:* Are domain boundaries respected?

### The System's Response to the Developer
```markdown
## Architecture Review & Drift Analysis

### Axis 1: Standards & Code Smells
- **Duplicated Code:** None detected.
- **Shotgun Surgery:** Changes isolated strictly to `src/middleware/` and `tests/`.

### Axis 2: Architectural Alignment (docs/architecture/02-security-boundaries.md)
- **Secret Management:** Webhook secret resolved via `config.get('WEBHOOK_SECRET')`.
- **Layer Boundaries:** Clean separation; middleware acts as a gate before controllers.
- **Verdict:** **ALIGNED (Zero Architectural Drift)**
```

---

## Workflow 5: Ending a Session & Preserving Continuity (`session-end`)

### The Developer's Input
```text
"End session"
```
*(or `"Wrap up"`, `"Handoff"`)*

### Behind the Scenes: What Alamia-Skills-System Does
1. Inspects Git status (`git status --short`) to catalog all modified, added, and untracked files.
2. Verifies latest test suite health.
3. Detects the project's memory convention:
   - Identifies sequential numbering in `docs/handoffs/`.
   - Previous handoff was `04-webhook-integration.md`.
   - **Calculates the next sequence:** `docs/handoffs/05-webhook-signature-verification.md`.
4. Writes the new handoff adhering strictly to the established format:
   - *Objectives Achieved in This Session*
   - *Key Verification Artifacts & System State*
   - *Pending Work / Next Session Objectives* (carries forward unfinished tasks).
5. Reports Git status cleanly without auto-pushing.

### The Generated Artifact: `docs/handoffs/05-webhook-signature-verification.md`
```markdown
# Session Handoff 05: Webhook Signature Verification & Idempotency Cache

> **Session Date:** 2026-10-05  
> **Branch:** `feature/webhooks`  
> **Status:** Verified (Vitest Green)  

---

## 1. Objectives Achieved in This Session
1. **HMAC-SHA256 Webhook Signature Verification:**
   - Implemented `src/middleware/verifyWebhook.ts` checking timing-safe signatures.
2. **Redis Idempotency Cache:**
   - Integrated duplicate request rejection with 24-hour sliding TTL.

---

## 2. Key Verification Artifacts & System State
| Component | State / Verification | Location |
| :--- | :--- | :--- |
| **Signature Middleware** | 6 unit tests passing | `tests/webhooks/signature.test.ts` |
| **Idempotency Cache** | 14 integration tests passing | `tests/api/checkout.test.ts` |

---

## 3. Pending Work / Next Session Objectives
1. **Add retry backoff policy for failed provider notifications:**
   - Implement exponential backoff queue in `src/workers/notificationRetry.ts`.
2. **Integration test suite for webhook replays:**
   - Simulate out-of-order network replay attacks in `tests/e2e/webhooks.test.ts`.
```

---

## Cross-Stack Adaptability

Because Alamia-Skills-System uses universal discovery adapters, the exact same five commands work identically across different tech stacks:

| Tech Stack | What `bootstrap` Detects | What `qa` Runs | Memory Convention Supported |
| :--- | :--- | :--- | :--- |
| **PHP / Laravel** | PHP 8.x, Composer, Laravel | `./vendor/bin/pest` or `phpunit` | `docs/handoffs/`, `notes/`, `CHANGELOG.md` |
| **Node / Next.js / TS** | TypeScript, pnpm/npm, Next.js | `npx vitest` or `npm test` | `docs/handoffs/`, `.scratch/`, `docs/sessions/` |
| **Python / FastAPI / Django** | Python 3.x, pip/poetry, FastAPI | `pytest` | `.ai/transient/handoffs/`, `docs/handoffs/` |
| **Rust** | Rust, Cargo | `cargo test` | `docs/handoffs/`, `CHANGELOG.md` |
| **Go** | Go, `go.mod` | `go test ./...` | `docs/handoffs/`, `notes/` |

---

## Developer Cheatsheet

| Command | What to Say | What Alamia Does | Next Recommended Step |
| :--- | :--- | :--- | :--- |
| **`bootstrap`** | *"Bootstrap project"* / *"Start session"* | Discovers stack, tests, git state, and extracts last handoff's pending tasks. | Proceed to `implement`. |
| **`implement`** | *"Implement next task"* / *"Implement X"* | Reads architecture spec, routes to `/to-spec` or `/tdd`, writes code & tests. | Proceed to `qa`. |
| **`qa`** | *"Run QA"* / *"Validate changes"* | Dynamically executes detected test runner & linters; isolates bugs with `/diagnosing-bugs`. | Proceed to `architecture-review`. |
| **`architecture-review`** | *"Review architecture"* / *"Check drift"* | Compares diff against architecture specs using two-axis `/code-review`. | Proceed to `session-end`. |
| **`session-end`** | *"End session"* / *"Wrap up"* | Verifies tests, catalogs git diff, and writes the next sequential handoff file. | Session closed cleanly. |
