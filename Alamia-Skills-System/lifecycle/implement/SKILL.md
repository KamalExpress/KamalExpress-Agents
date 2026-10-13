---
name: implement
description: >-
  Use this skill when the developer says "implement", "implement X", or "start coding".
  Orchestrates active feature development, pulls context from project memory/architecture, and routes to atomic skills.
---

# Implement Lifecycle Orchestrator

The `implement` skill manages the active development phase. It is **project-agnostic** and adapts to the discovered project profile.

## Operational Workflow

1. **Resolve Work Context**:
   - Inspect `.alamia/project.json` for project stack, architecture dir, and memory dir.
   - If the developer specified a task (e.g. "implement X"), use that as the primary objective.
   - If no task was specified, inspect the latest session handoff in the discovered handoff directory and propose working on the highest-priority pending objective.

2. **Inspect Architectural Baseline**:
   - Check the project's discovered architecture directory (e.g. `docs/architecture/` or `.ai/permanent/architecture/`).
   - Read any specifications or conventions relevant to the target module or feature.
   - If a spec is needed before coding, invoke the atomic skill `/to-spec`.

3. **Orchestrate Atomic Implementation**:
   - For testable logic, invoke the atomic skill `/tdd` (test-driven development).
   - Write or update code following the language conventions detected in `.alamia/project.json`.
   - Update or add tests in the project's detected test directory.

4. **Verify Immediate Changes**:
   - Run the project's detected test command (from `.alamia/project.json`) against the modified unit/file.

5. **Handoff to Validation**:
   - Inform the developer of the completed changes.
   - Suggest the next lifecycle phase: `qa` for comprehensive test suite validation.
