r"""
Universal AI Knowledgebase & Documentation Scaffolder.
Adapted from F:\scaff-ai-kb\scaffold_ai_knowledge.ps1 into a cross-platform,
project-agnostic Python module.

Initializes the canonical documentation and AI memory architecture:
  docs/
    ├── README.md                      # Master Index & AI Bootstrap Read Order
    ├── index.html                     # Standalone In-Browser Docs Viewer (0-server)
    ├── docs_data.js                   # Ingested Markdown Data Registry
    ├── assets/
    │   └── marked.min.js              # Offline Client-Side Markdown Parser
    ├── permanent/                     # Lives for years
    │   ├── architecture/              # System design, boundaries, invariants
    │   ├── adr/                       # Architecture Decision Records
    │   ├── glossary/                  # Terminology single source of truth
    │   └── standards/                 # Coding, testing, and PII masking rules
    ├── transient/                     # Lives for days / weeks
    │   ├── sprint/                    # 00-current-state.md (active focus)
    │   ├── handoffs/                  # Chronological session handoffs
    │   └── backlog/                   # Pending tasks & roadmap
    ├── history/                       # Architecture timeline & retrospectives
    ├── lessons/                       # Operational playbooks & incident post-mortems
    └── indexes/                       # repository.md (concept-to-code map)
"""

import os
import shutil
from pathlib import Path
from typing import Dict, Any, Optional

MASTER_INDEX_TEMPLATE = """# AI Knowledge Base Master Index

Welcome to the canonical documentation and AI knowledge base for this project.
This system is designed to preserve architectural intent, minimize context loss across AI agent sessions, and provide human developers with an immediate reference hub.

## The AI Bootstrap Read Order
If you are an AI Agent entering a fresh conversation, you **must** read the following documents in order before inspecting the source code:

1. `README.md` (You are here: Master Index)
2. `permanent/architecture/01-system-architecture.md` (High-level system design & invariants)
3. `indexes/repository.md` (Concept-to-code mapping)
4. `permanent/standards/01-coding-standards.md` (Conventions, testing, and safety rules)
5. `transient/sprint/00-current-state.md` (Current sprint objectives & active blockers)

---

## Directory Structure

### 1. Permanent Knowledge (Lives for Years)
* **`permanent/architecture/`**: High-level design intent, tradeoffs, system boundaries, and components.
* **`permanent/adr/`**: Architecture Decision Records. Contains the record of all accepted, rejected, and deprecated architectural choices.
* **`permanent/glossary/`**: The single source of truth for project terminology, codes, and domain concepts.
* **`permanent/standards/`**: Coding conventions, testing standards, and compliance rules.

### 2. Transient Knowledge (Lives for Days / Weeks)
* **`transient/sprint/`**: The current sprint's focus, active milestones, and immediate blockers (`00-current-state.md`).
* **`transient/handoffs/`**: Session handoffs summarizing the immediate delta between chats and developer handovers.
* **`transient/backlog/`**: Pending tasks and feature roadmaps.

### 3. History & Meta
* **`history/`**: Architecture timeline. Records additions, removals, and deprecations sprint-by-sprint.
* **`indexes/`**: 
  * `repository.md`: Maps high-level architectural concepts to source code directories and ADRs.
  * `dependency-map.md`: Architecture diagrams of system components.
* **`lessons/`**: Operational knowledge, debugging outcomes, incident playbooks, and learned failure modes.
"""

AGENTS_RULES_TEMPLATE = """# AI Workspace Instructions (AGENTS.md)

This file contains the foundational rules for all AI agents working in this repository. It is automatically injected into the context of every new conversation.

## 1. The AI Bootstrap Sequence
If you are entering a fresh conversation and do not have full context of this project, you **MUST NOT** immediately inspect the source code or reverse engineer the repository. 

Instead, strictly follow this read order:
1. Read `docs/README.md` (Master Index)
2. Read `docs/transient/sprint/00-current-state.md`
3. Read `docs/permanent/architecture/01-system-architecture.md`
4. Read `docs/indexes/repository.md` to map concepts to code.
5. Only then may you inspect the source code.

## 2. Documentation Ownership (Knowledge Manager)
- The documentation in `docs/` is the single source of truth for both humans and AI agents.
- If you modify architecture, database models, workflows, or APIs, you must either update the docs directly or record a session handoff in `docs/transient/handoffs/`.
- Never summarize source code in documentation. Document *intent, invariants, tradeoffs, and failure modes*.
- Always keep documentation 100% synchronized by running:
  `python scripts/sync_docs.py`
"""

ARCHITECTURE_STARTER_TEMPLATE = """# System Architecture & Technical Specifications

> **Status:** Baseline Architecture  
> **Last Updated:** System Initialization

---

## 1. System Overview & Core Intent
Document the primary purpose, runtime boundaries, and core user workflows of this application.

```mermaid
flowchart TD
    Client([User / Client Interface]) <--> AppGateway[Application Gateway / API]
    AppGateway <--> CoreServices[Core Business Services]
    CoreServices <--> Persistence[(Data Persistence Layer)]
```

---

## 2. Core Architectural Invariants
List the non-negotiable system rules and constraints that must NEVER be violated.
1. **Invariant 1:** [Define rule]
2. **Invariant 2:** [Define rule]

---

## 3. Technology Stack & Runtime Boundaries
* **Language & Framework:** [e.g. Python / FastAPI / PHP / TypeScript]
* **Database & Storage:** [e.g. SQLite WAL / PostgreSQL / Redis]
* **Testing Framework:** [e.g. pytest / pest / vitest]
"""

SPRINT_STATE_TEMPLATE = """# Current Sprint State & Active Objectives

> **Current Milestone:** Milestone 1  
> **Last Updated:** Initial Scaffolding

---

## 1. Active Sprint Objectives
- [ ] Task 1: Complete initial project scaffolding
- [ ] Task 2: Implement baseline specifications
- [ ] Task 3: Establish automated test suite

---

## 2. Current Blockers & Critical Notes
* None currently identified.

---

## 3. Immediate Next Steps for AI Agent / Developer
1. Verify system dependencies and test harness.
2. Review architecture baseline in `docs/permanent/architecture/01-system-architecture.md`.
"""

REPOSITORY_INDEX_TEMPLATE = """# Repository Concept-to-Code Index

Maps high-level functional concepts, business logic, and architectural specifications to their concrete source code locations.

| Architectural Concept | Primary Spec / ADR | Source Code Implementation | Test Suite |
| :--- | :--- | :--- | :--- |
| **System Architecture** | `docs/permanent/architecture/01-system-architecture.md` | Core application directory | `tests/` |
| **Data Persistence** | `docs/permanent/architecture/` | Database layer | `tests/` |
"""

CODING_STANDARDS_TEMPLATE = """# Coding Standards & Engineering Invariants

---

## 1. Code Quality & Style
* Clean, readable, self-documenting code over clever hacks.
* Strict type annotations where supported by the language.
* Zero compiler/linter warnings in CI/CD pipeline.

---

## 2. Professional Standards (Zero Emojis)
* **Strict Rule:** Never use emoji characters anywhere in code, logs, tooltips, or alerts.
* Use clean SVG icons (Lucide standard) in user interfaces and standard uppercase bracketed status tags (`[SUCCESS]`, `[ERROR]`, `[WARNING]`, `[INFO]`) in log output.

---

## 3. Testing & Verification Protocol
* All new features and bug fixes must have accompanying automated unit/integration tests.
* Full test suite must pass 100% prior to pushing to main branches.
"""

SYNC_SCRIPT_TEMPLATE = """#!/usr/bin/env python3
\"\"\"
Documentation Synchronization Hook.
Re-indexes all markdown files in `docs/` and repository specifications
into `docs/docs_data.js` for instant in-browser runtime rendering.
\"\"\"
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

try:
    # Try importing from Alamia-Skills-System if available
    from core.doc_engine import DocEngine
    engine = DocEngine(project_root=REPO_ROOT)
    engine.sync()
except Exception:
    # Fallback to local embedded sync
    import json
    docs_dir = REPO_ROOT / "docs"
    docs_data_file = docs_dir / "docs_data.js"
    docs_payload = []
    
    for root, _, files in os.walk(docs_dir):
        for f in sorted(files):
            if f.endswith(".md"):
                full_path = Path(root) / f
                rel_path = str(full_path.relative_to(REPO_ROOT)).replace("\\\\", "/")
                try:
                    content = full_path.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    content = ""
                
                # Extract first header
                title = f
                for line in content.splitlines():
                    if line.startswith("# "):
                        title = line[2:].strip()
                        break
                        
                cat = "General"
                rel_parts = full_path.relative_to(docs_dir).parts
                if len(rel_parts) > 1:
                    cat = rel_parts[0].capitalize()
                    
                docs_payload.append({
                    "id": full_path.stem.lower().replace(" ", "-"),
                    "title": title,
                    "category": cat,
                    "path": rel_path,
                    "description": title,
                    "markdown": content,
                    "line_count": len(content.splitlines()),
                })
                
    js_code = (
        "// Auto-generated by scripts/sync_docs.py - Do not edit directly\\n"
        "window.DOCS_DATA = " + json.dumps(docs_payload, indent=2, ensure_ascii=False) + ";\\n"
    )
    with open(docs_data_file, "w", encoding="utf-8") as out:
        out.write(js_code)
    print(f"[SUCCESS] Synchronized {len(docs_payload)} documentation files into {docs_data_file}")
"""


class DocScaffolder:
    """Universal scaffolder for project documentation and AI knowledgebase."""

    def __init__(self, project_root: Path):
        self.root = Path(project_root).resolve()

    def scaffold(self, overwrite: bool = False) -> Dict[str, Any]:
        """
        Creates canonical directory structure and populates initial starter content.
        """
        created_dirs = []
        created_files = []

        # 1. Directories
        dirs = [
            self.root / "docs" / "permanent" / "architecture",
            self.root / "docs" / "permanent" / "adr",
            self.root / "docs" / "permanent" / "glossary",
            self.root / "docs" / "permanent" / "standards",
            self.root / "docs" / "transient" / "sprint",
            self.root / "docs" / "transient" / "handoffs",
            self.root / "docs" / "transient" / "backlog",
            self.root / "docs" / "history",
            self.root / "docs" / "lessons",
            self.root / "docs" / "indexes",
            self.root / "docs" / "assets",
            self.root / ".agents",
            self.root / "scripts",
        ]

        for d in dirs:
            if not d.exists():
                d.mkdir(parents=True, exist_ok=True)
                created_dirs.append(str(d.relative_to(self.root)).replace("\\", "/"))

        # 2. Starter Content Files
        starter_files = [
            (self.root / "docs" / "README.md", MASTER_INDEX_TEMPLATE),
            (self.root / ".agents" / "AGENTS.md", AGENTS_RULES_TEMPLATE),
            (self.root / "docs" / "permanent" / "architecture" / "01-system-architecture.md", ARCHITECTURE_STARTER_TEMPLATE),
            (self.root / "docs" / "transient" / "sprint" / "00-current-state.md", SPRINT_STATE_TEMPLATE),
            (self.root / "docs" / "indexes" / "repository.md", REPOSITORY_INDEX_TEMPLATE),
            (self.root / "docs" / "permanent" / "standards" / "01-coding-standards.md", CODING_STANDARDS_TEMPLATE),
            (self.root / "scripts" / "sync_docs.py", SYNC_SCRIPT_TEMPLATE),
        ]

        for path, template in starter_files:
            if not path.exists() or overwrite:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(template, encoding="utf-8")
                created_files.append(str(path.relative_to(self.root)).replace("\\", "/"))

        return {
            "success": True,
            "created_dirs": created_dirs,
            "created_files": created_files,
            "docs_root": str((self.root / "docs").relative_to(self.root)).replace("\\", "/"),
        }


if __name__ == "__main__":
    import sys
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    scaffolder = DocScaffolder(target)
    result = scaffolder.scaffold()
    print(f"[SUCCESS] Scaffolding complete for: {target}")
    print(f"Created {len(result['created_dirs'])} directories and {len(result['created_files'])} initial files.")
