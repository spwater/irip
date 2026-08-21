# CI Required Checks

## Required Checks for Main Branch Protection

All checks must pass before merge to `main`. No check may be bypassed.

### Code Quality

| Check | Tool | Gate |
|-------|------|------|
| Ruff Lint (Python 3.12/3.13) | `uv run ruff check` | 0 errors |
| Mypy Typecheck | `uv run mypy packages apps/api apps/worker` | 0 errors |
| Unit Tests (Python 3.12/3.13) | `uv run pytest tests/unit` | --cov-fail-under=50 |
| Contract Tests | `uv run pytest tests/contract` | 0 failures |
| Acceptance Tests | `uv run pytest tests/acceptance` | 0 failures |
| Integration Tests | `uv run pytest tests/integration` | --cov-fail-under=50 |
| Security Tests | `uv run pytest tests/security` | 0 failures, 0 skips |
| Recovery Tests | `uv run pytest tests/recovery` | IRIP_REQUIRE_RECOVERY_TESTS=1, 0 skips |

### Frontend

| Check | Tool | Gate |
|-------|------|------|
| Web Lint | `pnpm --dir apps/web lint` | 0 errors |
| Web Tests | `pnpm --dir apps/web test -- --run` | 0 failures |
| Web Build | `pnpm --dir apps/web build` | exit 0 |
| E2E Tests | `pnpm --dir apps/web exec playwright test` | 0 failures |

### Security Scans

| Check | Tool | Gate |
|-------|------|------|
| Dependency Scan (Python) | `pip-audit --skip-editable` | 0 vulns |
| Dependency Scan (Node) | `pnpm audit --prod --audit-level=moderate` | 0 vulns |
| Container Scan | `trivy image --severity HIGH,CRITICAL --exit-code 1` | 0 vulns |
| SAST (Bandit) | `bandit -r packages apps -ll -ii -x tests/` | 0 findings |
| SAST (Semgrep) | `semgrep scan --config security/semgrep.yml --error` | 0 findings |
| Secret Scan | `gitleaks` | 0 findings |
| SBOM | `cyclonedx-py` | artifact produced |

### Load Tests

| Check | Tool | Gate |
|-------|------|------|
| k6 Smoke | `k6 run tests/performance/k6-smoke.js` | http_req_failed rate<0.01, p(95)<1000 |

### Critical Coverage

| Module | Coverage Floor |
|--------|---------------|
| packages.research.timeline | 80% branch |
| apps.api.routers.research_timeline | 80% branch |
| packages.research.planning.plan_analyzer | 70% branch |
| packages.ai.numeric.service | 70% branch |
| packages.facts.query_service | 70% branch |

## Release Evidence Template

Each release must record:

```
Release: vX.Y.Z
Date: YYYY-MM-DD
Commit SHA: <full hash>

Check Results:
- Ruff: PASS
- Mypy: PASS
- Unit Tests: N passed, 0 failed
- Contract Tests: N passed, 0 failed
- Acceptance Tests: N passed, 0 failed
- Integration Tests: N passed, 0 failed
- Security Tests: N passed, 0 skipped
- Recovery Tests: N passed, 0 skipped
- Web Lint: PASS
- Web Tests: N passed, 0 failed
- Web Build: PASS
- E2E: N passed, 0 failed
- Dependency Scan: PASS (0 vulns)
- Container Scan: PASS (0 HIGH/CRITICAL)
- SAST: PASS (0 findings)
- Secret Scan: PASS (0 findings)
- k6 Smoke: PASS (p95 < 1000ms)
- Critical Coverage: PASS (timeline >= 80%)

Signed: <release manager>
```

## Branch Protection

Apply with:
```bash
gh api --method PUT repos/spwater/irip/branches/main/protection --input security/branch-protection.json
```

The `security/branch-protection.json` file must list every check above as a required status check.
