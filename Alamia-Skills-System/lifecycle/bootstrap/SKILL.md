---
name: bootstrap
description: >-
  Use this skill when the developer says "bootstrap", "bootstrap this project", or "start session".
  Discovers project stack, testing, memory/handoff conventions, and reconciles the lifecycle system idempotently.
---

# Bootstrap Lifecycle Orchestrator

The `bootstrap` skill establishes or reconciles the project environment. It is **completely project-agnostic** and operates via automatic discovery and idempotent reconciliation.

## Operational Workflow

When triggered:

1. **Execute Discovery & Reconciliation**:
   Run the Alamia reconciler to detect stack, tests, git state, and session memory:
   ```bash
   python Alamia-Skills-System/core/reconciler.py
   ```
   *(Or read `.alamia/project.json` if already generated)*.

2. **Evaluate Project State & Case**:
   - **Case A (Fresh Project)**: No code exists; confirm tech stack setup.
   - **Case B (Existing Unconfigured)**: Discovers existing code, tests, docs; generates `.alamia/project.json` without modifying code.
   - **Case C (Partially Configured)**: Scaffolds only missing lifecycle capabilities.
   - **Case D (Configured & Up-to-Date)**: Validates health, captures active branch, latest handoff, and test status.
   - **Case E (Outdated)**: Re-syncs configuration non-destructively.

3. **Capture Immediate Work Context**:
   - Check the discovered `latest_handoff` file (if one exists).
   - Identify active pending objectives or immediate tasks.
   - Check Git status (current branch, uncommitted diffs).

4. **Return Bootstrap Report**:
   Present the structured Bootstrap Report to the developer:
   - Discovered stack & test runner
   - Discovered session memory & latest handoff title
   - Active pending objectives
   - Recommended next lifecycle step (`implement`, `qa`, etc.)
