# Alamia-Skills-System

> **Universal, lifecycle-aware orchestration of development skills for AI pair programmers.**

Alamia-Skills-System is a portable, project-agnostic operating layer that sits above atomic AI skills (such as [Matt Pocock's `skills`](https://github.com/mattpocock/skills) catalog). It provides state-aware, lifecycle-driven orchestration for software development sessions.

Instead of requiring developers to remember, select, and manually chain dozens of granular skills, Alamia-Skills-System allows developers to interact through a small set of intuitive lifecycle commands:

```text
bootstrap  →  implement  →  qa  →  architecture-review  →  session-end
```

The system autonomously inspects the project, discovers its tech stack, test runners, and session memory conventions, and reconciles the necessary infrastructure without modifying existing project code.

---

## 1. Architectural Philosophy

### The Gap: Atomic Skills vs. Lifecycle Orchestration
The AI skills ecosystem pioneered by Matt Pocock established the **atomic skill unit**: small, composable, markdown-based prompt runbooks (e.g. `tdd`, `code-review`, `to-spec`, `diagnosing-bugs`).

However, atomic skills do not understand:
- **Project Lifecycle:** When should a skill be invoked? What prerequisites must run first?
- **Project State:** What branch are we on? What test runner is used? Where are session handoffs stored?
- **Session Continuity:** What was accomplished in the previous session, and what are the active pending tasks?

Alamia-Skills-System bridges this gap:

```text
                    Alamia-Skills-System
                            │
               Lifecycle Orchestration Layer
    (bootstrap → implement → qa → arch-review → session-end)
                            │
              State Engine & Universal Adapters
      (StackDetector, TestDetector, MemoryDetector)
                            │
               Atomic Skills (Matt Pocock)
       (tdd, code-review, to-spec, diagnosing-bugs)
                            │
                      Project State
```

### Core Abstraction Hierarchy
$$\text{Atomic Skill} \longrightarrow \text{Skill Group} \longrightarrow \text{Lifecycle Workflow} \longrightarrow \text{Project State}$$

* **Atomic Skill:** A single reusable technical capability (e.g., `tdd`, `code-review`).
* **Skill Group:** A functional cluster of capabilities serving a common domain (e.g., `testing`, `session-memory`).
* **Lifecycle Workflow:** A state-aware orchestration that evaluates project state, checks prerequisites, invokes atomic skills, and records state.
* **Project State:** The concrete reality of the codebase (git branch, test results, uncommitted changes, latest handoff).

---

## 2. Universal Portability & Zero Hardcoding

Alamia-Skills-System contains **zero hardcoded project assumptions**. It works identically in:
- **PHP / Laravel** (detects Composer, Pest/PHPUnit, Laravel specs)
- **Node.js / TypeScript / Next.js** (detects pnpm/npm, Vitest/Jest, React/Next)
- **Python** (detects pip/poetry, pytest, FastAPI/Django)
- **Rust / Go / Ruby** (detects Cargo, `go test`, Bundler)

### Pluggable Discovery Adapters (`core/adapters/`)
1. **`StackDetector`**: Discovers language, framework, package managers, and container configurations from project manifests.
2. **`TestDetector`**: Identifies test runners, commands (`pytest`, `./vendor/bin/pest`, `npm test`, `cargo test`), and test directories.
3. **`MemoryDetector`**: Detects session handoffs across common conventions (`.ai/transient/handoffs/`, `docs/handoffs/`, `handoffs/`, `CHANGELOG.md`). Dynamically parses the latest handoff and extracts active pending tasks.
4. **`AgentDetector`**: Detects existing agent configurations (`.agents/skills/`, `CLAUDE.md`, `.cursorrules`, `GEMINI.md`).

---

## 3. Directory Structure

```text
Alamia-Skills-System/
├── README.md                     # System documentation & usage guide
├── core/
│   ├── engine.py                 # Core Project State Engine (Case A–E classifier)
│   ├── reconciler.py             # Idempotent reconciler & Bootstrap report formatter
│   └── adapters/                 # Universal Discovery Adapters
│       ├── base.py               # BaseDetector interface
│       ├── stack_detector.py     # Language & framework detector
│       ├── test_detector.py      # Test runner & command detector
│       ├── memory_detector.py    # Session handoff & architecture detector
│       └── agent_detector.py     # Agent environment & rules detector
├── lifecycle/                    # Universal lifecycle orchestrators
│   ├── bootstrap/SKILL.md        # Runs discovery & reconciles project profile
│   ├── implement/SKILL.md        # Pulls tasks from memory & routes to atomic skills
│   ├── qa/SKILL.md               # Executes detected test runner & static checks
│   ├── architecture-review/SKILL.md # Analyzes changes against discovered architecture
│   └── session-end/SKILL.md      # Formats next sequential handoff in native style
├── templates/                    # Starter templates for unconfigured projects
│   ├── handoff-template.md       # Standard session handoff template
│   ├── architecture-template.md  # Baseline architecture specification template
│   └── project.example.json      # Reference configuration schema
├── vendor/
│   └── mattpocock-skills/        # Bundled atomic skills (tdd, code-review, etc.)
└── tests/
    └── test_portability.py       # Automated cross-stack discovery & idempotency tests
```

---

## 4. How to Install & Drop into Any Project

### Step 1: Copy the Folder
Copy the `Alamia-Skills-System/` directory directly into the root of any target software repository:

```bash
cp -r /path/to/Alamia-Skills-System /path/to/your-project/
```

### Step 2: Bootstrap the Project
Open your AI coding assistant (Antigravity, Claude Code, Cursor, etc.) and say:

> **"Bootstrap this project"**

The system executes the discovery reconciler:
```bash
python Alamia-Skills-System/core/reconciler.py
```

### What Happens During Bootstrap:
1. **Detects Project State:** Classifies the project into one of five states:
   * **Case A (Fresh Project):** No code exists; sets up clean baseline.
   * **Case B (Existing Unconfigured):** Discovers code/tests/docs; establishes lifecycle without modifying project code.
   * **Case C (Partially Configured):** Fills only missing lifecycle capabilities.
   * **Case D (Configured & Healthy):** Validates environment and reports active tasks.
   * **Case E (Outdated):** Non-destructively re-syncs configuration.
2. **Generates `.alamia/project.json`:** Stores the discovered tech stack, test runner, and memory paths.
3. **Mounts Lifecycle Skills:** Configures `.agents/skills/` so lifecycle commands are immediately available.
4. **Wired Atomic Skills:** Configures `.agents/skills.json` pointing to bundled atomic skills.
5. **Returns Kickoff Report:** Displays detected stack, active branch, and immediate tasks from the last handoff.

---

## 5. Daily Developer Workflow

Once bootstrapped, developers do not need to memorize individual skills. The entire session lifecycle is handled through 5 commands:

### 1. `bootstrap` (Start of Session)
* **What it does:** Reconciles the environment, checks git branch/commit, reads the latest session handoff, and extracts pending tasks.
* **Command:** *"Bootstrap this project"* or *"Start session"*.

### 2. `implement` (Active Development)
* **What it does:** Reads the active task (or picks the top pending task from the latest handoff), consults the project's architecture documents, and orchestrates atomic skills (`/to-spec`, `/tdd`, `/prototype`).
* **Command:** *"Implement [feature]"* or *"Start coding"*.

### 3. `qa` (Validation & Testing)
* **What it does:** Dynamically runs the project's detected test runner (`pytest`, `pest`, `vitest`, `cargo test`) and linters. If a test fails, routes to `/diagnosing-bugs`.
* **Command:** *"Run QA"* or *"Test this"*.

### 4. `architecture-review` (Drift Governance)
* **What it does:** Compares recent git changes against the project's architectural specifications, checking for boundary violations, layer bypasses, and pattern inconsistency.
* **Command:** *"Review architecture"* or *"Check drift"*.

### 5. `session-end` (Wrap-Up & Handoff)
* **What it does:** Verifies working tree status, checks test health, and drafts the **next sequential handoff** (e.g. `08-session-handoff-...md` in `.ai/transient/handoffs/` or `docs/handoffs/`) in the project's exact native format.
* **Command:** *"End session"* or *"Wrap up"*.

---

## 6. Self-Containment & Offline Support

Alamia-Skills-System is **100% self-contained**:
* **Pre-bundled:** The complete catalog of Matt Pocock atomic skills is bundled directly in `Alamia-Skills-System/vendor/mattpocock-skills/`. No external network calls or npm installs are needed.
* **Auto-Pull Fallback:** If `Alamia-Skills-System` is copied as a lightweight bundle without the `vendor/` directory, the reconciler automatically detects its absence and clones it from GitHub on demand:
  ```python
  git clone --depth 1 https://github.com/mattpocock/skills.git
  ```

---

## 7. Running the Automated Tests

Verify discovery, portability, and idempotency across different programming stacks using the included test suite:

```bash
python Alamia-Skills-System/tests/test_portability.py
```

Expected output:
```text
[PASS] Laravel project discovery verified successfully!
[PASS] Next.js / TypeScript discovery verified successfully!
[PASS] Fresh project (Case A) verified successfully!
[PASS] Reconciler idempotency verified successfully!

ALL PORTABILITY TESTS PASSED!
```

---

## 8. License & Attribution

- **Alamia-Skills-System:** Developed by Alamia AI.
- **Atomic Skills Catalog:** Reuses and builds upon the atomic skills ecosystem created by [Matt Pocock](https://github.com/mattpocock/skills) under MIT License.
