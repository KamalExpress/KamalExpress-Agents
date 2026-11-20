---
name: docs
description: Use this skill when the developer says "docs", "scaffold docs", "sync docs", "update docs", or "doc audit". Initializes the unified knowledgebase architecture, synchronizes markdown files into the in-browser viewer, and audits documentation coverage.
---

# Universal Documentation & Knowledgebase Lifecycle Skill

> **Skill Type:** Universal Project-Agnostic Lifecycle Orchestrator  
> **Target Scope:** Documentation Scaffolding, Synchronization, and Knowledge Management  
> **Philosophy:** Dual-Readership (One single source of truth for both Human Developers and AI Agents).

---

## 1. When to Use This Skill
Activate this skill whenever:
* Starting a new project and needing the canonical AI memory + human documentation architecture (`docs:init`).
* Adding, modifying, or refactoring code and needing to ensure documentation reflects recent changes (`docs:sync`).
* Wrapping up a sprint or checking for missing documentation or architectural drift (`docs:audit`).

---

## 2. Core Workflows

### Workflow 1: Initialize Unified Knowledgebase (`docs:init`)
Runs `core.doc_scaffolder` to initialize the standard documentation and AI memory architecture:
* Scaffolds `docs/permanent/` (architecture, adr, glossary, standards).
* Scaffolds `docs/transient/` (sprint state `00-current-state.md`, handoffs, backlog).
* Scaffolds `docs/history/`, `docs/lessons/`, `docs/indexes/`.
* Creates `docs/README.md` with the official **AI Bootstrap Read Order**.
* Injects `.agents/AGENTS.md` with **Documentation Ownership & AI Instructions**.
* Installs the self-contained in-browser documentation viewer (`docs/index.html` + `docs/assets/marked.min.js`).
* Installs `scripts/sync_docs.py`.

```powershell
python -m core.doc_scaffolder
```

---

### Workflow 2: Synchronize In-Browser Documentation (`docs:sync`)
Runs `core.doc_engine` to scan all markdown specifications, extract structural metadata, and emit `docs/docs_data.js`:
* Scans all markdown files in `docs/` and root modules.
* Preserves raw markdown text for client-side runtime rendering.
* Updates the live search index, table of contents, and category tree.

```powershell
python scripts/sync_docs.py
# Or directly via Alamia-Skills-System engine:
python -m core.doc_engine
```

---

### Workflow 3: Documentation Coverage Audit (`docs:audit`)
Evaluates documentation coverage against git state:
* Inspects `git status` and recent commits.
* Checks if newly introduced features or endpoints have corresponding specs or handoff notes.
* Verifies `docs/transient/sprint/00-current-state.md` matches the active branch objectives.

---

## 3. The AI Bootstrap Sequence (Strict Protocol)
When an AI agent starts a conversation, it must strictly follow this reading sequence before inspecting code:
1. `docs/README.md` (Master Index)
2. `docs/transient/sprint/00-current-state.md` (Current State & Active Blockers)
3. `docs/permanent/architecture/01-system-architecture.md` (System Boundaries & Invariants)
4. `docs/indexes/repository.md` (Concept-to-Code Map)
5. Source code inspection.

---

## 4. Invariant Rules
* **Single Source of Truth:** Never maintain separate documentation silos for AI agents and humans. Both read from the same `docs/` hierarchy.
* **Zero Emojis:** Always maintain clean, professional styling with Lucide SVG icons in UI and uppercase bracketed status tags (`[SUCCESS]`, `[ERROR]`, `[WARNING]`) in logs.
* **Instant In-Browser Viewer:** `docs/index.html` must always be runnable offline with 0 servers via double-click in any browser.
