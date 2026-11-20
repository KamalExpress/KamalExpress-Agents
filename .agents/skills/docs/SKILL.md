---
name: docs
description: Initialize, audit, physically migrate, synchronize and maintain one canonical project knowledge base shared by human developers and AI agents.
---

# Universal Documentation & Knowledgebase Lifecycle Skill

**Canonical source:** docs/  
**Core principle:** One physical set of Markdown knowledge files; multiple readers and interfaces.

## Invariants

1. docs/ is the single canonical project knowledge tree. Do not maintain a parallel .ai/ knowledge base.
2. AI agents and humans consume the exact same Markdown files. The browser viewer is only another interface.
3. docs/docs_data.js and other generated indexes are disposable projections generated from canonical Markdown, never manually maintained knowledge.
4. Preserve meaning, provenance, decisions, links and useful history during migration.
5. Never silently overwrite, delete or perform a lossy merge.
6. Ground documentation in evidence; mark unknowns as TODO/unknown.
7. All workflows must be idempotent.

## Commands

- docs:init — safely initialize missing canonical structure and bootstrap instructions.
- docs:audit — inventory all knowledge locations, duplicates, conflicts, gaps and stale material.
- docs:migrate — plan and execute physical migration into docs/.
- docs:sync — regenerate the browser index from canonical Markdown.
- docs:update — reconcile documentation after engineering changes.
- docs:handoff — record session changes, evidence, blockers and next actions.

## Canonical structure

Use this as a flexible taxonomy; preserve meaningful project-specific groupings.

    docs/
      README.md
      index.html                 # optional offline viewer
      docs_data.js               # generated only
      assets/
      permanent/
        product/
        architecture/
        adr/
        glossary/
        standards/
      transient/
        sprint/
        backlog/
        handoffs/
      history/
      lessons/
      indexes/
      research/

Projects may add domain folders such as api, integrations, operations, security or ux. Classify documents by actual purpose and expected lifespan; do not force-fit useful existing taxonomies.

## docs:init

1. Inspect Git status and repository root before writing.
2. Inventory docs/, legacy .ai/, .agents/, root AGENTS.md/CLAUDE.md, README and project-specific docs.
3. Extend an existing docs/ structure; never scaffold a competing tree.
4. Create only missing files/directories; preserve existing content.
5. Keep .agents/AGENTS.md concise. It points to docs/README.md and contains bootstrap/operating rules, not duplicated project knowledge.
6. Create a verified master index and bootstrap order.
7. Populate templates only from repository evidence; mark unknowns as TODO.
8. Preserve existing viewer/sync tooling unless there is a verified reason to change it.

Suggested bootstrap order:
1. docs/README.md
2. docs/transient/sprint/00-current-state.md, if present
3. Product and system architecture docs
4. Relevant ADRs, standards, glossary and security constraints
5. docs/indexes/repository.md
6. Latest relevant handoff
7. Relevant source code and tests

## docs:audit

Inventory docs/**/*.md, .ai/**/*.md, .agents/**, root agent instructions, root and module READMEs, architecture/product/API/operations docs, diagrams/assets, viewer files, sync scripts and relevant Git history/status.

For each knowledge file record source path, title/topic, lifecycle classification, authority/status, provenance/date when available, references, and whether canonical, duplicate, conflicting, stale, generated or unknown.

Identify duplicates and near-duplicates by meaning/content, conflicting decisions, undocumented high-risk behavior, stale current-state/handoffs, broken links, orphan assets, generated artifacts and substantive knowledge embedded in agent instructions.

Produce a read-only audit report with counts, findings, proposed destinations and unresolved decisions. Audit does not move or rewrite files.

## docs:migrate — physical migration, not mirroring

The goal is to physically move/reconcile actual knowledge files into docs/. Merely configuring the viewer to read .ai/, copying content into a browser bundle, or retaining .ai/ as canonical does NOT satisfy migration.

### Phase A: plan (read-only)

1. Run docs:audit.
2. Build a source-to-destination manifest for every candidate: source path, proposed docs/ destination, content hash, classification/rationale, links needing updates, action and risk.
3. Compare content and meaning, not just filenames. Preserve unique details, dates, decisions, evidence and provenance.
4. Flag conflicting claims/decisions. Do not choose a winner without evidence or owner approval.
5. Keep .agents/AGENTS.md as concise bootstrap/policy. Move substantive knowledge from agent instruction files into canonical docs.
6. Do not move runtime configuration, executable tooling, credentials, secrets, vendor files or unrelated source files into docs.
7. Present the plan: files to move/merge/retain, references to update, conflicts and risks.

### Phase B: approval gate

Audit and planning are read-only. A general request to migrate permits safe moves that preserve content, but does not authorize destructive consolidation. Obtain explicit approval before overwriting, deleting unique/conflicting material, lossy merging or irreversible cleanup. If uncertain, retain the source and report the blocker. Do not interpret migration intent as permission to discard information.

### Phase C: apply

1. Create destination directories.
2. Physically move files, using Git-aware renames where possible.
3. If source and destination are identical, retain one canonical copy and record the duplicate. Remove the duplicate only within approved scope.
4. If contents differ, merge only when meaning is compatible and every unique detail is preserved. Otherwise retain both temporarily and flag the conflict.
5. Update relative Markdown links, image paths, embedded references, bootstrap instructions, scripts, tests and viewer configuration.
6. Preserve dates, authorship/provenance, status, decision history and citations.
7. Record old path -> new path, hashes, actions, conflicts and validation in a migration report under docs/history/ or another agreed location.
8. Do not leave .ai/ as a second knowledge base after completed migration. Preserve unrelated .ai/ tooling/configuration and account for every source file.
9. Do not create symlink-based dual homes. There must be one physical canonical copy.

### Phase D: validate

- Every source knowledge file is accounted for in the manifest.
- No unique source content was lost; compare hashes/diffs and review merged docs.
- All internal links and image references resolve.
- Agent bootstrap points to canonical docs/.
- Viewer indexes canonical files and no parallel knowledge silo.
- Generated data is regenerated, not manually merged.
- Review Git status/diff; run relevant tests and sync.
- Re-run docs:audit and compare before/after inventories.
- Report moves, merges, retained conflicts, approvals still needed, checks run and rollback point.

### Rollback

Keep migration reviewable in Git. Record the pre-migration commit. Before destructive cleanup, preserve source content in Git history. If validation fails, revert the migration change or restore from the manifest; never reconstruct from memory.

## docs:sync

Run the repository sync script or core.doc_engine to scan canonical Markdown under docs/ and regenerate docs/docs_data.js.

- Viewer renders the same Markdown agents read.
- Generated JS/JSON is not an editable knowledge store.
- Exclude build/dependency directories, generated output and legacy hidden knowledge roots.
- Preserve stable IDs across moves using explicit frontmatter IDs or an alias map.
- Verify output count, paths, duplicate IDs and browser loading.

    python scripts/sync_docs.py
    python -m core.doc_engine

## docs:update

After changes to architecture, data, APIs, workflows, security, integrations, deployment or operations:
1. Identify affected canonical docs.
2. Update intent, invariants, constraints, trade-offs and failure modes from verified evidence.
3. Amend/supersede ADRs instead of silently rewriting accepted decisions.
4. Update repository/dependency indexes and glossary where needed.
5. Keep current-state concise and link to detailed docs.
6. Run relevant tests and docs sync; report checks actually run.

## docs:handoff

Record objective/outcome, changed modules, decisions, tests actually run and results, blockers/risks, uncommitted work, branch/commit and exact next actions. Store dated handoffs under docs/transient/handoffs/ and update current-state only with still-active information.

## Browser viewer

docs/index.html is an optional presentation layer. It reads generated docs/docs_data.js, which is reproducible from canonical Markdown. It must not embed a separately maintained copy of project knowledge. Offline behavior is required only when the project explicitly needs it.

## Quality gates

- [ ] docs/ is the only canonical knowledge tree.
- [ ] AI and human interfaces consume the same physical Markdown files.
- [ ] Legacy knowledge inventory is complete and every file accounted for.
- [ ] Permanent, transient, history, research and non-knowledge files are classified.
- [ ] No unapproved overwrite, lossy merge or deletion.
- [ ] Links, assets and bootstrap references validate.
- [ ] Viewer index is generated from canonical docs.
- [ ] Migration is idempotent and rollback is available.
- [ ] Claims and decisions are evidence-based.

## Boundaries

Never maintain parallel .ai/ and docs/ knowledge bases. Never overwrite by default. Never treat empty templates as completed docs. Never store credentials in project knowledge. Do not silently resolve conflicting decisions. Do not copy large source files into docs.
