# Data Ocean UI Audit — Keyboard, Zoom, Reduced Motion, and Performance

> This document records the executable browser checks and manual audit results
> for the Data Ocean / Polar Mist UI upgrade. It is tied to the exact commit SHA
> at which the audit was performed and must be updated with fresh command output.

## Audit Metadata

| Field | Value |
|---|---|
| **Commit SHA** | _(to be filled with `git log -1 --format=%H` output)_ |
| **Audit date** | _(to be filled with `date -u +%Y-%m-%dT%H:%M:%SZ` output)_ |
| **Browser** | Chromium (Playwright bundled) |
| **Machine context** | _(OS, CPU, RAM — fill from `uname -a`)_ |
| **Playwright version** | _(fill from `pnpm --dir apps/web list @playwright/test`)_ |
| **Auditor** | _(name / role)_ |

---

## 1. Routes Tested

| Route | Description | Test file |
|---|---|---|
| `/login` | Login page keyboard form | `data-ocean-accessibility.spec.ts` |
| `/workbench` | Workbench dashboard, reduced motion, zoom, overflow | `data-ocean-accessibility.spec.ts` |
| `/standards` | Lab construction tabs | `data-ocean-accessibility.spec.ts` |
| `/lab-ops?tab=flows` | Flow detail, long content scroll | `data-ocean-accessibility.spec.ts` |
| `/lab-ops?tab=facts` | Facts table | `data-ocean-accessibility.spec.ts` |
| `/lab-ops?tab=components` | Components registry, long content scroll | `data-ocean-accessibility.spec.ts` |
| `/platform?tab=assistant` | AI assistant, long content scroll | `data-ocean-accessibility.spec.ts` |
| `/platform?tab=parameters` | Parameter management | `data-ocean-accessibility.spec.ts` |
| `/governance` | Governance console tabs | `data-ocean-accessibility.spec.ts` |
| `/models` | Models list | `data-ocean-accessibility.spec.ts` |
| `/models/predict` | Prediction workbench | `data-ocean-accessibility.spec.ts` |
| `/jobs` | Jobs page | `data-ocean-accessibility.spec.ts` |

---

## 2. Keyboard Result

### Automated Checks

| Check | Status | Evidence |
|---|---|---|
| Login form keyboard navigable (Tab → type → Enter) | ☐ Pass ☐ Fail | `pnpm --dir apps/web e2e -- data-ocean-accessibility.spec.ts -g "login form is keyboard navigable"` |
| Primary nav menu keyboard accessible (focus → Enter) | ☐ Pass ☐ Fail | `-g "primary navigation menu is keyboard accessible"` |
| LabOps tabs keyboard switchable (focus → Enter) | ☐ Pass ☐ Fail | `-g "LabOps tabs are keyboard switchable"` |
| JobDrawer: Enter opens, Escape closes, focus returns | ☐ Pass ☐ Fail | `-g "JobDrawer opens with Enter and closes with Escape"` |
| Governance tabs keyboard switchable | ☐ Pass ☐ Fail | `-g "governance tabs are keyboard switchable"` |

### Manual Checks

| Check | Status | Notes |
|---|---|---|
| One directory filter (Standards) reachable and operable by keyboard | ☐ Pass ☐ Fail | |
| One form (Model create / Parameter submit) reachable by keyboard | ☐ Pass ☐ Fail | |
| One Modal (Model publish) opens with Enter, closes with Escape, focus returns | ☐ Pass ☐ Fail | |
| Focus ring visible on all interactive elements | ☐ Pass ☐ Fail | |
| Tab order is logical (no focus traps outside overlays) | ☐ Pass ☐ Fail | |

---

## 3. 200% Zoom Result

### Automated Checks

| Check | Status | Evidence |
|---|---|---|
| Key controls remain visible at 200% zoom | ☐ Pass ☐ Fail | `-g "key controls remain visible at 200% zoom"` |
| Content frame capped at 1680px on wide viewport | ☐ Pass ☐ Fail | `-g "content frame is capped at 1680px"` |

### Manual Checks

| Check | Status | Notes |
|---|---|---|
| Native 200% browser zoom (Ctrl/Cmd +): all primary destinations readable | ☐ Pass ☐ Fail | |
| No clipped tables, tabs, labels, units, or actions at 200% | ☐ Pass ☐ Fail | |
| Sidebar menu usable at 200% (collapses on narrow) | ☐ Pass ☐ Fail | |

---

## 4. Reduced-Motion Result

### Automated Checks

| Check | Status | Evidence |
|---|---|---|
| `.ocean-atmosphere` animationName is `none` in reduced-motion | ☐ Pass ☐ Fail | `-g "ocean-atmosphere animation is none"` |
| All `[data-echarts]` have `data-echarts-animation="false"` in reduced-motion | ☐ Pass ☐ Fail | `-g "echarts containers report animation disabled"` |
| `.ocean-enter` animation duration < 1ms in reduced-motion | ☐ Pass ☐ Fail | `-g "global CSS transitions are near-zero"` |

### Manual Checks

| Check | Status | Notes |
|---|---|---|
| No non-essential motion visible when `prefers-reduced-motion: reduce` | ☐ Pass ☐ Fail | |
| Status transitions (StatusMark) still convey state without animation | ☐ Pass ☐ Fail | |
| Page enter transitions are instant | ☐ Pass ☐ Fail | |

---

## 5. 1280 Overflow Result

### Automated Checks

| Check | Status | Evidence |
|---|---|---|
| No page-level horizontal scrollbar at 1280px on all 5 primary destinations | ☐ Pass ☐ Fail | `-g "no page-level horizontal scrollbar at 1280px"` |

### Manual Checks

| Check | Status | Notes |
|---|---|---|
| 1280 viewport: Workbench panels stack without clipping | ☐ Pass ☐ Fail | |
| 1280 viewport: LabOps tabs and content fit within width | ☐ Pass ☐ Fail | |
| 1280 viewport: Governance config grid stacks to single column | ☐ Pass ☐ Fail | |
| 1280 viewport: Table-local horizontal scroll is expected and contained | ☐ Pass ☐ Fail | |

---

## 6. Long-Table / Chat / Flow Scroll Result

### Automated Checks

| Check | Status | Evidence |
|---|---|---|
| Long content pages scroll without blocking (< 500ms) | ☐ Pass ☐ Fail | `-g "long content pages scroll without blocking"` |

### Manual Checks

| Page | Scroll behavior | Status | Notes |
|---|---|---|---|
| FlowDetail (`/lab-ops?tab=flows`) | Vertical scroll smooth, no jank | ☐ Pass ☐ Fail | |
| ComponentsPage (`/lab-ops?tab=components`) | Table scroll contained, page scroll smooth | ☐ Pass ☐ Fail | |
| AssistantPage (`/platform?tab=assistant`) | Chat thread scroll smooth, auto-scroll works | ☐ Pass ☐ Fail | |
| AuditPage (`/governance` → 审计事件) | Long audit log scroll smooth | ☐ Pass ☐ Fail | |
| JobsPage (`/jobs` or `/governance` → 作业中心) | Job table scroll smooth | ☐ Pass ☐ Fail | |

---

## 7. Performance Evidence

### Automated Checks

| Check | Status | Evidence |
|---|---|---|
| Workbench page loads within 10s | ☐ Pass ☐ Fail | `-g "workbench page loads within acceptable time"` |
| Resource count < 200 per page | ☐ Pass ☐ Fail | `-g "page resource count is within expected bounds"` |
| Navigation timing has valid response start | ☐ Pass ☐ Fail | `-g "navigation timing has valid response start"` |

### Recorded Metrics

| Metric | Value | Command |
|---|---|---|
| Workbench load time (ms) | _(fill from test output)_ | `pnpm --dir apps/web e2e -- data-ocean-accessibility.spec.ts -g "workbench page loads"` |
| Resource count | _(fill from test output)_ | `-g "page resource count"` |
| Navigation response start (ms) | _(fill from test output)_ | `-g "navigation timing"` |

---

## 8. Known Limitations with User Impact

| # | Limitation | User impact | Mitigation |
|---|---|---|---|
| 1 | _(e.g., 1280px table-local horizontal scroll required for wide tables)_ | Users must scroll within table to see all columns | Expected behavior; table-local scroll is contained |
| 2 | _(fill as discovered)_ | | |
| 3 | _(fill as discovered)_ | | |

---

## 9. Fresh Verification Commands

Run these from the repository root and record output:

```bash
# Commit SHA
git log -1 --format=%H

# Audit date
date -u +%Y-%m-%dT%H:%M:%SZ

# Machine context
uname -a

# Playwright version
pnpm --dir apps/web list @playwright/test

# Full accessibility suite
pnpm --dir apps/web e2e -- data-ocean-accessibility.spec.ts

# Individual checks
pnpm --dir apps/web e2e -- data-ocean-accessibility.spec.ts -g "keyboard"
pnpm --dir apps/web e2e -- data-ocean-accessibility.spec.ts -g "reduced motion"
pnpm --dir apps/web e2e -- data-ocean-accessibility.spec.ts -g "zoom"
pnpm --dir apps/web e2e -- data-ocean-accessibility.spec.ts -g "overflow"
pnpm --dir apps/web e2e -- data-ocean-accessibility.spec.ts -g "performance"
```

---

## Sign-off

| Role | Name | Date | Signature |
|---|---|---|---|
| QA Engineer | | | |
| Frontend Lead | | | |
| Product Manager | | | |
