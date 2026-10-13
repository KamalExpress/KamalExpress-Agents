---
name: architecture-review
description: >-
  Use this skill when the developer says "architecture review", "review architecture", or "check drift".
  Evaluates recent changes against discovered architectural specifications, catching boundary violations and technical debt.
---

# Architecture Review Lifecycle Orchestrator

The `architecture-review` skill ensures that implementation work adheres to the project's architectural baseline.

## Operational Workflow

1. **Locate Architectural Specifications**:
   - Inspect `.alamia/project.json` for `memory.architecture_dir`.
   - If configured, read the baseline architectural documents, specifications, or data schemas.
   - If not configured, inspect root architecture documentation (e.g. `ARCHITECTURE.md`, `README.md`).

2. **Inspect Changed Code**:
   - Inspect `git diff` against the base branch (or recent commits) to identify new modules, dependencies, and boundary crossings.

3. **Evaluate Two-Axis Review & Drift**:
   - Invoke the atomic skill `/code-review` to analyze Standards vs. Spec.
   - Check for:
     - **Architecture Drift:** Are new components bypassing established layers (e.g. UI talking directly to raw DB instead of service/API)?
     - **Dependency Violations:** Are unnecessary or unvetted external libraries being introduced?
     - **Pattern Consistency:** Does new code match the patterns established in the project?

4. **Produce Architecture Health Report**:
   - Summarize architectural alignment, identify any boundary leaks or smells, and propose refactoring steps if needed.
