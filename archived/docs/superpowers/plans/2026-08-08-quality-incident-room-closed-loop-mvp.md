# IRIP Quality Incident Room Closed-Loop MVP Implementation Plan

> **归档状态：未实施。** 该工作线于 2026-08-08 暂停并移入历史资料区，不作为当前开发入口；若未来重新启动，应先按当时的代码、客户和市场事实重新评审，不直接照此计划执行。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a sellable first-stage “IRIP 质量事件室” workflow in which a user can create a cement quality incident, bind and freeze an evidence snapshot, record evidence-backed hypotheses and expert reviews, assign corrective actions, approve an immutable report version, close the incident, and retrieve it from the historical case library.

**Architecture:** Add a focused `packages/quality_incidents` domain beside—not inside—`packages/research`. Reuse IRIP authentication, department-scoped sessions, audit recording, upstream research evidence snapshots, FastAPI composition, and the React shell. Keep this stage synchronous and workflow-focused: it consumes a previously frozen IRIP evidence snapshot and does not yet implement direct LIMS/DCS ingestion, change-point analysis, lag correlation, or automatic similar-case ranking.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, SQLAlchemy async, PostgreSQL 16 JSONB/RLS/immutable triggers, Alembic, pytest, React 18, TypeScript, TanStack Router/Query, Ant Design, Vitest, Testing Library, Playwright, python-docx.

## Global Constraints

- The approved product source is `archived/docs/superpowers/specs/2026-08-08-cement-quality-incident-commercial-entry-design.md`; this plan implements its first workflow slice only.
- Preserve the commercial boundary: the system offers candidate explanations and evidence, never an automatic causal verdict or a control-system write.
- Keep all operational behavior read-only with respect to LIMS/DCS and existing IRIP facts. No endpoint in this plan may modify process-control parameters or upstream evidence.
- Do not add quality-incident behavior to `packages/research`; depend on a narrow read-only gateway so later data and analysis implementations can change independently.
- Every quality-incident table carries `department_id`, has `ENABLE/FORCE ROW LEVEL SECURITY`, and is accessed through `ScopedSessionMixin`.
- Snapshot, expert-review, report-version, and report-approval rows are append-only and protected by `raise_immutable_violation()` triggers.
- Every AI-origin hypothesis must contain source citations and an explicit record of the search for opposing evidence. An uncited AI statement cannot enter an approved report.
- A report version is content-addressed and immutable. Corrections create a new version; approval points to a specific version without rewriting that version.
- Keep API DTOs separate from ORM entities. Domain services return frozen dataclasses and never leak SQLAlchemy objects to routers.
- Use existing error codes where they are semantically correct. Add only `incident_alignment_blocked` and `report_traceability_incomplete` to the central registry.
- Keep the module behind `QUALITY_INCIDENT_MODULE_ENABLED` (default `false`) until the final acceptance task. Tests explicitly enable it.
- Preserve unrelated workspace changes and the documentation consolidation conventions in `docs/STAGE_DELIVERY.md`.

---

## File Structure

| Area | File | Single responsibility |
|---|---|---|
| Domain | `packages/quality_incidents/models.py` | Enums, frozen commands/DTOs, report traceability validation |
| Domain | `packages/quality_incidents/entities.py` | Seven SQLAlchemy table mappings only |
| Domain | `packages/quality_incidents/repository.py` | Department-qualified persistence queries; no business rules |
| Domain | `packages/quality_incidents/evidence.py` | Read-only upstream research-snapshot adapter and canonical hashing |
| Domain | `packages/quality_incidents/service.py` | Lifecycle, evidence, hypothesis, review, action, report, approval, audit orchestration |
| Domain | `packages/quality_incidents/reporting.py` | Pure immutable-report-to-DOCX rendering |
| Database | `migrations/versions/0083_quality_incidents.py` | Tables, constraints, indexes, RLS, immutable triggers, downgrade |
| API | `apps/api/routers/quality_incidents.py` | Pydantic HTTP contract and RBAC dependencies |
| API | `apps/api/composition/quality_incidents.py` | Per-request gateway/service construction and RLS override |
| Web data | `apps/web/src/api/quality-incidents.ts` | HTTP types and functions |
| Web data | `apps/web/src/features/quality-incidents/queries.ts` | TanStack query keys, queries, mutations, invalidation |
| Web collection | `IncidentCenter.tsx`, `CreateIncidentModal.tsx`, `CaseLibrary.tsx` | Event collection, creation, and closed-case search |
| Web detail | `QualityIncidentDetail.tsx` plus focused panels | One incident's evidence, hypotheses, reviews, actions, and report |
| Tests | `tests/unit/quality_incidents/`, `tests/unit/api/` | Pure domain/repository/router tests |
| Tests | `tests/integration/quality_incidents/` | PostgreSQL lifecycle, RLS, immutability, audit atomicity |
| Tests | `tests/acceptance/test_quality_incident_mvp.py`, `tests/e2e/quality-incidents.spec.ts` | Product invariants and browser journey |

## Delivery Boundary

This plan is **Stage 1 of 4**:

1. **This plan — closed-loop MVP:** event → frozen evidence → hypotheses → expert review → actions → approved report → case library.
2. **Next — automatic evidence and diagnostics:** LIMS/DCS window extraction, time/batch alignment, change points, lag relationships, opposing-evidence search, similar-case retrieval, two-hour evidence-pack SLA.
3. **Later — pilot operations:** continuous detection, connector templates, data-quality operations, commercial shell, deployment/monitoring, six-week pilot runbook.
4. **Later — group network:** cross-plant case search, comparable measures, group benchmarks, centrally governed knowledge reuse.

Stage 1 is accepted only as a workflow MVP. It must not be represented as satisfying the two-hour automatic evidence-pack promise by itself.

## Approved-Spec Coverage

| Approved requirement | This plan | Deferred boundary |
|---|---|---|
| 事件—调查—研判—报告—案例闭环 | Tasks 4–12 | — |
| 数据快照、双向证据、专家冲突保留 | Tasks 2, 4, 5, 13 | — |
| 措施、责任人、验证结果、复发状态 | Tasks 6 and 11 | Automatic recurrence linking is Stage 2/3 |
| 报告审核、冻结、修正留版本、导出 | Tasks 6, 8, 11, 13 | Electronic-signature integration is Stage 3 |
| 五个客户业务模块 | Tasks 10–12 | A dedicated commercial-only shell is Stage 3 |
| 部门权限、审计、不可变、100% 引用 | Tasks 2, 6–8, 13–14 | — |
| 化验/DCS 自动提取、时间与批次对齐 | Upstream snapshot is consumed in Task 4 | Stage 2 |
| 变点、滞后、变量贡献、相似事件算法 | Data model accepts their cited output | Stage 2 |
| 两小时证据包、连续监测、六周试点运维 | Workflow records timestamps needed to measure them | Stage 2/3 |
| 跨厂案例网络和集团基准 | Department isolation is preserved | Stage 4 |

---

### Task 1: Lock the domain vocabulary and lifecycle contract

**Files:**
- Create: `packages/quality_incidents/__init__.py`
- Create: `packages/quality_incidents/models.py`
- Create: `tests/unit/quality_incidents/__init__.py`
- Create: `tests/unit/quality_incidents/test_models.py`

**Interfaces:**
- Consumes: approved Chinese workflow terms and the existing domain DTO convention in `packages/research/models.py`.
- Produces: stable enums, commands, evidence-reference shapes, and response DTOs used by repository, service, API, and web tasks.

- [ ] **Step 1: Write the failing lifecycle and report-contract tests**

Create tests that pin the only legal customer-facing states and the report conclusion rule:

```python
from packages.quality_incidents.models import (
    ConclusionStatus,
    IncidentStatus,
    ReportContent,
)


def test_incident_statuses_match_customer_workflow() -> None:
    assert [status.value for status in IncidentStatus] == [
        "open",
        "investigating",
        "pending_review",
        "closed",
    ]


def test_unresolved_report_requires_limitations() -> None:
    content = ReportContent(
        summary="本次事件尚无足够证据确认单一原因",
        timeline=[],
        key_findings=[],
        conclusion_status=ConclusionStatus.UNRESOLVED,
        confirmed_hypothesis_ids=[],
        limitations=[],
        action_ids=[],
    )
    assert content.traceability_errors() == ["unresolved_conclusion_requires_limitations"]
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run --frozen --extra dev pytest tests/unit/quality_incidents/test_models.py -q
```

Expected: FAIL during collection because `packages.quality_incidents` does not exist.

- [ ] **Step 3: Implement exact enums and frozen DTOs**

Implement these values without synonyms:

```python
class IncidentStatus(StrEnum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    PENDING_REVIEW = "pending_review"
    CLOSED = "closed"


class IncidentSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AlignmentStatus(StrEnum):
    READY = "ready"
    DEGRADED = "degraded"
    BLOCKED = "blocked"


class ReviewDecision(StrEnum):
    SUPPORT = "support"
    OPPOSE = "oppose"
    NEEDS_MORE_EVIDENCE = "needs_more_evidence"


class ExpertDiscipline(StrEnum):
    QUALITY = "quality"
    PROCESS = "process"
    EQUIPMENT = "equipment"
    MANAGEMENT = "management"


class ConclusionStatus(StrEnum):
    CONFIRMED = "confirmed"
    UNRESOLVED = "unresolved"


class ConfidenceLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class HypothesisOrigin(StrEnum):
    MANUAL = "manual"
    ANALYSIS = "analysis"
    AI = "ai"


class HypothesisStatus(StrEnum):
    ACTIVE = "active"
    WITHDRAWN = "withdrawn"
```

Define the shared shapes exactly once in this file; later DTOs compose these types rather than redefining dictionaries:

```python
@dataclass(frozen=True)
class EvidenceCitation:
    snapshot_id: UUID
    source_namespace: str
    source_id: str
    field_name: str
    observed_at: datetime | None
    note: str


@dataclass(frozen=True)
class KeyFinding:
    text: str
    evidence: list[EvidenceCitation]


@dataclass(frozen=True)
class CreateIncidentCommand:
    title: str
    line_code: str
    metric_code: str
    metric_name: str
    unit: str
    observed_value: float
    lower_limit: float | None
    upper_limit: float | None
    occurred_at: datetime
    window_start: datetime
    window_end: datetime
    severity: IncidentSeverity


@dataclass(frozen=True)
class IncidentListFilters:
    status: IncidentStatus | None = None
    line_code: str | None = None
    metric_code: str | None = None
    occurred_from: datetime | None = None
    occurred_to: datetime | None = None


@dataclass(frozen=True)
class CreateHypothesisCommand:
    rank: int
    statement: str
    rationale: str
    confidence_level: ConfidenceLevel
    origin: HypothesisOrigin
    support_evidence: list[EvidenceCitation]
    oppose_evidence: list[EvidenceCitation]
    opposing_evidence_notes: str


@dataclass(frozen=True)
class ReportContent:
    summary: str
    timeline: list[dict[str, Any]]
    key_findings: list[KeyFinding]
    conclusion_status: ConclusionStatus
    confirmed_hypothesis_ids: list[UUID]
    limitations: list[str]
    action_ids: list[UUID]

    def traceability_errors(self) -> list[str]:
        errors = [
            "key_finding_requires_evidence"
            for finding in self.key_findings
            if not finding.evidence
        ]
        if self.conclusion_status is ConclusionStatus.CONFIRMED and not self.confirmed_hypothesis_ids:
            errors.append("confirmed_conclusion_requires_hypothesis")
        if self.conclusion_status is ConclusionStatus.UNRESOLVED and not self.limitations:
            errors.append("unresolved_conclusion_requires_limitations")
        return errors
```

Define the response dataclasses with these signatures (all use `@dataclass(frozen=True)`):

```python
class IncidentRef:
    incident_id: UUID
    title: str
    line_code: str
    metric_name: str
    occurred_at: datetime
    severity: IncidentSeverity
    status: IncidentStatus

class IncidentDetail:
    incident_id: UUID
    title: str
    line_code: str
    metric_name: str
    occurred_at: datetime
    severity: IncidentSeverity
    status: IncidentStatus
    owner_user_id: UUID
    metric_code: str
    unit: str
    observed_value: float
    lower_limit: float | None
    upper_limit: float | None
    window_start: datetime
    window_end: datetime
    current_snapshot_id: UUID | None
    current_report_version: int
    approved_report_version: int | None
    lock_version: int

class UpstreamSnapshotRef:
    snapshot_id: UUID
    content_hash: str
    source_refs: list[dict[str, Any]]
    field_manifest: dict[str, Any]
    permission_envelope: dict[str, Any]

class IncidentSnapshotRef:
    snapshot_id: UUID
    incident_id: UUID
    snapshot_number: int
    content_hash: str
    upstream_snapshot_id: UUID
    upstream_content_hash: str
    source_refs: list[dict[str, Any]]
    field_manifest: dict[str, Any]
    permission_envelope: dict[str, Any]
    alignment_status: AlignmentStatus
    alignment_notes: str
    data_quality: dict[str, Any]
    captured_at: datetime

class HypothesisRef:
    hypothesis_id: UUID
    incident_id: UUID
    snapshot_id: UUID
    rank: int
    statement: str
    rationale: str
    confidence_level: ConfidenceLevel
    origin: HypothesisOrigin
    support_evidence: list[EvidenceCitation]
    oppose_evidence: list[EvidenceCitation]
    opposing_evidence_notes: str
    status: HypothesisStatus

class ExpertReviewRef:
    review_id: UUID
    hypothesis_id: UUID
    decision: ReviewDecision
    discipline: ExpertDiscipline
    comment: str
    evidence_refs: list[EvidenceCitation]
    created_at: datetime
    created_by: UUID

class ActionRef:
    action_id: UUID
    incident_id: UUID
    description: str
    owner_user_id: UUID
    due_at: datetime
    status: str
    verification_result: str | None
    recurrence_status: str
    lock_version: int

class ReportVersionRef:
    report_version_id: UUID
    incident_id: UUID
    snapshot_id: UUID
    version_number: int
    content: ReportContent
    content_hash: str
    created_at: datetime
    created_by: UUID

class ReportApprovalRef:
    approval_id: UUID
    report_version_id: UUID
    decision: str
    comment: str
    created_at: datetime
    created_by: UUID
```

`ReportContent.traceability_errors()` must enforce:

- every key finding contains at least one `EvidenceCitation`;
- `confirmed` contains at least one confirmed hypothesis;
- `unresolved` contains at least one limitation.

Task 6's service-level report validation additionally loads each referenced hypothesis and enforces that it has a support-evidence list plus either opposing citations or non-empty `opposing_evidence_notes`; that cross-object rule does not belong in the standalone `ReportContent` value object.

- [ ] **Step 4: Run the focused tests and type checker**

```bash
uv run --frozen --extra dev pytest tests/unit/quality_incidents/test_models.py -q
uv run --frozen --extra dev mypy packages/quality_incidents
```

Expected: both PASS.

- [ ] **Step 5: Commit the contract**

```bash
git add packages/quality_incidents tests/unit/quality_incidents
git commit -m "feat: define quality incident domain contract"
```

---

### Task 2: Add role permissions, tenant schema, and immutable entities

**Files:**
- Modify: `packages/auth/permissions.py`
- Modify: `packages/common/error_codes.py`
- Create: `packages/quality_incidents/entities.py`
- Create: `migrations/versions/0083_quality_incidents.py`
- Modify: `tests/conftest.py`
- Create: `tests/unit/quality_incidents/test_permissions.py`
- Create: `tests/unit/quality_incidents/test_migration_0083.py`
- Create: `tests/unit/quality_incidents/test_entities.py`

**Interfaces:**
- Consumes: `RoleCode`, `Permission.all()`, `GUID`, `UTCDateTime`, `raise_immutable_violation()`, and `current_visible_dept_ids()`.
- Produces: seven department-isolated tables and four explicit permissions.

- [ ] **Step 1: Write failing permission-matrix tests**

Pin the intended separation of duties:

```python
from typing import cast

from packages.auth.permissions import BUILTIN_ROLES, Permission


def permissions_for(role_code: str) -> list[str]:
    return cast(list[str], BUILTIN_ROLES[role_code]["permissions"])


def test_quality_incident_permissions_follow_role_separation() -> None:
    assert Permission.QUALITY_INCIDENT_APPROVE in permissions_for("lab_director")
    assert Permission.QUALITY_INCIDENT_REVIEW in permissions_for("lab_member")
    assert Permission.QUALITY_INCIDENT_APPROVE not in permissions_for("lab_member")
    assert Permission.QUALITY_INCIDENT_READ in permissions_for("lab_viewer")
    assert Permission.QUALITY_INCIDENT_WRITE not in permissions_for("lab_viewer")
    assert Permission.QUALITY_INCIDENT_READ in permissions_for("platform_auditor")
```

- [ ] **Step 2: Run the permission test to verify it fails**

```bash
uv run --frozen --extra dev pytest tests/unit/quality_incidents/test_permissions.py -q
```

Expected: FAIL because the permission constants are missing.

- [ ] **Step 3: Add the four permissions and two errors**

Add:

```python
QUALITY_INCIDENT_READ = "quality_incident:read"
QUALITY_INCIDENT_WRITE = "quality_incident:write"
QUALITY_INCIDENT_REVIEW = "quality_incident:review"
QUALITY_INCIDENT_APPROVE = "quality_incident:approve"
```

Role matrix:

| Role | read | write | review | approve |
|---|---:|---:|---:|---:|
| platform_administrator | yes | yes | yes | yes |
| platform_auditor | yes | no | no | no |
| lab_director | yes | yes | yes | yes |
| lab_member | yes | yes | yes | no |
| lab_viewer | yes | no | no | no |

Append all four constants to `Permission.all()` and apply the role table above. Register the errors exactly as:

```python
INCIDENT_ALIGNMENT_BLOCKED = ("incident_alignment_blocked", 422)
REPORT_TRACEABILITY_INCOMPLETE = ("report_traceability_incomplete", 422)
```

- [ ] **Step 4: Write the failing migration and entity tests**

Test the new Alembic revision metadata, table names, RLS statements, and immutable triggers. Test that every ORM entity exposes a non-null `department_id` and that JSON fields use JSONB.

```python
from pathlib import Path

from sqlalchemy.dialects.postgresql import JSONB

from packages.quality_incidents.entities import QUALITY_INCIDENT_ENTITIES


EXPECTED_TABLES = {
    "quality_incident",
    "quality_incident_snapshot",
    "quality_incident_hypothesis",
    "quality_incident_review",
    "quality_incident_action",
    "quality_incident_report_version",
    "quality_incident_report_approval",
}


def test_migration_0083_has_tables_rls_and_immutability() -> None:
    source = Path("migrations/versions/0083_quality_incidents.py").read_text()
    assert 'revision = "0083"' in source
    assert 'down_revision = "0082"' in source
    for table in EXPECTED_TABLES:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in source
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in source
        assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in source
    for table in {
        "quality_incident_snapshot",
        "quality_incident_review",
        "quality_incident_report_version",
        "quality_incident_report_approval",
    }:
        assert f"prevent_modify_{table}" in source


def test_every_entity_is_department_scoped() -> None:
    assert {entity.__tablename__ for entity in QUALITY_INCIDENT_ENTITIES} == EXPECTED_TABLES
    for entity in QUALITY_INCIDENT_ENTITIES:
        column = entity.__table__.columns["department_id"]
        assert column.nullable is False


def test_json_columns_use_jsonb() -> None:
    json_columns = {
        "quality_incident_snapshot": {"source_refs", "field_manifest", "permission_envelope", "data_quality"},
        "quality_incident_hypothesis": {"support_evidence", "oppose_evidence"},
        "quality_incident_review": {"evidence_refs"},
        "quality_incident_report_version": {"content"},
    }
    by_name = {entity.__tablename__: entity for entity in QUALITY_INCIDENT_ENTITIES}
    for table, columns in json_columns.items():
        for name in columns:
            assert isinstance(by_name[table].__table__.columns[name].type, JSONB)
```

- [ ] **Step 5: Run the schema tests to verify they fail**

```bash
uv run --frozen --extra dev pytest \
  tests/unit/quality_incidents/test_migration_0083.py \
  tests/unit/quality_incidents/test_entities.py -q
```

Expected: FAIL because revision `0083` and the entities do not exist.

- [ ] **Step 6: Implement the exact relational model**

Create the following tables:

| Table | Required business columns |
|---|---|
| `quality_incident` | `id`, `department_id`, `owner_user_id`, `title`, `line_code`, `metric_code`, `metric_name`, `unit`, `observed_value`, `lower_limit`, `upper_limit`, `occurred_at`, `window_start`, `window_end`, `severity`, `status`, `current_snapshot_id`, `current_report_version`, `approved_report_version`, `created_at`, `updated_at`, `lock_version` |
| `quality_incident_snapshot` | `id`, `incident_id`, `department_id`, `snapshot_number`, `upstream_snapshot_id`, `upstream_content_hash`, `content_hash`, `source_refs`, `field_manifest`, `permission_envelope`, `alignment_status`, `alignment_notes`, `data_quality`, `captured_at`, `created_by` |
| `quality_incident_hypothesis` | `id`, `incident_id`, `department_id`, `snapshot_id`, `rank`, `statement`, `rationale`, `confidence_level`, `origin`, `support_evidence`, `oppose_evidence`, `opposing_evidence_notes`, `status`, `created_at`, `created_by` |
| `quality_incident_review` | `id`, `incident_id`, `department_id`, `hypothesis_id`, `decision`, `discipline`, `comment`, `evidence_refs`, `created_at`, `created_by` |
| `quality_incident_action` | `id`, `incident_id`, `department_id`, `description`, `owner_user_id`, `due_at`, `status`, `verification_result`, `recurrence_status`, `verified_at`, `verified_by`, `created_at`, `updated_at`, `lock_version` |
| `quality_incident_report_version` | `id`, `incident_id`, `department_id`, `snapshot_id`, `version_number`, `content`, `content_hash`, `created_at`, `created_by` |
| `quality_incident_report_approval` | `id`, `incident_id`, `department_id`, `report_version_id`, `decision`, `comment`, `created_at`, `created_by` |

Use this mapping style for every table and export the list used by the test:

```python
class QualityIncident(Base):
    __tablename__ = "quality_incident"
    __table_args__ = (sa.UniqueConstraint("id", "department_id"),)

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    department_id: Mapped[UUID] = mapped_column(
        GUID, sa.ForeignKey("department.id"), nullable=False
    )
    owner_user_id: Mapped[UUID] = mapped_column(
        GUID, sa.ForeignKey("app_user.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(sa.Text, nullable=False)
    line_code: Mapped[str] = mapped_column(sa.Text, nullable=False)
    metric_code: Mapped[str] = mapped_column(sa.Text, nullable=False)
    metric_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    unit: Mapped[str] = mapped_column(sa.Text, nullable=False)
    observed_value: Mapped[float] = mapped_column(sa.Float, nullable=False)
    lower_limit: Mapped[float | None] = mapped_column(sa.Float)
    upper_limit: Mapped[float | None] = mapped_column(sa.Float)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    window_start: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    window_end: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    severity: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False, default="open")
    current_snapshot_id: Mapped[UUID | None] = mapped_column(GUID)
    current_report_version: Mapped[int] = mapped_column(sa.Integer, default=0)
    approved_report_version: Mapped[int | None] = mapped_column(sa.Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=sa.func.now())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=sa.func.now())
    lock_version: Mapped[int] = mapped_column(sa.Integer, default=0)


QUALITY_INCIDENT_ENTITIES = (
    QualityIncident,
    QualityIncidentSnapshot,
    QualityIncidentHypothesis,
    QualityIncidentReview,
    QualityIncidentAction,
    QualityIncidentReportVersion,
    QualityIncidentReportApproval,
)
```

Use composite uniqueness on `(id, department_id)` for parent rows and composite foreign keys on child rows so an object can never point across departments. Add unique `(incident_id, version_number)` constraints for snapshots and reports. Add indexes for `(department_id, status, occurred_at DESC)`, `(incident_id, rank)`, action owner/due date, and approved report lookup.

Apply this policy to all seven tables:

```python
TENANT_TABLES = (
    "quality_incident",
    "quality_incident_snapshot",
    "quality_incident_hypothesis",
    "quality_incident_review",
    "quality_incident_action",
    "quality_incident_report_version",
    "quality_incident_report_approval",
)

for table in TENANT_TABLES:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} USING ("
        "department_id IN (SELECT current_visible_dept_ids()))"
    )
```

Attach `raise_immutable_violation()` to `quality_incident_snapshot`, `quality_incident_review`, `quality_incident_report_version`, and `quality_incident_report_approval` for `BEFORE UPDATE OR DELETE`.

- [ ] **Step 7: Register metadata imports and verify migration shape**

Import `packages.quality_incidents.entities` in `tests/conftest.py`. Then run:

```bash
uv run --frozen --extra dev pytest tests/unit/quality_incidents/test_permissions.py \
  tests/unit/quality_incidents/test_migration_0083.py \
  tests/unit/quality_incidents/test_entities.py -q
uv run --frozen alembic heads
```

Expected: tests PASS and Alembic reports the single head `0083`.

- [ ] **Step 8: Commit schema and access policy**

```bash
git add packages/auth/permissions.py packages/common/error_codes.py \
  packages/quality_incidents/entities.py migrations/versions/0083_quality_incidents.py \
  tests/conftest.py tests/unit/quality_incidents
git commit -m "feat: add tenant-safe quality incident schema"
```

---

### Task 3: Build a department-safe repository boundary

**Files:**
- Create: `packages/quality_incidents/repository.py`
- Create: `tests/unit/quality_incidents/test_repository.py`

**Interfaces:**
- Consumes: `AsyncSession`, quality-incident ORM entities, opaque cursor helpers, and UUID department scope.
- Produces: persistence methods that require `department_id` even though RLS also protects the database.

- [ ] **Step 1: Write failing repository tests with an async-session spy**

Cover create, get, cursor list, child insert, current snapshot/report pointers, and optimistic action updates. Assert every select/update includes a department predicate.

```python
from unittest.mock import AsyncMock


async def test_get_incident_filters_by_id_and_department() -> None:
    session = AsyncMock()
    session.scalar.return_value = None
    await QualityIncidentRepository.get_incident(session, INCIDENT_ID, DEPT_ID)
    statement = session.scalar.await_args.args[0]
    sql = str(statement)
    assert "quality_incident.id" in sql
    assert "quality_incident.department_id" in sql
```

- [ ] **Step 2: Run the repository test to verify it fails**

```bash
uv run --frozen --extra dev pytest tests/unit/quality_incidents/test_repository.py -q
```

Expected: FAIL because the repository is missing.

- [ ] **Step 3: Implement repository methods as persistence only**

Start with the department-qualified read and use the same predicate on every method:

```python
class QualityIncidentRepository:
    @staticmethod
    async def get_incident(
        session: AsyncSession,
        incident_id: UUID,
        department_id: UUID,
        *,
        for_update: bool = False,
    ) -> QualityIncident | None:
        statement = sa.select(QualityIncident).where(
            QualityIncident.id == incident_id,
            QualityIncident.department_id == department_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return await session.scalar(statement)

    @staticmethod
    async def insert_incident(
        session: AsyncSession,
        incident: QualityIncident,
    ) -> QualityIncident:
        session.add(incident)
        await session.flush()
        return incident
```

Complete the repository surface with these exact inputs and outputs, applying the concrete `get_incident` query pattern above to every row lookup and mutation:

| Method | Inputs after `session` | Return |
|---|---|---|
| `list_incidents` | `department_id: UUID, filters: IncidentListFilters, cursor: str | None, page_size: int` | `tuple[list[QualityIncident], str | None]` |
| `insert_snapshot` | `snapshot: QualityIncidentSnapshot` | `QualityIncidentSnapshot` |
| `get_snapshot` | `snapshot_id: UUID, department_id: UUID` | `QualityIncidentSnapshot | None` |
| `get_current_snapshot` | `incident_id: UUID, department_id: UUID` | `QualityIncidentSnapshot | None` |
| `insert_hypothesis` | `hypothesis: QualityIncidentHypothesis` | `QualityIncidentHypothesis` |
| `list_hypotheses` | `incident_id: UUID, department_id: UUID` | `list[QualityIncidentHypothesis]` |
| `withdraw_hypothesis` | `hypothesis_id: UUID, department_id: UUID` | `None` |
| `insert_review` | `review: QualityIncidentReview` | `QualityIncidentReview` |
| `list_reviews` | `incident_id: UUID, department_id: UUID` | `list[QualityIncidentReview]` |
| `insert_action` | `action: QualityIncidentAction` | `QualityIncidentAction` |
| `update_action` | `action_id: UUID, department_id: UUID, lock_version: int, values: Mapping[str, object]` | `QualityIncidentAction` |
| `list_actions` | `incident_id: UUID, department_id: UUID` | `list[QualityIncidentAction]` |
| `insert_report_version` | `version: QualityIncidentReportVersion` | `QualityIncidentReportVersion` |
| `get_report_version` | `incident_id: UUID, department_id: UUID, version_number: int` | `QualityIncidentReportVersion | None` |
| `list_report_versions` | `incident_id: UUID, department_id: UUID` | `list[QualityIncidentReportVersion]` |
| `insert_report_approval` | `approval: QualityIncidentReportApproval` | `QualityIncidentReportApproval` |
| `get_latest_approval` | `incident_id: UUID, department_id: UUID` | `QualityIncidentReportApproval | None` |
| `get_approval_for_report_version` | `report_version_id: UUID, department_id: UUID` | `QualityIncidentReportApproval | None` |
| `set_incident_status` | `incident: QualityIncident, status: IncidentStatus` | `None` |
| `set_current_snapshot` | `incident: QualityIncident, snapshot_id: UUID` | `None` |
| `set_report_pointers` | `incident: QualityIncident, current_version: int, approved_version: int | None` | `None` |

Each method is one SQLAlchemy statement plus `flush()` when mutating. An optimistic update that affects zero rows raises `AppError(code="conflict")`.

Repository methods may flush but may not commit, open sessions, run business transitions, or record audit events. Lists use the existing opaque cursor convention and stable ordering `(occurred_at DESC, id DESC)`.

- [ ] **Step 4: Run focused tests and static checks**

```bash
uv run --frozen --extra dev pytest tests/unit/quality_incidents/test_repository.py -q
uv run --frozen --extra dev ruff check packages/quality_incidents/repository.py \
  tests/unit/quality_incidents/test_repository.py
uv run --frozen --extra dev mypy packages/quality_incidents/repository.py
```

Expected: PASS.

- [ ] **Step 5: Commit repository boundary**

```bash
git add packages/quality_incidents/repository.py tests/unit/quality_incidents/test_repository.py
git commit -m "feat: add quality incident repository"
```

---

### Task 4: Freeze authoritative incident evidence and start investigation

**Files:**
- Modify: `packages/quality_incidents/models.py`
- Create: `packages/quality_incidents/evidence.py`
- Create: `packages/quality_incidents/service.py`
- Create: `tests/unit/quality_incidents/test_evidence.py`
- Create: `tests/unit/quality_incidents/test_service_lifecycle.py`

**Interfaces:**
- Consumes: an upstream `research_evidence_snapshot` through `UpstreamEvidenceGateway`, scoped DB sessions, and `AuditRecorder`.
- Produces: an immutable incident snapshot and the atomic `open → investigating` transition.

- [ ] **Step 1: Write failing evidence-gateway and canonical-hash tests**

Define a narrow protocol:

```python
class UpstreamEvidenceGateway(Protocol):
    async def get_snapshot(
        self, snapshot_id: UUID, department_id: UUID
    ) -> UpstreamSnapshotRef: ...


def test_canonical_snapshot_hash_ignores_dict_key_order() -> None:
    left = {"source": {"id": "a", "version": 1}, "quality": {"missing": 0.01}}
    right = {"quality": {"missing": 0.01}, "source": {"version": 1, "id": "a"}}
    assert canonical_snapshot_hash(left) == canonical_snapshot_hash(right)
```

Also compile the adapter's select statement and assert it joins `research_evidence_snapshot` to `research_workspace` and contains `research_workspace.department_id = :department_id_1`. The returned `UpstreamSnapshotRef` must copy `source_refs`, `field_manifest`, `permission_envelope`, and the authoritative upstream hash.

- [ ] **Step 2: Write failing lifecycle tests**

Cover:

- create returns status `open` and audits `quality_incident.created`;
- freeze fails when the upstream snapshot belongs to another department;
- freeze with `alignment_status=blocked` stores no snapshot and keeps `open`;
- valid freeze copies upstream metadata, calculates its own hash, stores one immutable row, points `current_snapshot_id` to it, and changes status to `investigating` in one transaction;
- a second freeze creates `snapshot_number=2`, never updates snapshot 1, and is allowed only while `investigating`.

```python
async def test_blocked_alignment_does_not_freeze_or_transition(service, repository) -> None:
    command = FreezeSnapshotCommand(
        upstream_snapshot_id=UPSTREAM_ID,
        alignment_status=AlignmentStatus.BLOCKED,
        alignment_notes="化验批次与 DCS 时钟相差 45 分钟",
        data_quality={"completeness_ratio": 0.72},
    )
    with pytest.raises(AppError) as exc:
        await service.freeze_snapshot(INCIDENT_ID, command)
    assert exc.value.code == "incident_alignment_blocked"
    repository.insert_snapshot.assert_not_awaited()
    repository.set_incident_status.assert_not_awaited()


async def test_valid_freeze_creates_snapshot_and_starts_investigation(
    service, repository
) -> None:
    result = await service.freeze_snapshot(INCIDENT_ID, READY_COMMAND)
    assert result.snapshot_number == 1
    assert result.alignment_status is AlignmentStatus.READY
    repository.insert_snapshot.assert_awaited_once()
    repository.set_current_snapshot.assert_awaited_once()
    repository.set_incident_status.assert_awaited_once_with(
        ANY, ANY, IncidentStatus.INVESTIGATING
    )
```

- [ ] **Step 3: Run the evidence and lifecycle tests to verify they fail**

```bash
uv run --frozen --extra dev pytest \
  tests/unit/quality_incidents/test_evidence.py \
  tests/unit/quality_incidents/test_service_lifecycle.py -q
```

Expected: FAIL because the gateway, command, hasher, and service are missing.

- [ ] **Step 4: Implement gateway, hashing, and service session discipline**

Add the command to `models.py`:

```python
@dataclass(frozen=True)
class FreezeSnapshotCommand:
    upstream_snapshot_id: UUID
    alignment_status: AlignmentStatus
    alignment_notes: str
    data_quality: dict[str, Any]
```

`QualityIncidentService` must inherit `ScopedSessionMixin` and initialize:

```python
self._factory = session_factory
self._dept_id = department_id
self._actor_id = actor_id
self._upstream_evidence = upstream_evidence
self._rls_dept_id: UUID | None = None
```

Build the incident hash from canonical JSON containing the upstream hash, copied refs/manifests/envelope, alignment assessment, and data-quality declaration:

```python
payload = json.dumps(snapshot_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
content_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

Implement the transition in one scoped transaction:

```python
async def freeze_snapshot(
    self,
    incident_id: UUID,
    command: FreezeSnapshotCommand,
) -> IncidentSnapshotRef:
    if command.alignment_status is AlignmentStatus.BLOCKED:
        raise AppError(
            code="incident_alignment_blocked",
            message="时间或批次未可靠对齐，不能冻结调查证据",
            retryable=False,
            fields={"incident_id": str(incident_id)},
        )
    async with self._scoped_session() as session:
        incident = await self._repository.get_incident(
            session, incident_id, self._dept_id, for_update=True
        )
        upstream = await self._upstream_evidence.get_snapshot(
            command.upstream_snapshot_id, self._dept_id
        )
        snapshot = self._build_snapshot(incident, upstream, command)
        await self._repository.insert_snapshot(session, snapshot)
        await self._repository.set_current_snapshot(session, incident, snapshot.id)
        await self._repository.set_incident_status(
            session, incident, IncidentStatus.INVESTIGATING
        )
        await self._audit.record(session, self._snapshot_audit(incident, snapshot))
        return self._snapshot_ref(snapshot)
```

All mutation plus `AuditRecorder.record(...)` occurs inside one `self._scoped_session()` transaction.

- [ ] **Step 5: Run focused tests and type checks**

```bash
uv run --frozen --extra dev pytest \
  tests/unit/quality_incidents/test_evidence.py \
  tests/unit/quality_incidents/test_service_lifecycle.py -q
uv run --frozen --extra dev mypy packages/quality_incidents/evidence.py \
  packages/quality_incidents/service.py
```

Expected: PASS.

- [ ] **Step 6: Commit the first end-to-end domain action**

```bash
git add packages/quality_incidents/evidence.py packages/quality_incidents/service.py \
  tests/unit/quality_incidents/test_evidence.py \
  tests/unit/quality_incidents/test_service_lifecycle.py
git commit -m "feat: freeze quality incident evidence"
```

---

### Task 5: Add evidence-backed hypotheses and append-only expert review

**Files:**
- Modify: `packages/quality_incidents/models.py`
- Modify: `packages/quality_incidents/service.py`
- Modify: `packages/quality_incidents/repository.py`
- Create: `tests/unit/quality_incidents/test_hypothesis_review.py`

**Interfaces:**
- Consumes: the current incident snapshot and write/review commands.
- Produces: ranked hypotheses with bidirectional evidence and an uneditable multidisciplinary review history.

- [ ] **Step 1: Write failing hypothesis-validation tests**

Cover these rules:

- only an `investigating` incident accepts hypotheses;
- every evidence citation references the current incident snapshot;
- `origin="ai"` requires non-empty support citations and either opposing citations or non-empty opposing-search notes;
- manual hypotheses may be unresolved but still require a rationale;
- ranks are positive and unique among active hypotheses.

```python
async def test_ai_hypothesis_without_bidirectional_evidence_is_rejected(service) -> None:
    with pytest.raises(AppError) as exc:
        await service.add_hypothesis(
            INCIDENT_ID,
            CreateHypothesisCommand(
                rank=1,
                statement="窑尾喂料波动可能与本次质量偏差相关",
                rationale="异常前一小时波动幅度显著增加",
                confidence_level=ConfidenceLevel.MEDIUM,
                origin=HypothesisOrigin.AI,
                support_evidence=[],
                oppose_evidence=[],
                opposing_evidence_notes="",
            ),
        )
    assert exc.value.code == "report_traceability_incomplete"
```

- [ ] **Step 2: Write failing review-history tests**

Add this contradiction-retention test plus parametrized validation cases for empty comments on `oppose` and `needs_more_evidence`:

```python
async def test_conflicting_reviews_are_both_retained(service) -> None:
    support = await service.add_review(
        INCIDENT_ID,
        HYPOTHESIS_ID,
        CreateReviewCommand(
            decision=ReviewDecision.SUPPORT,
            discipline=ExpertDiscipline.PROCESS,
            comment="喂料波动时间领先质量异常",
            evidence_refs=[CITATION],
        ),
    )
    oppose = await service.add_review(
        INCIDENT_ID,
        HYPOTHESIS_ID,
        CreateReviewCommand(
            decision=ReviewDecision.OPPOSE,
            discipline=ExpertDiscipline.QUALITY,
            comment="同批次复检结果未复现",
            evidence_refs=[OPPOSING_CITATION],
        ),
    )
    view = await service.get_investigation(INCIDENT_ID)
    assert [review.review_id for review in view.reviews] == [
        support.review_id,
        oppose.review_id,
    ]
```

- [ ] **Step 3: Run the hypothesis/review test to verify it fails**

```bash
uv run --frozen --extra dev pytest tests/unit/quality_incidents/test_hypothesis_review.py -q
```

Expected: FAIL because review commands and service methods are missing.

- [ ] **Step 4: Implement service methods and repository queries**

Add the exact DTOs:

```python
@dataclass(frozen=True)
class CreateReviewCommand:
    decision: ReviewDecision
    discipline: ExpertDiscipline
    comment: str
    evidence_refs: list[EvidenceCitation]


@dataclass(frozen=True)
class InvestigationView:
    snapshot: IncidentSnapshotRef
    hypotheses: list[HypothesisRef]
    reviews: list[ExpertReviewRef]
```

Implement the validation before insert and leave aggregation read-only:

```python
def _validate_hypothesis(command: CreateHypothesisCommand, snapshot_id: UUID) -> None:
    citations = [*command.support_evidence, *command.oppose_evidence]
    if any(citation.snapshot_id != snapshot_id for citation in citations):
        raise AppError(code="validation_failed", message="证据不属于当前快照", retryable=False, fields={})
    if command.origin is HypothesisOrigin.AI and (
        not command.support_evidence
        or (not command.oppose_evidence and not command.opposing_evidence_notes.strip())
    ):
        raise AppError(code="report_traceability_incomplete", message="AI 假设缺少双向证据", retryable=False, fields={})


async def get_investigation(self, incident_id: UUID) -> InvestigationView:
    async with self._scoped_session() as session:
        snapshot = await self._repository.get_current_snapshot(
            session, incident_id, self._dept_id
        )
        hypotheses = await self._repository.list_hypotheses(session, incident_id, self._dept_id)
        reviews = await self._repository.list_reviews(session, incident_id, self._dept_id)
        return self._investigation_view(snapshot, hypotheses, reviews)
```

Implement `add_hypothesis`, `withdraw_hypothesis`, and `add_review` with the same scoped-session/audit pattern from Task 4. Preserve all review rows; never derive one mutable “consensus” field.

- [ ] **Step 5: Run tests and checks**

```bash
uv run --frozen --extra dev pytest tests/unit/quality_incidents/test_hypothesis_review.py -q
uv run --frozen --extra dev ruff check packages/quality_incidents tests/unit/quality_incidents
uv run --frozen --extra dev mypy packages/quality_incidents
```

Expected: PASS.

- [ ] **Step 6: Commit expert-review workflow**

```bash
git add packages/quality_incidents tests/unit/quality_incidents/test_hypothesis_review.py
git commit -m "feat: add quality incident expert review"
```

---

### Task 6: Add corrective actions, versioned reports, approval, and closure

**Files:**
- Modify: `packages/quality_incidents/models.py`
- Modify: `packages/quality_incidents/repository.py`
- Modify: `packages/quality_incidents/service.py`
- Create: `tests/unit/quality_incidents/test_actions.py`
- Create: `tests/unit/quality_incidents/test_reports.py`
- Create: `tests/unit/quality_incidents/test_closure.py`

**Interfaces:**
- Consumes: investigation state, expert reviews, evidence citations, action commands, and an approving actor.
- Produces: tracked actions, immutable report versions, append-only approvals, and guarded incident closure.

- [ ] **Step 1: Write failing action tests**

Pin statuses `open`, `verified`, `ineffective`, `cancelled` and recurrence states `unknown`, `not_recurred`, `recurred`:

```python
def test_verified_action_requires_result() -> None:
    command = UpdateActionCommand(
        status=ActionStatus.VERIFIED,
        verification_result="",
        recurrence_status=RecurrenceStatus.NOT_RECURRED,
        lock_version=2,
    )
    assert command.validation_errors() == ["verified_action_requires_result"]


async def test_stale_action_update_is_rejected(service) -> None:
    with pytest.raises(AppError) as exc:
        await service.update_action(INCIDENT_ID, ACTION_ID, STALE_COMMAND)
    assert exc.value.code == "conflict"
```

`CreateActionCommand` requires non-empty description, `owner_user_id: UUID`, and timezone-aware `due_at`. `UpdateActionCommand` requires `verification_result`, verifier, and verification timestamp when status becomes `verified` or `ineffective`.

- [ ] **Step 2: Write failing report-version tests**

Cover:

- report version starts at 1 and increments under a row lock;
- content hash is stable for semantically identical JSON;
- every key finding and included hypothesis passes `traceability_errors()`;
- the report cites the current snapshot and existing actions/hypotheses only;
- creating a correction inserts version 2 and leaves version 1 byte-for-byte unchanged;
- creating a new version while investigating clears the incident's approved-version pointer but never deletes the old approval;
- creating a correction for a closed incident preserves the last approved pointer until the correction is approved, so the case library never exposes an unapproved version.

```python
async def test_closed_correction_keeps_last_approved_report_until_reapproved(service) -> None:
    version_two = await service.create_report_version(CLOSED_INCIDENT_ID, CORRECTED_CONTENT)
    detail = await service.get_incident(CLOSED_INCIDENT_ID)
    assert version_two.version_number == 2
    assert detail.current_report_version == 2
    assert detail.approved_report_version == 1
    await service.approve_report_version(CLOSED_INCIDENT_ID, 2, "修正批次编号")
    corrected = await service.get_incident(CLOSED_INCIDENT_ID)
    assert corrected.status is IncidentStatus.CLOSED
    assert corrected.approved_report_version == 2
```

- [ ] **Step 3: Write failing approval and transition tests**

The exact lifecycle is:

```text
open --freeze valid snapshot--> investigating
investigating --submit report--> pending_review
pending_review --request changes--> investigating
pending_review --approve latest report--> pending_review
pending_review --close--> closed
```

Enforce:

- submit requires a current snapshot, at least one active hypothesis, a review for every active hypothesis, and a report version;
- approval requires `quality_incident:approve` at the API boundary and a different user from the report-version creator;
- approval is blocked when alignment is `blocked`, the approved version is not the latest, or traceability validation fails;
- close requires the latest report version to be approved and all non-cancelled actions to have owner/due date;
- closed incidents are read-only except action verification, recurrence status, and creation/approval of a corrective report version; hypotheses, evidence, and incident facts remain frozen;
- approving a corrective version on a closed incident atomically moves `approved_report_version` while leaving the incident closed and retaining every prior version/approval;
- there is no reopen transition in Stage 1; recurrence creates a new linked incident in a later stage.

```python
def test_transition_matrix_is_closed_and_explicit() -> None:
    assert legal_targets(IncidentStatus.OPEN) == {IncidentStatus.INVESTIGATING}
    assert legal_targets(IncidentStatus.INVESTIGATING) == {IncidentStatus.PENDING_REVIEW}
    assert legal_targets(IncidentStatus.PENDING_REVIEW) == {
        IncidentStatus.INVESTIGATING,
        IncidentStatus.CLOSED,
    }
    assert legal_targets(IncidentStatus.CLOSED) == set()


async def test_report_author_cannot_approve_own_version(service) -> None:
    with pytest.raises(AppError) as exc:
        await service.approve_report_version(INCIDENT_ID, 1, "同意")
    assert exc.value.code == "self_approval_forbidden"
```

- [ ] **Step 4: Run the action/report/lifecycle tests to verify they fail**

```bash
uv run --frozen --extra dev pytest \
  tests/unit/quality_incidents/test_actions.py \
  tests/unit/quality_incidents/test_reports.py \
  tests/unit/quality_incidents/test_closure.py -q
```

Expected: FAIL because action commands and report lifecycle methods are missing.

- [ ] **Step 5: Implement actions and report state machine**

Add the exact action types:

```python
class ActionStatus(StrEnum):
    OPEN = "open"
    VERIFIED = "verified"
    INEFFECTIVE = "ineffective"
    CANCELLED = "cancelled"

class RecurrenceStatus(StrEnum):
    UNKNOWN = "unknown"
    NOT_RECURRED = "not_recurred"
    RECURRED = "recurred"

@dataclass(frozen=True)
class CreateActionCommand:
    description: str
    owner_user_id: UUID
    due_at: datetime

@dataclass(frozen=True)
class UpdateActionCommand:
    status: ActionStatus
    verification_result: str | None
    recurrence_status: RecurrenceStatus
    lock_version: int
```

Keep transition rules in one pure helper so they can be exhaustively tested:

```python
LEGAL_TRANSITIONS = {
    IncidentStatus.OPEN: {IncidentStatus.INVESTIGATING},
    IncidentStatus.INVESTIGATING: {IncidentStatus.PENDING_REVIEW},
    IncidentStatus.PENDING_REVIEW: {
        IncidentStatus.INVESTIGATING,
        IncidentStatus.CLOSED,
    },
    IncidentStatus.CLOSED: set(),
}


def legal_targets(status: IncidentStatus) -> set[IncidentStatus]:
    return set(LEGAL_TRANSITIONS[status])
```

Implement the service surface with these typed signatures:

| Method | Return |
|---|---|
| `add_action(incident_id: UUID, command: CreateActionCommand)` | `ActionRef` |
| `update_action(incident_id: UUID, action_id: UUID, command: UpdateActionCommand)` | `ActionRef` |
| `create_report_version(incident_id: UUID, content: ReportContent)` | `ReportVersionRef` |
| `submit_for_review(incident_id: UUID)` | `IncidentDetail` |
| `request_changes(incident_id: UUID, comment: str)` | `IncidentDetail` |
| `approve_report_version(incident_id: UUID, version_number: int, comment: str)` | `ReportApprovalRef` |
| `close_incident(incident_id: UUID)` | `IncidentDetail` |

Each method locks the incident row, validates preconditions, inserts append-only history, updates only the stable incident pointer/status or action row, and records a specific audit action in the same transaction.

- [ ] **Step 6: Run focused and aggregate domain tests**

```bash
uv run --frozen --extra dev pytest \
  tests/unit/quality_incidents/test_actions.py \
  tests/unit/quality_incidents/test_reports.py \
  tests/unit/quality_incidents/test_closure.py -q
uv run --frozen --extra dev pytest tests/unit/quality_incidents -q
```

Expected: PASS.

- [ ] **Step 7: Commit the closed-loop domain**

```bash
git add packages/quality_incidents tests/unit/quality_incidents
git commit -m "feat: close quality incident investigation loop"
```

---

### Task 7: Expose the API with explicit RBAC and composition wiring

**Files:**
- Create: `apps/api/routers/quality_incidents.py`
- Create: `apps/api/composition/quality_incidents.py`
- Modify: `apps/api/composition/__init__.py`
- Modify: `apps/api/main.py`
- Modify: `packages/common/feature_flags.py`
- Modify: `apps/api/routers/auth.py`
- Create: `tests/unit/api/__init__.py`
- Create: `tests/unit/api/test_quality_incidents_router.py`
- Create: `tests/unit/api/test_quality_incident_feature_flag.py`

**Interfaces:**
- Consumes: quality-incident service/gateway, current user, role permissions, root-department RLS override, and module feature flag.
- Produces: `/api/v1/quality-incidents` JSON endpoints and a `/me.feature_flags.quality_incident_module` capability.

- [ ] **Step 1: Write failing route-contract tests**

Pin the endpoints and permissions:

```text
POST   /quality-incidents                                      write
GET    /quality-incidents                                      read
GET    /quality-incidents/{incident_id}                        read
POST   /quality-incidents/{incident_id}/snapshots              write
GET    /quality-incidents/{incident_id}/investigation          read
POST   /quality-incidents/{incident_id}/hypotheses             write
POST   /quality-incidents/{incident_id}/hypotheses/{id}/reviews review
POST   /quality-incidents/{incident_id}/actions                write
PATCH  /quality-incidents/{incident_id}/actions/{id}           write
POST   /quality-incidents/{incident_id}/reports/versions       write
POST   /quality-incidents/{incident_id}/submit                 write
POST   /quality-incidents/{incident_id}/request-changes        approve
POST   /quality-incidents/{incident_id}/reports/{version}/approve approve
POST   /quality-incidents/{incident_id}/close                  approve
GET    /quality-incidents/cases                                read
```

Assert a viewer receives 403 for mutation, a member receives 403 for approval, and a director reaches the approval service. Assert Pydantic rejects unknown status/decision values.

```python
def test_router_exposes_closed_loop_contract() -> None:
    routes = {
        (method, route.path)
        for route in quality_incidents_router.routes
        for method in route.methods or set()
    }
    assert ("POST", "/api/v1/quality-incidents") in routes
    assert ("POST", "/api/v1/quality-incidents/{incident_id}/close") in routes
    assert ("GET", "/api/v1/quality-incidents/cases") in routes


def test_member_cannot_approve(client_as_lab_member) -> None:
    response = client_as_lab_member.post(
        f"/api/v1/quality-incidents/{INCIDENT_ID}/reports/1/approve",
        json={"comment": "同意"},
    )
    assert response.status_code == 403
```

- [ ] **Step 2: Run the route tests to verify they fail**

```bash
uv run --frozen --extra dev pytest tests/unit/api/test_quality_incidents_router.py -q
```

Expected: FAIL because the router is missing.

- [ ] **Step 3: Implement request/response models and route adapters**

Use UUID fields in request models and return ISO datetimes. Convert dataclasses in private `_to_response` functions. Keep routing functions thin: validate HTTP shape, call one service method, map result.

Place `/cases` before `/{incident_id}` so the literal path cannot be parsed as a UUID. `GET /cases` is a closed-incident query with filters for metric, line, confirmed hypothesis text, action status, and occurrence range; it is not a second storage model.

Use explicit permission dependencies and thin adapters:

```python
ReadUserDep = Annotated[CurrentUser, Depends(require_permission("quality_incident:read"))]
WriteUserDep = Annotated[CurrentUser, Depends(require_permission("quality_incident:write"))]
ReviewUserDep = Annotated[CurrentUser, Depends(require_permission("quality_incident:review"))]
ApproveUserDep = Annotated[CurrentUser, Depends(require_permission("quality_incident:approve"))]

quality_incidents_router = APIRouter(
    prefix="/api/v1/quality-incidents",
    tags=["quality-incidents"],
)


@quality_incidents_router.post("", response_model=IncidentResponse, status_code=201)
async def create_incident(
    body: CreateIncidentBody,
    _user: WriteUserDep,
    service: QualityIncidentServiceDep,
) -> IncidentResponse:
    return _incident_to_response(await service.create_incident(body.to_command()))


@quality_incidents_router.get("/cases", response_model=CaseListResponse)
async def list_cases(
    user: ReadUserDep,
    service: QualityIncidentServiceDep,
    metric_code: str | None = None,
    line_code: str | None = None,
) -> CaseListResponse:
    return _cases_to_response(await service.list_cases(metric_code, line_code))
```

- [ ] **Step 4: Wire per-request services and the upstream evidence adapter**

The composition provider must:

- resolve the real business `department_id` with `lookup_dept_id`;
- compute `rls_dept_id = get_rls_dept_id(current_user, ctx.root_dept_id)`;
- construct `ResearchSnapshotEvidenceGateway` and `QualityIncidentService` with the same session factory;
- assign `_rls_dept_id` only for the database GUC override;
- never reuse a mutable service instance between requests.

```python
async def _get_quality_incident_service_dep(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
) -> QualityIncidentService:
    department_id = await lookup_dept_id(ctx.session_factory, current_user.user_id)
    gateway = ResearchSnapshotEvidenceGateway(ctx.session_factory, department_id)
    service = QualityIncidentService(
        session_factory=ctx.session_factory,
        department_id=department_id,
        actor_id=current_user.user_id,
        upstream_evidence=gateway,
    )
    rls_dept_id = get_rls_dept_id(current_user, ctx.root_dept_id)
    if rls_dept_id is not None:
        service._rls_dept_id = rls_dept_id
        gateway._rls_dept_id = rls_dept_id
    return service
```

- [ ] **Step 5: Add the rollout flag**

In `packages/common/feature_flags.py`:

```python
QUALITY_INCIDENT_MODULE_ENABLED = (
    os.getenv("QUALITY_INCIDENT_MODULE_ENABLED", "false").lower() == "true"
)
```

Register the router and composition provider only when enabled. Add `quality_incident_module` to `/me.feature_flags`. Test both enabled and disabled states by reloading/patching the flag at the same boundary used by current research flag tests.

```python
def test_quality_incident_feature_flag_defaults_false(monkeypatch) -> None:
    monkeypatch.delenv("QUALITY_INCIDENT_MODULE_ENABLED", raising=False)
    importlib.reload(feature_flags)
    assert feature_flags.QUALITY_INCIDENT_MODULE_ENABLED is False


def test_me_exposes_quality_incident_feature_flag(monkeypatch, me_client) -> None:
    monkeypatch.setenv("QUALITY_INCIDENT_MODULE_ENABLED", "true")
    importlib.reload(feature_flags)
    response = me_client.get("/api/v1/me")
    assert response.json()["feature_flags"]["quality_incident_module"] is True
```

- [ ] **Step 6: Run API tests and static checks**

```bash
QUALITY_INCIDENT_MODULE_ENABLED=true uv run --frozen --extra dev pytest \
  tests/unit/api/test_quality_incidents_router.py \
  tests/unit/api/test_quality_incident_feature_flag.py -q
uv run --frozen --extra dev ruff check apps/api packages/quality_incidents
uv run --frozen --extra dev mypy apps/api packages/quality_incidents
```

Expected: PASS.

- [ ] **Step 7: Commit the HTTP boundary**

```bash
git add apps/api packages/common/feature_flags.py tests/unit/api
git commit -m "feat: expose quality incident API"
```

---

### Task 8: Generate a deterministic, traceable Word report

**Files:**
- Create: `packages/quality_incidents/reporting.py`
- Modify: `apps/api/routers/quality_incidents.py`
- Create: `tests/unit/quality_incidents/test_reporting.py`
- Modify: `tests/unit/api/test_quality_incidents_router.py`

**Interfaces:**
- Consumes: one immutable `ReportVersionRef`, its incident header, hypotheses/reviews/actions, and evidence index.
- Produces: a `.docx` byte stream for a specific immutable report version.

- [ ] **Step 1: Write the failing renderer test**

Render a fixed fixture twice, open both byte streams with `python-docx`, and assert the document contains:

```text
质量事件基本信息
异常时间线
候选原因与支持/反对证据
专家研判记录
最终结论与局限
整改措施与责任人
证据索引与内容哈希
报告版本、创建人、审批记录
```

Normalize ZIP metadata before comparing the semantic package hash; do not expect raw DOCX bytes to match because ZIP timestamps can differ.

```python
def test_docx_contains_required_sections_and_evidence_hashes() -> None:
    first = QualityIncidentDocxRenderer().render(REPORT_BUNDLE)
    second = QualityIncidentDocxRenderer().render(REPORT_BUNDLE)
    document = Document(BytesIO(first))
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    for heading in REQUIRED_HEADINGS:
        assert heading in text
    assert REPORT_BUNDLE.snapshot.content_hash in text
    assert REPORT_BUNDLE.version.content_hash in text
    assert semantic_docx_hash(first) == semantic_docx_hash(second)
```

- [ ] **Step 2: Run the renderer test to verify it fails**

```bash
uv run --frozen --extra dev pytest tests/unit/quality_incidents/test_reporting.py -q
```

Expected: FAIL because the renderer is missing.

- [ ] **Step 3: Implement a pure renderer**

`QualityIncidentDocxRenderer.render(report_bundle) -> bytes` must not open a DB session or call AI. Use the immutable report content as authority, append citations such as `[E-001]`, and include the upstream and incident snapshot hashes in the evidence index. Render “统计线索，需专家确认” beside non-manual hypotheses.

```python
@dataclass(frozen=True)
class ReportBundle:
    incident: IncidentDetail
    snapshot: IncidentSnapshotRef
    hypotheses: list[HypothesisRef]
    reviews: list[ExpertReviewRef]
    actions: list[ActionRef]
    version: ReportVersionRef
    approval: ReportApprovalRef | None


class QualityIncidentDocxRenderer:
    def render(self, bundle: ReportBundle) -> bytes:
        document = Document()
        document.add_heading("质量异常调查报告", level=0)
        self._add_incident(document, bundle)
        self._add_timeline(document, bundle)
        self._add_hypotheses(document, bundle)
        self._add_reviews(document, bundle)
        self._add_conclusion(document, bundle)
        self._add_actions(document, bundle)
        self._add_evidence_index(document, bundle)
        self._add_version_and_approval(document, bundle)
        output = BytesIO()
        document.save(output)
        return output.getvalue()
```

Add the version-specific read without changing current/approved pointers:

```python
async def get_report_bundle(
    self,
    incident_id: UUID,
    version_number: int,
) -> ReportBundle:
    async with self._scoped_session() as session:
        incident = await self._require_incident(session, incident_id)
        version = await self._repository.get_report_version(
            session, incident_id, self._dept_id, version_number
        )
        if version is None:
            raise AppError(code="not_found", message="报告版本不存在", retryable=False, fields={})
        snapshot = await self._repository.get_snapshot(
            session, version.snapshot_id, self._dept_id
        )
        hypotheses = await self._repository.list_hypotheses(session, incident_id, self._dept_id)
        reviews = await self._repository.list_reviews(session, incident_id, self._dept_id)
        actions = await self._repository.list_actions(session, incident_id, self._dept_id)
        approval = await self._repository.get_approval_for_report_version(
            session, version.id, self._dept_id
        )
        return self._report_bundle(incident, snapshot, hypotheses, reviews, actions, version, approval)
```

- [ ] **Step 4: Add version-specific export endpoint**

Add:

```text
GET /quality-incidents/{incident_id}/reports/{version}/export.docx  read
```

Return the requested version, not merely the current version, with media type `application/vnd.openxmlformats-officedocument.wordprocessingml.document` and a safe ASCII fallback filename plus UTF-8 `filename*`.

```python
@quality_incidents_router.get("/{incident_id}/reports/{version}/export.docx")
async def export_report(
    incident_id: UUID,
    version: int,
    _user: ReadUserDep,
    service: QualityIncidentServiceDep,
) -> Response:
    bundle = await service.get_report_bundle(incident_id, version)
    content = QualityIncidentDocxRenderer().render(bundle)
    filename = quote(f"质量异常调查报告-v{version}.docx")
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": (
                f"attachment; filename=incident-report-v{version}.docx; "
                f"filename*=UTF-8''{filename}"
            )
        },
    )
```

- [ ] **Step 5: Run report and API tests**

```bash
uv run --frozen --extra dev pytest \
  tests/unit/quality_incidents/test_reporting.py \
  tests/unit/api/test_quality_incidents_router.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit report export**

```bash
git add packages/quality_incidents/reporting.py apps/api/routers/quality_incidents.py \
  tests/unit/quality_incidents/test_reporting.py tests/unit/api/test_quality_incidents_router.py
git commit -m "feat: export traceable quality incident reports"
```

---

### Task 9: Add the typed web client and query hooks

**Files:**
- Create: `apps/web/src/api/quality-incidents.ts`
- Create: `apps/web/src/features/quality-incidents/queries.ts`
- Modify: `apps/web/src/api/client.ts`
- Create: `apps/web/src/test/quality-incidents-api.test.ts`

**Interfaces:**
- Consumes: Stage 1 JSON endpoints and `/me.feature_flags.quality_incident_module`.
- Produces: TypeScript domain types, API functions, stable query keys, and mutations with targeted cache invalidation.

- [ ] **Step 1: Write failing API-client tests**

Mock the shared Axios client and assert exact method/path/body for create, list, detail, snapshot, hypothesis, review, action, report, approve, close, cases, and DOCX export. Pin `IncidentStatus` as a string union rather than `string`.

```typescript
export type IncidentStatus = 'open' | 'investigating' | 'pending_review' | 'closed';

it('creates an incident at the versioned endpoint', async () => {
  vi.mocked(http.post).mockResolvedValueOnce({ data: incidentFixture });
  await createQualityIncident(createFixture);
  expect(http.post).toHaveBeenCalledWith('/quality-incidents', createFixture);
});

it('exports a selected immutable report version as a blob', async () => {
  vi.mocked(http.get).mockResolvedValueOnce({ data: new Blob() });
  await exportQualityIncidentReport('incident-1', 2);
  expect(http.get).toHaveBeenCalledWith(
    '/quality-incidents/incident-1/reports/2/export.docx',
    { responseType: 'blob' },
  );
});
```

- [ ] **Step 2: Run the API-client test to verify it fails**

```bash
pnpm --dir apps/web test --run src/test/quality-incidents-api.test.ts
```

Expected: FAIL because the client module is missing.

- [ ] **Step 3: Implement API functions and feature flag typing**

Use the shared `http` instance and omit the `/api/v1` prefix because it is already the base URL. Request DOCX with `responseType: 'blob'`. Extend `CurrentUser.featureFlags` and `MeApiResponse.feature_flags` with the quality-incident flag mapping.

```typescript
export type Incident = {
  incident_id: string;
  title: string;
  line_code: string;
  metric_name: string;
  occurred_at: string;
  severity: 'low' | 'medium' | 'high' | 'critical';
  status: IncidentStatus;
  current_snapshot_id: string | null;
  current_report_version: number;
  approved_report_version: number | null;
};

export type IncidentFilters = {
  status?: IncidentStatus;
  line_code?: string;
  metric_code?: string;
  occurred_from?: string;
  occurred_to?: string;
};

export type CaseFilters = IncidentFilters & {
  conclusion_text?: string;
  action_status?: 'open' | 'verified' | 'ineffective' | 'cancelled';
  recurrence_status?: 'unknown' | 'not_recurred' | 'recurred';
};

export type IncidentDetail = Incident & {
  owner_user_id: string;
  metric_code: string;
  unit: string;
  observed_value: number;
  lower_limit: number | null;
  upper_limit: number | null;
  window_start: string;
  window_end: string;
  lock_version: number;
};

export type EvidenceCitation = {
  snapshot_id: string;
  source_namespace: string;
  source_id: string;
  field_name: string;
  observed_at: string | null;
  note: string;
};

export type IncidentSnapshot = {
  snapshot_id: string;
  incident_id: string;
  snapshot_number: number;
  content_hash: string;
  upstream_snapshot_id: string;
  upstream_content_hash: string;
  alignment_status: 'ready' | 'degraded' | 'blocked';
  alignment_notes: string;
  data_quality: Record<string, unknown>;
  captured_at: string;
};

export type Hypothesis = {
  hypothesis_id: string;
  snapshot_id: string;
  rank: number;
  statement: string;
  rationale: string;
  confidence_level: 'low' | 'medium' | 'high';
  origin: 'manual' | 'analysis' | 'ai';
  support_evidence: EvidenceCitation[];
  oppose_evidence: EvidenceCitation[];
  opposing_evidence_notes: string;
  status: 'active' | 'withdrawn';
};

export type ExpertReview = {
  review_id: string;
  hypothesis_id: string;
  decision: 'support' | 'oppose' | 'needs_more_evidence';
  discipline: 'quality' | 'process' | 'equipment' | 'management';
  comment: string;
  evidence_refs: EvidenceCitation[];
  created_at: string;
  created_by: string;
};

export type InvestigationView = {
  snapshot: IncidentSnapshot;
  hypotheses: Hypothesis[];
  reviews: ExpertReview[];
};

export type Action = {
  action_id: string;
  incident_id: string;
  description: string;
  owner_user_id: string;
  due_at: string;
  status: 'open' | 'verified' | 'ineffective' | 'cancelled';
  verification_result: string | null;
  recurrence_status: 'unknown' | 'not_recurred' | 'recurred';
  lock_version: number;
};

export type ReportContent = {
  summary: string;
  timeline: Array<Record<string, unknown>>;
  key_findings: Array<{ text: string; evidence: EvidenceCitation[] }>;
  conclusion_status: 'confirmed' | 'unresolved';
  confirmed_hypothesis_ids: string[];
  limitations: string[];
  action_ids: string[];
};

export type ReportVersion = {
  report_version_id: string;
  incident_id: string;
  snapshot_id: string;
  version_number: number;
  content: ReportContent;
  content_hash: string;
  created_at: string;
  created_by: string;
};

export type QualityIncidentCase = {
  incident_id: string;
  title: string;
  line_code: string;
  metric_name: string;
  occurred_at: string;
  status: 'closed';
  approved_report_version: number;
  approved_report_hash: string;
  conclusion_status: 'confirmed' | 'unresolved';
  conclusion_text: string;
  actions: Action[];
};

export async function createQualityIncident(body: CreateIncidentBody): Promise<Incident> {
  const response = await http.post<Incident>('/quality-incidents', body);
  return response.data;
}

export async function exportQualityIncidentReport(
  incidentId: string,
  version: number,
): Promise<Blob> {
  const response = await http.get<Blob>(
    `/quality-incidents/${incidentId}/reports/${version}/export.docx`,
    { responseType: 'blob' },
  );
  return response.data;
}
```

Complete the endpoint functions listed in Task 7 with response types mirroring its Pydantic models; each function performs one `http` call and returns `response.data`, exactly like `createQualityIncident` and `exportQualityIncidentReport` above. In `client.ts`, map `feature_flags.quality_incident_module` to `featureFlags.qualityIncidentModule`.

- [ ] **Step 4: Implement query keys and invalidation**

Use:

```typescript
export const qualityIncidentKeys = {
  all: ['quality-incidents'] as const,
  lists: () => [...qualityIncidentKeys.all, 'list'] as const,
  list: (filters: IncidentFilters) => [...qualityIncidentKeys.lists(), filters] as const,
  detail: (id: string) => [...qualityIncidentKeys.all, 'detail', id] as const,
  investigation: (id: string) => [...qualityIncidentKeys.all, 'investigation', id] as const,
  cases: (filters: CaseFilters) => [...qualityIncidentKeys.all, 'cases', filters] as const,
};
```

Invalidate only the mutated incident detail/investigation plus affected list/case prefixes.

- [ ] **Step 5: Run focused tests and TypeScript build**

```bash
pnpm --dir apps/web test --run src/test/quality-incidents-api.test.ts
pnpm --dir apps/web build
```

Expected: both commands PASS. An existing global Vitest dependency issue is not grounds to waive this focused test; fix or isolate the dependency before completing the task.

- [ ] **Step 6: Commit web data boundary**

```bash
git add apps/web/src/api/client.ts apps/web/src/api/quality-incidents.ts \
  apps/web/src/features/quality-incidents/queries.ts \
  apps/web/src/test/quality-incidents-api.test.ts
git commit -m "feat(web): add quality incident client"
```

---

### Task 10: Build the quality event center and incident creation flow

**Files:**
- Create: `apps/web/src/features/quality-incidents/QualityIncidentPage.tsx`
- Create: `apps/web/src/features/quality-incidents/IncidentCenter.tsx`
- Create: `apps/web/src/features/quality-incidents/CreateIncidentModal.tsx`
- Create: `apps/web/src/features/quality-incidents/IncidentStatusBoard.tsx`
- Create: `apps/web/src/features/quality-incidents/IncidentFilters.tsx`
- Create: `apps/web/src/features/quality-incidents/IncidentTable.tsx`
- Create: `apps/web/src/features/quality-incidents/status.ts`
- Modify: `apps/web/src/app/router.tsx`
- Modify: `apps/web/src/app/AppShell.tsx`
- Create: `apps/web/src/test/quality-incident-center.test.tsx`

**Interfaces:**
- Consumes: feature flag, incident list/create APIs, and existing `PageHeaderContext`/Tideline layout.
- Produces: a discoverable `/quality-incidents` route with the customer states 待调查、调查中、待确认、已关闭.

- [ ] **Step 1: Write the failing event-center component test**

Assert:

- the nav is absent and a direct route redirects to `/workbench` when the module flag is false;
- the module is visible when true and the user has read permission;
- four status counters use the exact Chinese labels;
- filter changes call list with status/line/metric/date;
- create modal validates time-window order and requires metric, line, occurrence time, observed value, and unit;
- a successful create navigates to the new incident detail.

```typescript
function renderCenter(): void {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <IncidentCenter />
    </QueryClientProvider>,
  );
}

async function fillRequiredIncidentFields(times: {
  windowStart: string;
  windowEnd: string;
}): Promise<void> {
  await userEvent.type(screen.getByLabelText('事件标题'), '熟料游离钙超限');
  await userEvent.type(screen.getByLabelText('生产线'), '1#窑');
  await userEvent.type(screen.getByLabelText('指标编码'), 'f-CaO');
  await userEvent.type(screen.getByLabelText('指标名称'), '游离钙');
  await userEvent.type(screen.getByLabelText('实测值'), '2.1');
  await userEvent.type(screen.getByLabelText('单位'), '%');
  await userEvent.type(screen.getByLabelText('异常发生时间'), '2026-08-08 10:30');
  await userEvent.type(screen.getByLabelText('调查开始时间'), times.windowStart);
  await userEvent.type(screen.getByLabelText('调查结束时间'), times.windowEnd);
}

it('renders the four contractual incident states', async () => {
  vi.mocked(listQualityIncidents).mockResolvedValue({ items: [], next_cursor: null });
  renderCenter();
  for (const label of ['待调查', '调查中', '待确认', '已关闭']) {
    expect(await screen.findByText(label)).toBeInTheDocument();
  }
});

it('rejects an evidence window ending before it starts', async () => {
  render(<CreateIncidentModal open onCancel={vi.fn()} onCreated={vi.fn()} />);
  await fillRequiredIncidentFields({ windowStart: '2026-08-08 11:00', windowEnd: '2026-08-08 10:00' });
  await userEvent.click(screen.getByRole('button', { name: '创建事件' }));
  expect(await screen.findByText('调查结束时间必须晚于开始时间')).toBeInTheDocument();
});
```

- [ ] **Step 2: Run the event-center test to verify it fails**

```bash
pnpm --dir apps/web test --run src/test/quality-incident-center.test.tsx
```

Expected: FAIL because the components and route do not exist.

- [ ] **Step 3: Implement the product route and conditional nav entry**

Lazy-load `QualityIncidentPage`. Add `/quality-incidents` and `/quality-incidents/$incidentId`. Insert nav label `质量事件室`, English label `Incident`, and watermark `Quality Incident`. Derive nav items inside `AppShell` from `user.featureFlags.qualityIncidentModule`; do not mutate module-level arrays after import.

```typescript
import { lazy, Suspense, useEffect } from 'react';

const QualityIncidentPage = lazy(() =>
  import('@/features/quality-incidents/QualityIncidentPage').then((module) => ({
    default: module.QualityIncidentPage,
  })),
);

function QualityIncidentGate(): JSX.Element | null {
  const user = useAuthStore((state) => state.user);
  const navigate = useNavigate();
  const enabled = user?.featureFlags?.qualityIncidentModule === true
    && user.permissions.includes('quality_incident:read');
  useEffect(() => {
    if (!enabled) void navigate({ to: '/workbench', replace: true });
  }, [enabled, navigate]);
  return enabled ? <LazyPage><QualityIncidentPage /></LazyPage> : null;
}

const qualityIncidentsRoute = createRoute({
  getParentRoute: () => protectedLayoutRoute,
  path: '/quality-incidents',
  component: QualityIncidentGate,
});

const qualityIncidentDetailRoute = createRoute({
  getParentRoute: () => protectedLayoutRoute,
  path: '/quality-incidents/$incidentId',
  component: QualityIncidentGate,
});
```

Inside `AppShell`, derive `navMeta` with `useMemo`; append `{ key: '/quality-incidents', label: '质量事件室', num: '06', en: 'Incident' }` only when `user.featureFlags?.qualityIncidentModule === true` and `user.permissions.includes('quality_incident:read')`.

- [ ] **Step 4: Implement the center and creation modal**

Use a compact status board plus table. Show severity, metric, line, occurrence time, owner, status, and elapsed investigation time. Do not show algorithmic confidence on the event list. Keep empty/loading/error states explicit.

```typescript
export const INCIDENT_STATUS_LABEL: Record<IncidentStatus, string> = {
  open: '待调查',
  investigating: '调查中',
  pending_review: '待确认',
  closed: '已关闭',
};

export function IncidentCenter(): JSX.Element {
  const [filters, setFilters] = useState<IncidentFilters>({});
  const query = useQualityIncidentList(filters);
  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <IncidentStatusBoard items={query.data?.items ?? []} onSelectStatus={(status) => setFilters({ ...filters, status })} />
      <IncidentFilters value={filters} onChange={setFilters} />
      <IncidentTable loading={query.isLoading} error={query.error} items={query.data?.items ?? []} />
    </Space>
  );
}
```

- [ ] **Step 5: Run focused test and build**

```bash
pnpm --dir apps/web test --run src/test/quality-incident-center.test.tsx
pnpm --dir apps/web build
```

Expected: PASS.

- [ ] **Step 6: Commit event center**

```bash
git add apps/web/src/features/quality-incidents apps/web/src/app/router.tsx \
  apps/web/src/app/AppShell.tsx apps/web/src/test/quality-incident-center.test.tsx
git commit -m "feat(web): add quality event center"
```

---

### Task 11: Build the investigation, expert-review, actions, and report workspace

**Files:**
- Create: `apps/web/src/features/quality-incidents/QualityIncidentDetail.tsx`
- Create: `apps/web/src/features/quality-incidents/IncidentOverview.tsx`
- Create: `apps/web/src/features/quality-incidents/EvidenceSnapshotPanel.tsx`
- Create: `apps/web/src/features/quality-incidents/HypothesisPanel.tsx`
- Create: `apps/web/src/features/quality-incidents/ExpertReviewPanel.tsx`
- Create: `apps/web/src/features/quality-incidents/ActionPanel.tsx`
- Create: `apps/web/src/features/quality-incidents/IncidentReportPanel.tsx`
- Create: `apps/web/src/test/quality-incident-detail.test.tsx`
- Create: `apps/web/src/test/quality-incident-report.test.tsx`

**Interfaces:**
- Consumes: incident detail/investigation APIs and all Stage 1 mutations.
- Produces: one incident screen containing the commercial investigation, expert review, measures, and report modules.

- [ ] **Step 1: Write the failing detail-workspace test**

Test the state-aware UI:

- `open`: evidence snapshot binding form is enabled; later workflow is locked with an explanation;
- `investigating`: evidence header, support/opposition citations, expert reviews, and actions are available;
- `pending_review`: edit controls are disabled for members; directors see request-change/approve controls;
- `closed`: report and case data are read-only while action verification remains available;
- `blocked` alignment renders a destructive warning and never offers submit/approve.

```typescript
let testPermissions: string[] = [];

vi.mock('@/features/auth/AuthProvider', () => ({
  useAuthStore: (selector: (state: { user: { permissions: string[] } }) => unknown) => selector({
    user: { permissions: testPermissions },
  }),
}));

function mockIncidentDetail(overrides: Partial<IncidentDetail>): void {
  vi.mocked(getQualityIncident).mockResolvedValue({ ...incidentDetailFixture, ...overrides });
}

function mockInvestigation(overrides: Partial<InvestigationView>): void {
  vi.mocked(getQualityIncidentInvestigation).mockResolvedValue({
    ...investigationFixture,
    ...overrides,
  });
}

function renderDetail(options: { permissions: string[] }): void {
  testPermissions = options.permissions;
  renderWithQueryClient(
    <QualityIncidentDetail incidentId={INCIDENT_ID} section="investigation" />,
  );
}

function renderReport(options: { permissions: string[] }): void {
  testPermissions = options.permissions;
  renderWithQueryClient(
    <QualityIncidentDetail incidentId={INCIDENT_ID} section="report" />,
  );
}

it.each([
  ['open', '绑定已冻结证据'],
  ['investigating', '新增原因假设'],
  ['pending_review', '待负责人确认'],
  ['closed', '已关闭'],
] as const)('renders controls for %s state', async (status, expectedText) => {
  mockIncidentDetail({ status });
  renderDetail({ permissions: ['quality_incident:read', 'quality_incident:write'] });
  expect(await screen.findByText(expectedText)).toBeInTheDocument();
});

it('shows both supporting and opposing expert reviews', async () => {
  mockInvestigation({ reviews: [supportReview, opposeReview] });
  renderDetail({ permissions: ['quality_incident:read', 'quality_incident:review'] });
  expect(await screen.findByText('支持')).toBeInTheDocument();
  expect(screen.getByText('反对')).toBeInTheDocument();
  expect(screen.getByText('统计线索，需专家确认')).toBeInTheDocument();
});
```

- [ ] **Step 2: Write the failing report-workflow test**

```typescript
it('never exposes approval controls to a lab member', async () => {
  mockIncidentDetail({ status: 'pending_review', current_report_version: 1 });
  renderReport({ permissions: ['quality_incident:read', 'quality_incident:write'] });
  expect(await screen.findByText('报告版本 v1')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '批准报告' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '关闭事件' })).not.toBeInTheDocument();
});

it('downloads the selected immutable version', async () => {
  mockIncidentDetail({ status: 'closed', approved_report_version: 1 });
  renderReport({ permissions: ['quality_incident:read'] });
  await userEvent.click(await screen.findByRole('button', { name: '下载 v1' }));
  expect(exportQualityIncidentReport).toHaveBeenCalledWith(INCIDENT_ID, 1);
});
```

- [ ] **Step 3: Run the detail/report tests to verify they fail**

```bash
pnpm --dir apps/web test --run \
  src/test/quality-incident-detail.test.tsx \
  src/test/quality-incident-report.test.tsx
```

Expected: FAIL because the detail panels and report controls do not exist.

- [ ] **Step 4: Implement evidence and hypothesis panels**

For Stage 1, the evidence form accepts an upstream IRIP snapshot ID plus alignment status/notes and data-quality declarations. Label this as “绑定已冻结证据” and never imply that live LIMS/DCS extraction is automatic yet.

Each hypothesis card must show rank, origin, confidence wording, supporting evidence, opposing evidence or opposing-search notes, and all contradictory expert reviews. Render `origin=analysis|ai` with the disclaimer “统计线索，需专家确认”.

```typescript
export function EvidenceSnapshotPanel({ incident }: { incident: IncidentDetail }): JSX.Element {
  const freeze = useFreezeIncidentSnapshot(incident.incident_id);
  if (incident.status !== 'open') return <FrozenEvidenceSummary incidentId={incident.incident_id} />;
  return (
    <Card title="绑定已冻结证据">
      <Alert type="info" showIcon message="本阶段绑定 IRIP 已冻结快照，不会直接读取或修改 DCS。" />
      <FreezeEvidenceForm loading={freeze.isPending} onFinish={(body) => freeze.mutate(body)} />
    </Card>
  );
}

export function HypothesisPanel({ view }: { view: InvestigationView }): JSX.Element {
  return <Space direction="vertical" style={{ width: '100%' }}>
    {view.hypotheses.map((hypothesis) => (
      <HypothesisCard
        key={hypothesis.hypothesis_id}
        hypothesis={hypothesis}
        reviews={view.reviews.filter((review) => review.hypothesis_id === hypothesis.hypothesis_id)}
        disclaimer={hypothesis.origin === 'manual' ? undefined : '统计线索，需专家确认'}
      />
    ))}
  </Space>;
}
```

- [ ] **Step 5: Implement review and action panels**

Require discipline and decision in reviews. Show every review chronologically. Action forms require owner and due date; verification requires result and recurrence state. Use optimistic-lock conflicts to prompt refresh, not overwrite.

```typescript
const submitReview = (values: ReviewFormValues): void => {
  addReview.mutate({
    decision: values.decision,
    discipline: values.discipline,
    comment: values.comment.trim(),
    evidence_refs: values.evidence_refs,
  });
};

const verifyAction = (action: Action, values: VerifyActionValues): void => {
  updateAction.mutate({
    actionId: action.action_id,
    body: {
      status: values.status,
      verification_result: values.verification_result.trim(),
      recurrence_status: values.recurrence_status,
      lock_version: action.lock_version,
    },
  });
};
```

On HTTP 409, show `事件已被其他人更新，请刷新后重试` and invalidate the detail query; do not retry the stale mutation.

- [ ] **Step 6: Implement report editor and lifecycle controls**

The editor must use structured sections matching `ReportContent`; do not store arbitrary HTML. Display citations beside key findings and a separate evidence index. On correction, seed the form from the selected version but submit a new version. Require an approval confirmation showing version number and hash prefix.

```typescript
const canApprove = permissions.includes('quality_incident:approve');
const reportActions = [
  incident.status === 'investigating' && <Button onClick={() => submit.mutate()}>提交确认</Button>,
  canApprove && incident.status === 'pending_review' && (
    <Button onClick={() => requestChanges.mutate('证据需补充')}>退回修改</Button>
  ),
  canApprove && incident.status === 'pending_review' && (
    <Popconfirm
      title={`批准报告 v${selectedVersion.version_number}（${selectedVersion.content_hash.slice(0, 12)}）？`}
      onConfirm={() => approve.mutate(selectedVersion.version_number)}
    >
      <Button type="primary">批准报告</Button>
    </Popconfirm>
  ),
  canApprove && incident.status === 'pending_review' && (
    <Button onClick={() => closeIncident.mutate()}>关闭事件</Button>
  ),
].filter(Boolean);
```

- [ ] **Step 7: Run focused UI tests and build**

```bash
pnpm --dir apps/web test --run \
  src/test/quality-incident-detail.test.tsx \
  src/test/quality-incident-report.test.tsx
pnpm --dir apps/web build
```

Expected: PASS.

- [ ] **Step 8: Commit investigation workspace**

```bash
git add apps/web/src/features/quality-incidents \
  apps/web/src/test/quality-incident-detail.test.tsx \
  apps/web/src/test/quality-incident-report.test.tsx
git commit -m "feat(web): add quality incident investigation workspace"
```

---

### Task 12: Add the historical case library without duplicating data

**Files:**
- Create: `apps/web/src/features/quality-incidents/CaseLibrary.tsx`
- Modify: `apps/web/src/features/quality-incidents/QualityIncidentPage.tsx`
- Create: `apps/web/src/test/quality-incident-cases.test.tsx`

**Interfaces:**
- Consumes: `GET /quality-incidents/cases` and immutable closed incidents.
- Produces: searchable history by metric, line, conclusion text, occurrence period, action result, and recurrence state.

- [ ] **Step 1: Write the failing case-library test**

Assert only closed incidents appear; filters are server-side; a result shows approved report version/hash, confirmed or explicitly unresolved conclusion, measures/effect, and recurrence status; clicking opens the immutable incident detail.

```typescript
it('shows only approved closed incidents with traceable report identity', async () => {
  vi.mocked(listQualityIncidentCases).mockResolvedValue({
    items: [closedCaseFixture],
    next_cursor: null,
  });
  renderWithQueryClient(<CaseLibrary />);
  expect(await screen.findByText(closedCaseFixture.title)).toBeInTheDocument();
  expect(screen.getByText('报告 v1')).toBeInTheDocument();
  expect(screen.getByText(closedCaseFixture.approved_report_hash.slice(0, 12))).toBeInTheDocument();
  expect(screen.getByText('未复发')).toBeInTheDocument();
});

it('sends case filters to the server', async () => {
  renderWithQueryClient(<CaseLibrary />);
  await userEvent.type(screen.getByLabelText('指标'), 'f-CaO');
  await userEvent.click(screen.getByRole('button', { name: '查询案例' }));
  await waitFor(() => expect(listQualityIncidentCases).toHaveBeenLastCalledWith(
    expect.objectContaining({ metric_code: 'f-CaO' }),
  ));
});
```

- [ ] **Step 2: Run the case-library test to verify it fails**

```bash
pnpm --dir apps/web test --run src/test/quality-incident-cases.test.tsx
```

Expected: FAIL because the case library is missing.

- [ ] **Step 3: Implement the fifth customer module**

Add internal page tabs/navigation for:

```text
质量事件中心 | 调查工作台 | 专家研判 | 调查报告 | 历史案例库
```

The first and fifth are collection views. The middle three deep-link into the selected incident and retain `incidentId` in the URL. Do not add separate platform-level menus for standards, models, components, or AI inside this product page.

```typescript
type QualitySection = 'center' | 'investigation' | 'review' | 'report' | 'cases';

function SelectIncidentPrompt(): JSX.Element {
  return <Empty description="请先从质量事件中心或历史案例库选择一个事件" />;
}

export function QualityIncidentPage(): JSX.Element {
  const params = useParams({ strict: false }) as { incidentId?: string };
  const search = useSearch({ strict: false }) as { section?: QualitySection };
  const navigate = useNavigate();
  const section = search.section ?? (params.incidentId ? 'investigation' : 'center');
  const items: TabsProps['items'] = [
    { key: 'center', label: '质量事件中心', children: <IncidentCenter /> },
    { key: 'investigation', label: '调查工作台', children: params.incidentId ? <QualityIncidentDetail incidentId={params.incidentId} section="investigation" /> : <SelectIncidentPrompt /> },
    { key: 'review', label: '专家研判', children: params.incidentId ? <QualityIncidentDetail incidentId={params.incidentId} section="review" /> : <SelectIncidentPrompt /> },
    { key: 'report', label: '调查报告', children: params.incidentId ? <QualityIncidentDetail incidentId={params.incidentId} section="report" /> : <SelectIncidentPrompt /> },
    { key: 'cases', label: '历史案例库', children: <CaseLibrary /> },
  ];
  return (
    <Tabs
      activeKey={section}
      items={items}
      onChange={(key) => void navigate({ search: { section: key as QualitySection } })}
    />
  );
}
```

When a collection row is selected, navigate to `/quality-incidents/$incidentId` and set the intended section through the route's search parameter `section=investigation|review|report`. Extend both quality routes' `validateSearch` to parse this value so refresh preserves the selected module.

- [ ] **Step 4: Run test and build**

```bash
pnpm --dir apps/web test --run src/test/quality-incident-cases.test.tsx
pnpm --dir apps/web build
```

Expected: PASS.

- [ ] **Step 5: Commit the case library**

```bash
git add apps/web/src/features/quality-incidents/CaseLibrary.tsx \
  apps/web/src/features/quality-incidents/QualityIncidentPage.tsx \
  apps/web/src/test/quality-incident-cases.test.tsx
git commit -m "feat(web): add quality incident case library"
```

---

### Task 13: Prove lifecycle, RLS isolation, immutability, and audit in PostgreSQL

**Files:**
- Create: `tests/quality_incident_harness.py`
- Create: `tests/integration/quality_incidents/__init__.py`
- Create: `tests/integration/quality_incidents/conftest.py`
- Create: `tests/integration/quality_incidents/test_incident_lifecycle.py`
- Create: `tests/integration/quality_incidents/test_incident_rls.py`
- Create: `tests/integration/quality_incidents/test_incident_immutability.py`
- Create: `tests/integration/quality_incidents/test_incident_audit.py`

**Interfaces:**
- Consumes: a migrated PostgreSQL database and the restricted `irip_app` database role.
- Produces: evidence that application checks and database enforcement agree.

`tests/quality_incident_harness.py` exports `seed_quality_scenario(session_factory) -> QualityScenario`, `seed_quality_e2e(session_factory) -> QualityScenario`, `complete_incident(member, director, upstream_snapshot_id) -> IncidentDetail`, `report_content(snapshot_id, hypothesis_id, action_id) -> ReportContent`, and `corrected_report_content(content) -> ReportContent`. It owns the deterministic commands used by integration, acceptance, and browser tests. `conftest.py` wraps `seed_quality_scenario` as the `quality_scenario` fixture. `seed_quality_e2e` uses fixed UUIDs and `ON CONFLICT DO NOTHING` so repeated browser runs are idempotent.

The harness seeds two departments, three users, one research workspace, and one research evidence snapshot and exposes:

```python
@dataclass(frozen=True)
class QualityScenario:
    department_a: UUID
    department_b: UUID
    member_a: UUID
    director_a: UUID
    member_b: UUID
    upstream_snapshot_a: UUID
    session_factory: async_sessionmaker[AsyncSession]

    def service(self, actor_id: UUID, department_id: UUID) -> QualityIncidentService:
        gateway = ResearchSnapshotEvidenceGateway(self.session_factory, department_id)
        return QualityIncidentService(self.session_factory, department_id, actor_id, gateway)
```

- [ ] **Step 1: Write the full lifecycle integration test**

Seed two users in one department, an upstream research snapshot, and permissions. Execute create → freeze → hypothesis → reviews → action → report v1 → submit → approve by different user → close. Re-read through a fresh session and assert every pointer, version, hash, audit action, and status.

```python
async def test_full_incident_lifecycle_persists_every_version(quality_scenario) -> None:
    member = quality_scenario.service(quality_scenario.member_a, quality_scenario.department_a)
    director = quality_scenario.service(quality_scenario.director_a, quality_scenario.department_a)
    incident = await member.create_incident(CREATE_INCIDENT)
    snapshot = await member.freeze_snapshot(
        incident.incident_id,
        FreezeSnapshotCommand(
            upstream_snapshot_id=quality_scenario.upstream_snapshot_a,
            alignment_status=AlignmentStatus.READY,
            alignment_notes="批次与 DCS 时间窗已由工艺专家确认",
            data_quality={"completeness_ratio": 0.98},
        ),
    )
    hypothesis = await member.add_hypothesis(incident.incident_id, HYPOTHESIS)
    await member.add_review(incident.incident_id, hypothesis.hypothesis_id, QUALITY_REVIEW)
    await member.add_review(incident.incident_id, hypothesis.hypothesis_id, PROCESS_REVIEW)
    action = await member.add_action(incident.incident_id, ACTION)
    report = await member.create_report_version(
        incident.incident_id,
        report_content(snapshot.snapshot_id, hypothesis.hypothesis_id, action.action_id),
    )
    await member.submit_for_review(incident.incident_id)
    approval = await director.approve_report_version(incident.incident_id, 1, "同意归档")
    closed = await director.close_incident(incident.incident_id)
    assert approval.report_version_id == report.report_version_id
    assert closed.status is IncidentStatus.CLOSED
    assert closed.approved_report_version == 1
```

- [ ] **Step 2: Write RLS tests with the restricted role**

Create incidents in departments A and B. Set A's GUC as `irip_app`; assert A cannot select, insert a child into, update, or approve B's incident. Do not run the core assertions as a superuser.

```sql
SET LOCAL ROLE irip_app;
SELECT set_config('app.current_dept_id', :department_a, true);
SELECT set_config('app.current_user_id', :member_a, true);
```

```python
async def test_rls_hides_other_department_incident(quality_scenario, sync_engine) -> None:
    incident_b = seed_incident(sync_engine, quality_scenario.department_b, quality_scenario.member_b)
    with sync_engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE irip_app"))
        connection.execute(sa.text("SELECT set_config('app.current_dept_id', :value, true)"), {"value": str(quality_scenario.department_a)})
        connection.execute(sa.text("SELECT set_config('app.current_user_id', :value, true)"), {"value": str(quality_scenario.member_a)})
        row = connection.execute(
            sa.text("SELECT id FROM quality_incident WHERE id = :id"),
            {"id": incident_b},
        ).first()
        assert row is None
```

- [ ] **Step 3: Write database immutability tests**

Attempt direct `UPDATE` and `DELETE` against snapshot, review, report-version, and approval rows. Assert PostgreSQL raises the immutable violation and the original values remain.

```python
@pytest.mark.parametrize(
    "table",
    [
        "quality_incident_snapshot",
        "quality_incident_review",
        "quality_incident_report_version",
        "quality_incident_report_approval",
    ],
)
def test_append_only_tables_reject_update(sync_engine, completed_incident, table) -> None:
    row_id = completed_incident.row_id(table)
    with pytest.raises(sa.exc.DBAPIError, match="immutable"):
        with sync_engine.begin() as connection:
            connection.execute(
                sa.text(f"UPDATE {table} SET department_id = department_id WHERE id = :id"),
                {"id": row_id},
            )
```

- [ ] **Step 4: Write failure-atomicity and audit tests**

Inject an audit recorder failure and verify the paired business mutation rolls back. Verify no snapshot is persisted for blocked alignment and no approval row is persisted for a self-approval attempt.

```python
class FailingAuditRecorder:
    async def record(self, session: AsyncSession, event: AuditEventData) -> None:
        raise RuntimeError("forced audit failure")


async def test_audit_failure_rolls_back_incident_create(quality_scenario) -> None:
    service = quality_scenario.service(quality_scenario.member_a, quality_scenario.department_a)
    service._audit = FailingAuditRecorder()
    with pytest.raises(RuntimeError, match="forced audit failure"):
        await service.create_incident(CREATE_INCIDENT)
    assert await count_incidents(quality_scenario.session_factory) == 0
```

- [ ] **Step 5: Run the integration slice**

```bash
IRIP_ENV=test QUALITY_INCIDENT_MODULE_ENABLED=true \
  uv run --frozen --extra dev pytest tests/integration/quality_incidents -q
```

Expected: PASS with PostgreSQL available. If Testcontainers cannot start, record the infrastructure blocker and rerun against `IRIP_TEST_DATABASE_URL`; do not downgrade the assertions or claim a pass.

- [ ] **Step 6: Commit integration proof**

```bash
git add tests/quality_incident_harness.py tests/integration/quality_incidents
git commit -m "test: prove quality incident database invariants"
```

---

### Task 14: Add acceptance/E2E proof, operator notes, and final quality gates

**Files:**
- Create: `tests/acceptance/test_quality_incident_mvp.py`
- Create: `tests/e2e/quality-incidents.spec.ts`
- Create: `scripts/seed_quality_incident_e2e.py`
- Create: `docs/user-guide/quality-incident-room.md`
- Modify: `docs/STAGE_DELIVERY.md`
- Modify: `.env.example`

**Interfaces:**
- Consumes: the complete Stage 1 implementation in a migrated local stack.
- Produces: user-visible proof, enablement instructions, known limitations, and a verified handoff to Stage 2.

- [ ] **Step 1: Write executable acceptance invariants**

The acceptance test must assert:

1. one historical event completes the full workflow;
2. every report key finding resolves to the frozen incident snapshot;
3. conflicting expert reviews remain visible after approval;
4. a correction creates a second report version and preserves version 1;
5. a cross-department user cannot access the event;
6. a lab member cannot approve;
7. blocked alignment cannot reach approved/closed;
8. closed incident appears in `/cases` with approved version/hash;
9. no operation writes to an upstream research snapshot or process-control object.

```python
async def test_quality_incident_mvp_invariants(async_session_factory) -> None:
    scenario = await seed_quality_scenario(async_session_factory)
    upstream_before = await scenario.upstream_snapshot_hash()
    member = scenario.service(scenario.member_a, scenario.department_a)
    director = scenario.service(scenario.director_a, scenario.department_a)
    closed = await complete_incident(member, director, scenario.upstream_snapshot_a)

    investigation = await member.get_investigation(closed.incident_id)
    assert {review.decision for review in investigation.reviews} >= {
        ReviewDecision.SUPPORT,
        ReviewDecision.OPPOSE,
    }
    version_one = await member.get_report_bundle(closed.incident_id, 1)
    for finding in version_one.version.content.key_findings:
        assert finding.evidence
        assert all(
            citation.snapshot_id == investigation.snapshot.snapshot_id
            for citation in finding.evidence
        )

    version_two = await member.create_report_version(
        closed.incident_id,
        corrected_report_content(version_one.version.content),
    )
    assert version_two.version_number == 2
    assert (await member.get_report_bundle(closed.incident_id, 1)).version.content_hash == version_one.version.content_hash
    assert await scenario.upstream_snapshot_hash() == upstream_before
```

- [ ] **Step 2: Run the acceptance test**

```bash
IRIP_ENV=test QUALITY_INCIDENT_MODULE_ENABLED=true \
  uv run --frozen --extra dev pytest tests/acceptance/test_quality_incident_mvp.py -q
```

Expected: PASS.

- [ ] **Step 3: Add one Playwright commercial journey**

Use stable `data-testid` selectors. Log in as member to create/freeze/investigate/draft/submit; log in as director to approve/close; reopen the case-library view and download report version 1. Assert the UI says “统计线索，需专家确认” for analysis/AI hypotheses.

```typescript
const MEMBER_EMAIL = process.env.E2E_QUALITY_MEMBER_EMAIL ?? 'quality-member@irip.local';
const MEMBER_PASSWORD = process.env.E2E_QUALITY_MEMBER_PASSWORD ?? 'Quality-Member-2026!';
const DIRECTOR_EMAIL = process.env.E2E_QUALITY_DIRECTOR_EMAIL ?? 'quality-director@irip.local';
const DIRECTOR_PASSWORD = process.env.E2E_QUALITY_DIRECTOR_PASSWORD ?? 'Quality-Director-2026!';
const UPSTREAM_SNAPSHOT_ID = '81818181-8181-4818-8818-818181818181';

async function login(page: Page, email: string, password: string): Promise<void> {
  await page.goto('/login');
  await page.getByLabel('邮箱').fill(email);
  await page.getByLabel('密码').fill(password);
  await page.getByRole('button', { name: /登\s*录/ }).click();
  await expect(page).toHaveURL(/\/quality-incidents|\/workbench/, { timeout: 15_000 });
}

async function fillIncidentForm(page: Page): Promise<void> {
  await page.getByLabel('事件标题').fill('1#窑熟料游离钙超限');
  await page.getByLabel('生产线').fill('1#窑');
  await page.getByLabel('指标编码').fill('f-CaO');
  await page.getByLabel('指标名称').fill('游离钙');
  await page.getByLabel('实测值').fill('2.1');
  await page.getByLabel('单位').fill('%');
  await page.getByLabel('异常发生时间').fill('2026-08-08 10:30');
  await page.getByLabel('调查开始时间').fill('2026-08-08 08:30');
  await page.getByLabel('调查结束时间').fill('2026-08-08 11:30');
}

async function addEvidenceBackedHypothesis(page: Page): Promise<void> {
  await page.getByRole('button', { name: '新增原因假设' }).click();
  await page.getByLabel('候选原因').fill('窑尾喂料波动可能相关');
  await page.getByLabel('判断依据').fill('波动发生在质量异常之前');
  await page.getByLabel('支持证据').selectOption('E-001');
  await page.getByLabel('反对证据检索说明').fill('检查复检与同工况批次，未发现直接反例');
  await page.getByRole('button', { name: '保存假设' }).click();
}

async function addExpertReview(page: Page, decision: '支持' | '反对'): Promise<void> {
  await page.getByRole('button', { name: '添加专家意见' }).click();
  await page.getByLabel('专业').selectOption('process');
  await page.getByLabel('研判').selectOption(decision === '支持' ? 'support' : 'oppose');
  await page.getByLabel('意见').fill('时间关系与现场记录一致');
  await page.getByRole('button', { name: '保存研判' }).click();
}

async function createActionAndReport(page: Page): Promise<void> {
  await page.getByRole('button', { name: '新增措施' }).click();
  await page.getByLabel('措施').fill('校验喂料秤并复核控制参数');
  await page.getByLabel('责任人').selectOption({ index: 1 });
  await page.getByLabel('完成期限').fill('2026-08-15 18:00');
  await page.getByRole('button', { name: '保存措施' }).click();
  await page.getByRole('tab', { name: '调查报告' }).click();
  await page.getByLabel('调查摘要').fill('本次异常已完成证据复盘');
  await page.getByRole('button', { name: '生成报告版本' }).click();
}

test('quality incident closes with a traceable immutable report', async ({ page }) => {
  await login(page, MEMBER_EMAIL, MEMBER_PASSWORD);
  await page.goto('/quality-incidents');
  await page.getByRole('button', { name: '创建质量事件' }).click();
  await fillIncidentForm(page);
  await page.getByRole('button', { name: '创建事件' }).click();
  await page.getByTestId('bind-evidence-snapshot').fill(UPSTREAM_SNAPSHOT_ID);
  await page.getByRole('button', { name: '冻结证据' }).click();
  await addEvidenceBackedHypothesis(page);
  await addExpertReview(page, '支持');
  await createActionAndReport(page);
  await page.getByRole('button', { name: '提交确认' }).click();
  await expect(page.getByText('统计线索，需专家确认')).toBeVisible();
  const reviewUrl = page.url();

  await page.getByRole('button', { name: '退出登录' }).click();
  await login(page, DIRECTOR_EMAIL, DIRECTOR_PASSWORD);
  await page.goto(reviewUrl);
  await page.getByRole('button', { name: '批准报告' }).click();
  await page.getByRole('button', { name: '关闭事件' }).click();
  await page.getByRole('tab', { name: '历史案例库' }).click();
  await expect(page.getByText('报告 v1')).toBeVisible();
  await page.getByRole('button', { name: '下载 v1' }).click();
});
```

The seed script is deterministic and uses the same snapshot UUID and default credentials:

```python
async def main() -> None:
    database_url = os.environ["IRIP_DATABASE_URL"]
    async_url = database_url.replace(
        "postgresql+psycopg://", "postgresql+psycopg_async://", 1
    )
    factory = build_session_factory(async_url)
    scenario = await seed_quality_e2e(factory)
    assert str(scenario.upstream_snapshot_a) == "81818181-8181-4818-8818-818181818181"


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: Run the browser journey**

```bash
IRIP_ENV=test uv run --frozen --extra dev python scripts/seed_quality_incident_e2e.py
QUALITY_INCIDENT_MODULE_ENABLED=true \
  pnpm --dir apps/web e2e -- quality-incidents.spec.ts
```

Expected: PASS against the local API/Web/PostgreSQL stack.

- [ ] **Step 5: Document enablement and honest scope**

The user guide must include:

- roles and permissions;
- how an operator prepares an upstream IRIP evidence snapshot;
- each lifecycle state and who may advance it;
- how to record support and opposing evidence;
- report correction/version behavior;
- case-library search;
- explicit Stage 1 limitation: no automatic LIMS/DCS window extraction or diagnostic algorithm yet.

Add `QUALITY_INCIDENT_MODULE_ENABLED=false` to `.env.example`. Update `docs/STAGE_DELIVERY.md` with actual verification results only; keep the overall release classification honest if the full release gate is not green.

Use these exact guide headings:

```markdown
# 质量事件室使用指南
## 启用模块
## 角色与权限
## 准备上游证据快照
## 待调查 → 调查中 → 待确认 → 已关闭
## 记录支持证据与反对证据
## 创建、审核与修正报告
## 跟踪措施和复发状态
## 检索历史案例
## 第一阶段能力边界
```

- [ ] **Step 6: Run complete proportionate verification**

```bash
uv run --frozen --extra dev ruff check apps packages tests
uv run --frozen --extra dev ruff format --check apps packages tests
uv run --frozen --extra dev mypy packages apps/api apps/worker
uv run --frozen --extra dev pytest tests/unit/quality_incidents tests/unit/api/test_quality_incidents_router.py -q
IRIP_ENV=test QUALITY_INCIDENT_MODULE_ENABLED=true \
  uv run --frozen --extra dev pytest tests/integration/quality_incidents \
  tests/acceptance/test_quality_incident_mvp.py -q
pnpm --dir apps/web test --run \
  src/test/quality-incidents-api.test.ts \
  src/test/quality-incident-center.test.tsx \
  src/test/quality-incident-detail.test.tsx \
  src/test/quality-incident-report.test.tsx \
  src/test/quality-incident-cases.test.tsx
pnpm --dir apps/web build
git diff --check
```

Expected: every listed command PASS. Infrastructure-dependent E2E must also pass before declaring the Stage 1 user journey complete.

- [ ] **Step 7: Review scope and placeholders**

```bash
rg -n "TODO|TBD|FIXME|NotImplementedError|pass$" \
  packages/quality_incidents apps/api/routers/quality_incidents.py \
  apps/api/composition/quality_incidents.py \
  apps/web/src/features/quality-incidents \
  docs/user-guide/quality-incident-room.md
rg -n "root cause|根因|因果" \
  packages/quality_incidents apps/web/src/features/quality-incidents \
  docs/user-guide/quality-incident-room.md
```

Expected: no placeholders in production paths. Any causal wording is explicitly qualified as a hypothesis/statistical clue requiring expert confirmation.

- [ ] **Step 8: Commit the verified Stage 1 handoff**

```bash
git add tests/acceptance/test_quality_incident_mvp.py tests/e2e/quality-incidents.spec.ts \
  scripts/seed_quality_incident_e2e.py \
  docs/user-guide/quality-incident-room.md docs/STAGE_DELIVERY.md \
  .env.example
git commit -m "docs: hand off quality incident closed-loop MVP"
```

---

## Stage 1 Definition of Done

- A member can create and investigate an event but cannot approve it.
- A director different from the report author can approve the latest traceable version and close the incident.
- The event cannot leave `open` without a valid evidence snapshot and cannot close without expert reviews and an approved latest report.
- Snapshot, review, report-version, and approval history survive attempted mutation at both service and database levels.
- RLS isolation is proven using the restricted application role.
- Every key report finding resolves to the frozen incident snapshot and appears in the DOCX evidence index.
- Closed events are searchable as cases without copying them into a second source of truth.
- The web application exposes the module only when the rollout flag is enabled.
- The documentation states that evidence ingestion and diagnostics are still manual/upstream in Stage 1.
- Unit, integration, acceptance, focused frontend, build, and E2E commands above have current passing evidence.

## Follow-on Plan Trigger

Create the Stage 2 plan only after the closed-loop MVP has been exercised with at least one real historical incident. Stage 2 starts from observed manual bottlenecks and adds automatic source-window extraction, batch/time alignment, data-quality grading, change-point and lag analysis, opposing-evidence search, and similar-case retrieval. It must preserve all Stage 1 hashes, citations, approval, RLS, and immutable-version contracts.
