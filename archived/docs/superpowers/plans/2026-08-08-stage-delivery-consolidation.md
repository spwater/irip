# IRIP Stage Delivery Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce one current, evidence-based IRIP stage delivery document and move superseded project reports into a traceable archive while retaining operational documentation.

**Architecture:** Treat code, migrations, tests, configuration, and current Git history as primary evidence. Keep a small active documentation set, use `docs/STAGE_DELIVERY.md` as the context entry point, and mirror legacy paths beneath `archived/` so every historical file remains traceable.

**Tech Stack:** Markdown, Git, ripgrep, shell-based link and inventory checks, Python 3.12, Ruff, mypy, pytest, pnpm/Vitest.

## Global Constraints

- Do not change application code, database migrations, or runtime configuration.
- Preserve `README.md` and current operational documents in their existing paths.
- Move rather than delete historical documents.
- Do not report an inherited test count or quality claim as current unless it is verified in this run.
- Preserve unrelated user changes.

---

### Task 1: Establish the current evidence baseline

**Files:**
- Read: `README.md`
- Read: `pyproject.toml`
- Read: `Makefile`
- Read: `.github/workflows/*`
- Read: `apps/**`, `packages/**`, `migrations/versions/**`, `tests/**`
- Read: current and historical Markdown/Mermaid documents

**Interfaces:**
- Consumes: current repository state and Git history.
- Produces: verified facts, module inventory, test inventory, recent fixes, and a retain/archive manifest used by Tasks 2 and 3.

- [ ] **Step 1: Inventory code, migrations, tests, configuration, active documents, and historical documents**

Run `rg --files` with scoped paths and summarize counts by subsystem and test category.

- [ ] **Step 2: Inspect recent development and debugging history**

Run `git log --stat` and `git show` for recent refactors, security fixes, CI fixes, and feature commits; verify key claims against current files.

- [ ] **Step 3: Run proportionate quality checks**

Run Ruff, mypy, backend tests, frontend tests, and frontend build when local dependencies permit. Record exact commands, timestamps, results, skips, and environmental blockers.

### Task 2: Write the current stage delivery document

**Files:**
- Create: `docs/STAGE_DELIVERY.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: verified baseline and retain/archive manifest from Task 1.
- Produces: the sole current context entry point and a README link to it.

- [ ] **Step 1: Draft the delivery document from verified facts**

Include baseline, scope, product status, architecture, implemented capabilities, data model and migrations, runtime/deployment, verification results, debugging lessons, limitations, risks, and prioritized next work.

- [ ] **Step 2: Add navigation and source discipline**

Link the retained operational documents and the archive index. Label historical-only conclusions and unverified checks explicitly.

- [ ] **Step 3: Update the README document index and stale status text**

Place `docs/STAGE_DELIVERY.md` first, keep links to retained operational references, and direct historical research to `archived/README.md`.

### Task 3: Archive superseded documents

**Files:**
- Create: `archived/README.md`
- Move: superseded root reports to `archived/`
- Move: superseded `docs/` reports to `archived/docs/`
- Move: `deliverables/` to `archived/deliverables/`
- Retain: current operational documentation listed in the approved design

**Interfaces:**
- Consumes: retain/archive manifest from Task 1.
- Produces: a small active documentation surface and traceable historical tree.

- [ ] **Step 1: Create the archive index**

Document archive date, authority rules, preserved path convention, and category inventory.

- [ ] **Step 2: Move historical files with original paths mirrored**

Use explicit source and destination paths. Do not remove any active operational guide or the approved design/plan records.

- [ ] **Step 3: Review Git rename detection**

Run `git status --short` and `git diff --summary`; confirm each historical source has a destination and no content was lost.

### Task 4: Validate the handoff

**Files:**
- Verify: `README.md`
- Verify: `docs/STAGE_DELIVERY.md`
- Verify: `archived/README.md`
- Verify: all retained Markdown files

**Interfaces:**
- Consumes: final documentation and archive tree.
- Produces: evidence that the delivery is internally consistent and ready as the next-work baseline.

- [ ] **Step 1: Check Markdown links and stale old paths**

Parse relative Markdown links in active documents, confirm their targets exist, and search for references to moved paths outside `archived/`.

- [ ] **Step 2: Check document completeness and placeholders**

Search the new delivery document for missing required sections, `TBD`, invented test claims, and conflicting versions.

- [ ] **Step 3: Check repository diff and rerun lightweight validation**

Run `git diff --check`, confirm no application code changed, and rerun any quick quality command whose result is quoted in the delivery document.

- [ ] **Step 4: Review against the approved design**

Confirm every verification standard in `docs/superpowers/specs/2026-08-08-stage-delivery-consolidation-design.md` is satisfied before reporting completion.
