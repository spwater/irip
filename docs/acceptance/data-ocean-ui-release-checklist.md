# Data Ocean UI Release Checklist

> Line-by-line mapping from every design-spec acceptance criterion to evidence.
> No criterion is marked complete solely because a developer says it is complete —
> each requires an automated test, reviewed visual baseline, manual audit section,
> or explicit failure blocking release.
>
> The design spec source of truth is `docs/superpowers/specs/2026-07-28-data-ocean-ui-upgrade-design.md`
> Section 17 (acceptance criteria). Where the spec file is not available, criteria
> are derived from the roadmap Gates A–D and the global constraints.

## Release Metadata

| Field | Value |
|---|---|
| **Release commit SHA** | _(fill with `git log -1 --format=%H`)_ |
| **Verification date** | _(fill with `date -u +%Y-%m-%dT%H:%M:%SZ`)_ |
| **Branch** | `newui_workbuddy` |
| **Verifier** | _(name / role)_ |

---

## Gate A — Foundation Accepted

### A1. Theme token tests pass

| Criterion | Evidence type | Status | Evidence |
|---|---|---|---|
| `oceanTokens` object is immutable and exported from `src/theme/tokens.ts` | Automated test | ☐ Pass ☐ Fail | `pnpm --dir apps/web test -- tokens` |
| All token values match the Polar Mist design spec exactly | Automated test | ☐ Pass ☐ Fail | `pnpm --dir apps/web test -- themeConfig` |
| `statusTone` mapping covers all six semantic states | Automated test | ☐ Pass ☐ Fail | `pnpm --dir apps/web test -- tokens` |
| ECharts theme helper merges without overriding business series/data | Automated test | ☐ Pass ☐ Fail | `pnpm --dir apps/web test -- chartTheme` |

### A2. UI primitive tests pass

| Criterion | Evidence type | Status | Evidence |
|---|---|---|---|
| `PageIntro` renders index, title, and description | Automated test | ☐ Pass ☐ Fail | `pnpm --dir apps/web test` |
| `OceanPanel` applies surface levels (default, strong, structural) | Automated test | ☐ Pass ☐ Fail | `pnpm --dir apps/web test` |
| `StatusMark` renders text/shape for every tone (not color-only) | Automated test | ☐ Pass ☐ Fail | `pnpm --dir apps/web test` |
| `FeedbackState` covers loading, error, empty, partial, success | Automated test | ☐ Pass ☐ Fail | `pnpm --dir apps/web test` |
| `FocusDrawer` and `FocusModal` render with correct ARIA roles | Automated test | ☐ Pass ☐ Fail | `pnpm --dir apps/web test` |
| `DataHero` and `MetricStrip` display real values with units | Automated test | ☐ Pass ☐ Fail | `pnpm --dir apps/web test` |

### A3. Existing login and JobDrawer tests pass

| Criterion | Evidence type | Status | Evidence |
|---|---|---|---|
| Login success scenario passes (reuses `loginAsAdmin` helper) | E2E test | ☐ Pass ☐ Fail | `pnpm --dir apps/web e2e -- v0-login.spec.ts` |
| Login failure scenario shows error message | E2E test | ☐ Pass ☐ Fail | `pnpm --dir apps/web e2e -- v0-login.spec.ts` |
| JobDrawer opens from header button | E2E test | ☐ Pass ☐ Fail | `pnpm --dir apps/web e2e -- data-ocean-critical-flows.spec.ts -g "JobDrawer opens from header"` |
| JobDrawer shows job progress and status | E2E test | ☐ Pass ☐ Fail | Same as above |

### A4. Lint, unit tests, and build pass

| Criterion | Evidence type | Status | Evidence |
|---|---|---|---|
| TypeScript type check passes | Command | ☐ Pass ☐ Fail | `pnpm --dir apps/web lint` (exit 0) |
| Vitest unit tests pass | Command | ☐ Pass ☐ Fail | `pnpm --dir apps/web test` (exit 0) |
| Vite production build succeeds | Command | ☐ Pass ☐ Fail | `pnpm --dir apps/web build` (exit 0) |

### A5. Login and AppShell screenshots approved at 1440×900

| Criterion | Evidence type | Status | Evidence |
|---|---|---|---|
| Login page visual baseline approved | Visual baseline | ☐ Approved ☐ Pending | `tests/e2e/data-ocean-visual.spec.ts-snapshots/login-visual-1440-*.png` |
| AppShell layout visual baseline approved | Visual baseline | ☐ Approved ☐ Pending | `tests/e2e/data-ocean-visual.spec.ts-snapshots/workbench-visual-1440-*.png` |

---

## Gate B — Core Modules Accepted

### B1. Shared page archetypes used in all migrated pages

| Criterion | Evidence type | Status | Evidence |
|---|---|---|---|
| Workbench uses PageIntro + OceanPanel + DataHero + MetricStrip | Code inspection | ☐ Pass ☐ Fail | `apps/web/src/pages/WorkbenchPage.tsx` |
| Lab construction uses PageIntro + OceanPanel + ConstructionTrack | Code inspection | ☐ Pass ☐ Fail | `apps/web/src/standards/StandardsPage.tsx` |
| LabOps uses PageIntro + OceanPanel + Tabs | Code inspection | ☐ Pass ☐ Fail | `apps/web/src/pages/LabOpsPage.tsx` |
| Platform uses PageIntro + OceanPanel + Tabs | Code inspection | ☐ Pass ☐ Fail | `apps/web/src/pages/PlatformPage.tsx` |
| Models list uses PageIntro + ActionBar + DataTableShell | Code inspection | ☐ Pass ☐ Fail | `apps/web/src/models/ModelsPage.tsx` |
| Governance uses PageIntro + OceanPanel + Tabs | Code inspection | ☐ Pass ☐ Fail | `apps/web/src/governance/GovernanceConsole.tsx` |

### B2. Existing behavior passes after migration

| Criterion | Evidence type | Status | Evidence |
|---|---|---|---|
| Cross-tab prefill (construction: dept → equipment → object) | E2E test | ☐ Pass ☐ Fail | `pnpm --dir apps/web e2e -- data-ocean-critical-flows.spec.ts -g "cross-tab preset"` |
| LabOps ?tab= search-param switching | E2E test | ☐ Pass ☐ Fail | `-g "lab-ops tab switching via search param"` |
| Upload/flow behavior preserved | E2E test | ☐ Pass ☐ Fail | `pnpm --dir apps/web e2e -- particle-size.spec.ts` |
| Assistant streaming behavior preserved | Manual audit | ☐ Pass ☐ Fail | `docs/acceptance/data-ocean-ui-audit.md` §6 |
| Parameter approval flow preserved | E2E test | ☐ Pass ☐ Fail | `pnpm --dir apps/web e2e -- parameter-provenance.spec.ts` |
| Model detail and prediction reachable | E2E test | ☐ Pass ☐ Fail | `-g "model list shows entries"` |

### B3. No near-black backgrounds or horizontal overflow

| Criterion | Evidence type | Status | Evidence |
|---|---|---|---|
| No off-theme color literals in migrated TSX | Theme gate | ☐ Pass ☐ Fail | `pnpm --dir apps/web check:theme` (exit 0) |
| No page-level horizontal overflow at 1280px | E2E test | ☐ Pass ☐ Fail | `pnpm --dir apps/web e2e -- data-ocean-accessibility.spec.ts -g "no page-level horizontal scrollbar"` |

---

## Gate C — Governance and Detail Accepted

### C1. Shared status and error language

| Criterion | Evidence type | Status | Evidence |
|---|---|---|---|
| Governance, jobs, facts, models use StatusMark for status display | Code inspection | ☐ Pass ☐ Fail | `grep -r "StatusMark" apps/web/src/` |
| All status has text or shape in addition to color | Unit test | ☐ Pass ☐ Fail | `pnpm --dir apps/web test -- StatusMark` |
| 403, 500, 503 states use FeedbackState | Code inspection | ☐ Pass ☐ Fail | `grep -r "FeedbackState" apps/web/src/` |
| Empty state uses FeedbackState kind="empty" | Code inspection | ☐ Pass ☐ Fail | `grep -r "kind=\"empty\"" apps/web/src/` |
| Partial-failure uses FeedbackState kind="partial" | Code inspection | ☐ Pass ☐ Fail | `grep -r "kind=\"partial\"" apps/web/src/` |

### C2. Error and confirmation states verified

| Criterion | Evidence type | Status | Evidence |
|---|---|---|---|
| Retry action available on error states | Code inspection | ☐ Pass ☐ Fail | `grep -r "onRetry" apps/web/src/` |
| Destructive actions require Popconfirm confirmation | E2E test | ☐ Pass ☐ Fail | `pnpm --dir apps/web e2e -- data-ocean-critical-flows.spec.ts -g "destructive"` |

### C3. Technical content readability

| Criterion | Evidence type | Status | Evidence |
|---|---|---|---|
| UUID, version, time, unit rendered with `ocean-tech` class | Code inspection | ☐ Pass ☐ Fail | `grep -r "ocean-tech" apps/web/src/` |
| JSON content viewable in table and raw modes | E2E test | ☐ Pass ☐ Fail | Manual: FactDetail viewMode toggle |
| Log and payload content readable and copyable | Manual audit | ☐ Pass ☐ Fail | `docs/acceptance/data-ocean-ui-audit.md` |

---

## Gate D — Release Accepted

### D1. Functional E2E passes

| Criterion | Evidence type | Status | Evidence |
|---|---|---|---|
| Five primary navigation destinations pass | E2E test | ☐ Pass ☐ Fail | `pnpm --dir apps/web e2e -- data-ocean-navigation.spec.ts` |
| Critical UI flows pass (7 scenarios) | E2E test | ☐ Pass ☐ Fail | `pnpm --dir apps/web e2e -- data-ocean-critical-flows.spec.ts` |
| No unhandled page errors on primary destinations | E2E test | ☐ Pass ☐ Fail | `-g "without unhandled page errors"` |

### D2. Three-viewport visual baselines approved

| Criterion | Evidence type | Status | Evidence |
|---|---|---|---|
| 1280×800 baselines generated and human-reviewed | Visual baseline | ☐ Approved ☐ Pending | `tests/e2e/data-ocean-visual.spec.ts-snapshots/*-visual-1280-*.png` |
| 1440×900 baselines generated and human-reviewed | Visual baseline | ☐ Approved ☐ Pending | `tests/e2e/data-ocean-visual.spec.ts-snapshots/*-visual-1440-*.png` |
| 1920×1080 baselines generated and human-reviewed | Visual baseline | ☐ Approved ☐ Pending | `tests/e2e/data-ocean-visual.spec.ts-snapshots/*-visual-1920-*.png` |
| No near-black large background in any baseline | Manual review | ☐ Pass ☐ Fail | Human inspection of all baselines |
| No unexpected pure-white Ant Design island in any baseline | Manual review | ☐ Pass ☐ Fail | Human inspection of all baselines |
| No clipped tables, tabs, labels, units, or actions | Manual review | ☐ Pass ☐ Fail | Human inspection of all baselines |
| 1280 viewport has no page-level horizontal scrollbar | Manual review | ☐ Pass ☐ Fail | Human inspection of 1280 baselines |
| 1920 content remains capped and centered | Manual review | ☐ Pass ☐ Fail | Human inspection of 1920 baselines |
| Status and focus remain readable | Manual review | ☐ Pass ☐ Fail | Human inspection of all baselines |
| No sensitive data appears in baselines | Manual review | ☐ Pass ☐ Fail | Human inspection of all baselines |

### D3. Keyboard, 200% zoom, and reduced-motion checks pass

| Criterion | Evidence type | Status | Evidence |
|---|---|---|---|
| Keyboard navigation passes (login, nav, tabs, drawer, modal) | E2E test | ☐ Pass ☐ Fail | `pnpm --dir apps/web e2e -- data-ocean-accessibility.spec.ts -g "keyboard"` |
| 200% zoom: key controls visible | E2E test | ☐ Pass ☐ Fail | `-g "200% zoom"` |
| Reduced-motion: CSS animations disabled | E2E test | ☐ Pass ☐ Fail | `-g "reduced motion"` |
| Reduced-motion: ECharts animation disabled | E2E test | ☐ Pass ☐ Fail | `-g "echarts containers report animation disabled"` |
| 1280px: no page-level horizontal overflow | E2E test | ☐ Pass ☐ Fail | `-g "no page-level horizontal scrollbar"` |
| Performance: page load < 10s, resources < 200 | E2E test | ☐ Pass ☐ Fail | `-g "performance"` |

### D4. No new runtime dependencies

| Criterion | Evidence type | Status | Evidence |
|---|---|---|---|
| No new runtime UI dependency added | Dependency diff | ☐ Pass ☐ Fail | `git diff origin/main -- apps/web/package.json` (dependencies section unchanged) |
| No new animation dependency added | Dependency diff | ☐ Pass ☐ Fail | Same as above |
| No new font dependency added | Dependency diff | ☐ Pass ☐ Fail | Same as above |
| No new chart dependency added | Dependency diff | ☐ Pass ☐ Fail | Same as above (echarts already present) |
| No new state-management dependency added | Dependency diff | ☐ Pass ☐ Fail | Same as above (zustand already present) |
| No new Markdown dependency added | Dependency diff | ☐ Pass ☐ Fail | Same as above (react-markdown already present) |
| No lockfile dependency change | Lockfile diff | ☐ Pass ☐ Fail | `git diff origin/main -- apps/web/pnpm-lock.yaml` (no new entries) |

### D5. Full verification from clean checkout

| Criterion | Evidence type | Status | Evidence |
|---|---|---|---|
| `pnpm --dir apps/web check:theme` exits 0 | Command | ☐ Pass ☐ Fail | _(paste exit code)_ |
| `pnpm --dir apps/web lint` exits 0 | Command | ☐ Pass ☐ Fail | _(paste exit code)_ |
| `pnpm --dir apps/web test` exits 0 | Command | ☐ Pass ☐ Fail | _(paste test count and exit code)_ |
| `pnpm --dir apps/web build` exits 0 | Command | ☐ Pass ☐ Fail | _(paste exit code)_ |
| `pnpm --dir apps/web e2e -- --project=chromium-functional` exits 0 | Command | ☐ Pass ☐ Fail | _(paste test count and exit code)_ |
| `pnpm --dir apps/web e2e -- data-ocean-visual.spec.ts` exits 0 | Command | ☐ Pass ☐ Fail | _(paste test count and exit code)_ |
| `pnpm --dir apps/web e2e -- data-ocean-accessibility.spec.ts` exits 0 | Command | ☐ Pass ☐ Fail | _(paste test count and exit code)_ |
| `git diff --check` exits 0 | Command | ☐ Pass ☐ Fail | _(paste exit code)_ |
| `git status --short` shows only intended acceptance docs | Command | ☐ Pass ☐ Fail | _(paste output)_ |

---

## Theme Boundary Enforcement

| Criterion | Evidence type | Status | Evidence |
|---|---|---|---|
| Scanner flags #030812, #061321 (near-black backgrounds) | Unit test | ☐ Pass ☐ Fail | `node apps/web/scripts/check-theme-literals.test.mjs` (12 tests pass) |
| Scanner flags #1677ff (legacy Ant Design blue) | Unit test | ☐ Pass ☐ Fail | Same as above |
| Scanner flags #f0f2f5, #f0f0f0 (legacy Ant Design grays) | Unit test | ☐ Pass ☐ Fail | Same as above |
| Scanner flags hard-coded white container backgrounds | Unit test | ☐ Pass ☐ Fail | Same as above |
| Scanner exempts src/theme/ and src/styles/ | Unit test | ☐ Pass ☐ Fail | Same as above |
| Scanner exempts chart/data context colors | Unit test | ☐ Pass ☐ Fail | Same as above |
| Scanner exempts test/spec fixtures | Unit test | ☐ Pass ☐ Fail | Same as above |
| `pnpm --dir apps/web check:theme` exits 0 on clean src | Command | ☐ Pass ☐ Fail | _(paste output)_ |

---

## Final Verification Record

### Commands and Output

Run all commands from repository root and paste fresh output below.

```bash
# 1. Theme gate
pnpm --dir apps/web check:theme
# Expected: ✓ No off-theme color literals found in N files

# 2. Lint
pnpm --dir apps/web lint
# Expected: exit 0

# 3. Unit tests
pnpm --dir apps/web test
# Expected: all tests pass, exit 0

# 4. Build
pnpm --dir apps/web build
# Expected: exit 0

# 5. Functional E2E
pnpm --dir apps/web e2e -- --project=chromium-functional
# Expected: all tests pass, exit 0

# 6. Visual regression (requires baselines)
pnpm --dir apps/web e2e -- data-ocean-visual.spec.ts
# Expected: all screenshots match baselines, exit 0

# 7. Accessibility
pnpm --dir apps/web e2e -- data-ocean-accessibility.spec.ts
# Expected: all tests pass, exit 0

# 8. Clean worktree
git diff --check
git status --short --branch
# Expected: no whitespace errors, clean worktree

# 9. Commit at HEAD
git log -1 --oneline
# Expected: release evidence commit at HEAD
```

### Results

| Command | Exit code | Output summary | Date |
|---|---|---|---|
| `check:theme` | | | |
| `lint` | | | |
| `test` | | | |
| `build` | | | |
| `e2e --functional` | | | |
| `e2e --visual` | | | |
| `e2e --accessibility` | | | |
| `git diff --check` | | | |
| `git status --short` | | | |

---

## Sign-off

| Gate | Verifier | Date | Result |
|---|---|---|---|
| Gate A — Foundation | | | ☐ Pass ☐ Fail |
| Gate B — Core modules | | | ☐ Pass ☐ Fail |
| Gate C — Governance & detail | | | ☐ Pass ☐ Fail |
| Gate D — Release | | | ☐ Pass ☐ Fail |

**Release decision:** ☐ Approved ☐ Blocked (list blocking issues below)

### Blocking issues (if any)

| # | Issue | Criterion affected | Fix commit |
|---|---|---|---|
| 1 | | | |
| 2 | | | |
