# IRIP 架构与代码质量深度审查报告

**审查人**：高见远（Gao），架构师
**审查日期**：2026-08-08
**项目版本**：v0.8.0
**审查范围**：模块边界 / 分层架构 / 设计模式 / 安全架构 / 代码质量 / 迁移链 / 前端架构 / CI 质量门
**审查方式**：静态分析 + 本地复现 + CI 远端日志取证（只审查，不修改代码）

---

## 0. 执行摘要

**总体成熟度评级：C+（有架构骨架，但质量门全线失守，不具备生产发布条件）**

IRIP 已建立起相当完整的分层架构（Router → Composition/ApplicationService → packages/DomainService+Repository → Entities）和安全基线（JobKindPolicy、EnvelopeEncryption、Fencing Token、RLS+GUC、不可变触发器），上一轮审计的 4 个 Critical 中 3 个已实质修复。**但当前代码库处于"构建即坏"状态**：

- CI 连续 6 次全红（自 2026-08-07 07:33 后再未绿过），质量门（H-17）形同虚设；
- Mypy 被 `provenance.py` 语法错误 100% 阻断，**整个类型安全门完全失效**；
- 前端生产构建被 2 个 TS 错误阻断；
- 4 个数据库相关 CI Job（Integration/Security/Recovery/Acceptance）因 `alembic upgrade head` 后缺表（`department`/`parameter`/`parameter_candidate`）而集体失败——上一轮 H-02"fresh migration 不可靠"不仅未修复，反而因 squashed baseline + env.py 双重执行而**恶化**；
- 本地 Python 3.13 环境下 `ErrorCode` 枚举无法导入，**全部 Python 测试无法运行**（CI 用 3.12 故不可见，存在版本覆盖盲区）。

**结论：No-Go。必须先修复 P0/P1 阻断项，恢复 CI 全绿，方可重新评估发布。**

问题分级汇总：

| 级别 | 数量 | 说明 |
|------|------|------|
| Critical (P0) | 4 | CI 全红、provenance 语法错误阻断 Mypy、3.13 Enum 不兼容、fresh-migration 缺表 |
| High (P1) | 8 | Ruff 50 错、6 文件待格式化、TS 2 错、死表查询+静默吞错、Router 越界、CI 版本错配、Mypy 范围缺口、Coverage 门限过低 |
| Medium (P2) | 6 | research 上帝包、大型文件、GUC 文档/实现不符、type:ignore 偏高、AI XSS 需核实、迁移元数据风格不一 |
| Low (P3) | 1 | 备份 pg_dump 存量路径待核实 |

---

## 1. 问题清单（按严重程度分级）

### Critical (P0)

#### P0-1 · CI 质量门全线失守（H-17 仍存在，且恶化）
- **位置**：`.github/workflows/ci.yml`；最近 6 次 CI run（`gh run list`）全部 `failure`
- **证据**：最近一次绿线为 2026-08-07 07:33（commit `fix: 数值工具 5 个评审问题修复`），之后 6 次 push 全红。最新 run（31197481143）失败 Job：
  - `Ruff Lint + Format Check` ✗（50 errors）
  - `Mypy Type Check` ✗（被 provenance.py 语法错误阻断）
  - `Web Build` ✗（2 TS 错误）
  - `Web Unit Tests` ✗（2 测试失败）
  - `Unit Tests` ✗（1 测试失败 `test_no_session_factory_skips_db_check`）
  - `Integration / Security / Recovery / Acceptance` ✗（缺表 `relation "department"/"parameter"/"parameter_candidate" does not exist`）
  - `Generate Stats & Acceptance Report` ⊘（skipped，因依赖未满足）
- **影响**：质量门完全失效，任何破坏性变更都能无阻合并；上一轮 H-17"质量门失败"未修复。
- **建议**：立即修复下述 P0-2/3/4 与 P1 阻断项，目标"主干 CI 全绿"作为解除 No-Go 的前置条件。

#### P0-2 · `provenance.py` 语法错误阻断 Mypy（类型安全门 100% 失效）
- **位置**：`packages/research/provenance.py:27-28`
- **证据**：`from typing import Any` 被注入到 `from packages.research.models import (...)` 的括号导入块中间：
  ```python
  from packages.research.models import (
  from typing import Any
      ProvenanceEdge,
      ...
  )
  ```
  CI Mypy 日志：`packages/research/provenance.py:28: error: Invalid syntax [syntax]` → `Found 1 error in 1 file (errors prevented further checking)`。
- **影响**：Mypy 对 `packages apps/api` 完全无法运行，**全仓库类型安全检查归零**；Ruff 也报 4 `invalid-syntax`。
- **建议**：将 `from typing import Any` 移到文件顶部标准 import 区，恢复 Mypy 可运行后立即重跑以暴露被掩盖的真实类型错误。

#### P0-3 · Python 3.13 Enum 兼容性阻断本地全部测试
- **位置**：`packages/common/error_codes.py:146`
- **证据**：`_code_cache: dict[str, "ErrorCode"] | None = None` 置于 `ErrorCode(enum.Enum)` 体内，配合自定义 `__init__(self, code, http_status)`（需 2 参）。在 Python 3.13.12 复现：
  ```
  File "enum.py", line 285, in __set_name__
      enum_member.__init__(*args)
  TypeError: ErrorCode.__init__() missing 1 required positional argument: 'http_status'
  ```
  经最小复现确认：**根因是赋值 `_code_cache = None`（单值）被 3.13 enum 元类当作成员尝试 `__init__(None)`**，与是否带类型注解无关（仅注解不赋值则正常）。`packages/common/__init__.py` 导入链触发该错误，conftest 无法收集，本地 3.13 venv 全部 Python 测试无法运行。
- **影响**：项目 `requires-python = ">=3.12"` 且 `pyproject.toml` 注释明确"放开上限以兼容本机 Python 3.13"，本机 venv 即 3.13.12——**实际运行环境被锁死**。CI 用 3.12 可通过（`error-code-check` Job 成功），故该缺陷对 CI 不可见。
- **建议**：用 `_ignore_ = ["_code_cache"]` 声明非成员，或将缓存改为模块级 `functools.lru_cache`/独立变量；并在 CI 增加 3.13 矩阵（见 P1-6）。

#### P0-4 · Fresh migration 缺表（H-02 恶化）
- **位置**：`migrations/env.py:107-122`、`migrations/versions/0001_squashed_baseline.py`
- **证据**：`alembic upgrade head` 后，Integration/Security/Recovery/Acceptance 四个 CI Job 均报 `relation "department"/"parameter"/"parameter_candidate" does not exist`（Acceptance: 42 passed / 2 failed，缺表专指 `parameter`/`parameter_candidate`）。
  - `env.py` 采用 `try: asyncio.run(...) except RuntimeError: 线程内再跑一次` 模式，CI 日志显示**单次 `alembic upgrade head` 触发了两轮完整 `-> 0001 … -> 0082` 迁移序列**，属脆弱的二次执行模式。
  - squashed baseline `0001`（1570 行）由 `pg_dump --schema-only` 从 0061 状态库导出，`parameter` 表仍带 **已退役的 `organization_id`** 列；经自定义 `_split_sql`（按 `;` 切分、`$$` 感知）逐语句 `op.execute`。该 splitter 对 1570 行 pg_dump 的健壮性存疑。
- **影响**：fresh 安装路径产出的 schema 与 ORM 模型漂移，应用无法在全新库上启动；上一轮 H-02 未修复且恶化。
- **建议**：①修 `env.py` 双重执行（移除 `except RuntimeError` 二次执行，或在确实身处事件循环时仅执行一次）；②本地对空库跑 `alembic upgrade head` 后 `SELECT tablename FROM pg_tables` 与 `Base.metadata.tables` 做 diff，定位 `_split_sql` 漏建/错建表；③squashed baseline 应反映 0066 之后的 `department_id` 终态，而非 0061 的 `organization_id` 中间态。

### High (P1)

#### P1-1 · Ruff 50 错误（CI 范围）+ 294 错误（全仓库）
- **位置**：CI 执行 `ruff check apps packages tests` = 50 errors：18 I001 + 9 F401 + 8 E501 + 7 F811 + 4 invalid-syntax(provenance) + 1 B904 + 1 E402 + 1 F821(`test_plan_service.py:81` undefined `PlanService`) + 1 UP037。
- **附加发现**：全仓库（含 `migrations/scripts/examples/deliverables`）共 294 errors，其中 **232 E501 line-too-long** 为绝对多数。**CI 不检查 migrations/scripts/examples**，存在 lint 覆盖盲区。
- **建议**：`ruff check --fix` 可自动修 35 项；E501 需手动断行或评估是否放宽 line-length；将 `migrations` 纳入 CI lint 范围。

#### P1-2 · 6 个文件待格式化 + provenance 解析失败
- **位置**：`ruff format --check` 报 `apps/api/dependencies/auth.py`、`packages/connectors/mapping.py`、`packages/research/candidates.py`、`packages/research/orchestrator.py`、`packages/research/plan_service.py`、`tests/unit/research/test_plan_service.py`，外加 `provenance.py` parse error。
- **建议**：`ruff format` 一键修复（先修 provenance 语法）。

#### P1-3 · TypeScript 2 错误阻断前端生产构建
- **位置**：`apps/web/src/features/research/PlanReviewCard.tsx:52`（TS6133 `onAdjust` declared but never read）、`apps/web/src/features/research/QueueStatus.tsx:62`（TS2552 `Cannot find name 'message'`）
- **证据**：CI Web Build 日志确认 `error TS6133` / `error TS2552`，`pnpm build` exit 2。
- **建议**：移除/使用 `onAdjust`；修正 `QueueStatus` 的 `message` 引用（应为 `onmessage`？或引入 antd `message`）。

#### P1-4 · `assistant.py` 查询已退役 `organization` 表 + 静默吞错
- **位置**：`apps/api/routers/assistant.py:92-99`
- **证据**：
  ```python
  try:
      async with _ai_session_scope(...) as session:
          result = await session.execute(
              sa.text("SELECT id FROM organization WHERE code = 'IRIP-DEMO'")
          )
  except Exception:
      pass
  ```
  迁移 0066 `retire_organization` 已 `DROP TABLE IF EXISTS organization`，该表不再存在 → 运行时必抛 `UndefinedTable`，被 `except Exception: pass` **静默吞掉**。demo-seeding 查找逻辑实际永远失败回退。
- **影响**：①死代码/破损逻辑；②静默吞错掩盖问题（git log 称"前端静默 catch 清零"，但**后端仍存在** `except Exception: pass`）；③残留 org→dept 迁移清理不彻底。
- **建议**：改为查 `department` 表；移除裸 `except Exception: pass`，至少 `logger.warning`。

#### P1-5 · Router→Service 边界未统一落地
- **位置**：`apps/api/routers/` 共 10 个 router 直接 `import sqlalchemy`（auth/backups/audit/health/assistant/object_types/jobs/experiment_projects/ai_config/components）
- **证据**：
  - `jobs.py:328-334`：router 内直接 `sa.select(AppUser.display_name).where(...)`，并访问 service 私有方法 `service._scoped_session()`（标 `# noqa: SLF001`）。
  - `assistant.py:85/94`：router 内 `sa.select(AppUser)` / `sa.text(raw SQL)`。
- **影响**：上一轮"Router 下沉 Application Service"在 `parameters.py`/`facts.py` 等已落地（干净委托 `service.xxx`），但未全面铺开；边界不一致增加耦合与测试难度。
- **建议**：将跨表 join/取展示名等下沉为 service 方法（如 `service.get_created_by_name`），router 仅做参数校验与响应组装；禁止 router `import sqlalchemy`（可加 lint 规则）。

#### P1-6 · CI Python 版本错配（3.13 不兼容不可见）
- **位置**：`.github/workflows/ci.yml`（全部 Job `python-version: "3.12"`）；`pyproject.toml` `requires-python = ">=3.12"`；本机/venv = 3.13.12
- **影响**：P0-3 的 3.13 Enum 不兼容对 CI 不可见；`requires-python` 宣称 3.13 支持但实际破坏，属"宣称即违约"。
- **建议**：CI 增加 `3.13` 矩阵（至少 lint + unit）；或将 `requires-python` 收窄至 `>=3.12,<3.13` 直到修复。

#### P1-7 · Mypy 检查范围缺口
- **位置**：CI `mypy packages apps/api`——**不含 `apps/worker`、`tests`**
- **影响**：Celery worker 任务（`apps/worker/tasks/{derivation,flows,models,sysuser}.py`、`research_tasks.py`）无类型检查；测试代码无类型约束。
- **建议**：扩展 mypy 范围至 `apps/worker`；测试代码可设较宽配置单独检查。

#### P1-8 · Coverage 门限过低
- **位置**：CI unit `--cov-fail-under=25`、integration `--cov-fail-under=15`；`pyproject.toml` `[tool.coverage.report] fail_under = 30`
- **影响**：25%/15% 远低于生产级阈值（通常 60–80%），质量门形同虚设；当前 unit 实际覆盖足以通过（1420 passed），但门限不具防退化能力。
- **建议**：分阶段抬升（25→40→60），先保不回退。

### Medium (P2)

#### P2-1 · `packages/research/` 上帝包
- **位置**：`packages/research/`（30+ 文件）
- **证据**：单包承载研究域全部职责——`repository.py` 2450 行、`publication.py` 1867、`expression.py` 1765、`products.py` 1526、`plan_service.py` 1508、`orchestrator.py` 1470、`models.py` 1255、`entities.py` 980、`service.py` 851，外加 provenance/lineage/sandbox/scheduler/search 等。
- **影响**：内聚过度、单包变更影响面过大、难独立测试与演进。
- **建议**：按子域拆分（如 `research/execution`、`research/products`、`research/publication`、`research/lineage`、`research/planning`），各自独立包。

#### P2-2 · 大型文件（>500 行）38 个
- **位置**：见上；最大 `repository.py` 2450 行
- **建议**：优先拆分 `repository.py`/`publication.py`/`expression.py`/`plan_service.py`/`orchestrator.py`，按聚合根或用例切分。

#### P2-3 · `tenant_guc._safe_literal` 文档与实现不符
- **位置**：`packages/common/tenant_guc.py:27-37`
- **证据**：docstring 称"使用 PostgreSQL 的 `quote_literal` 函数"，实现却仅 `value.replace("'", "''")` 手工转义。
- **影响**：对 UUID 值安全（无引号字符），但名实不符；若未来用于非 UUID 值，手工转义不如 `quote_literal` 健壮。
- **建议**：改用 `session.execute(sa.text("SELECT quote_literal(:v)"), {"v": str(dept_id)})` 或保持手工转义但修正文档。

#### P2-4 · `type: ignore` 228 处（已从 505 治理）
- **位置**：`grep -rn "type: ignore" packages apps` = 228；另有 `# noqa` 117 处
- **建议**：继续分模块治理，目标 < 100；建立"新增 type:ignore 需 review"规则。

#### P2-5 · AI 消息 XSS（H-14 部分修复，需核实）
- **位置**：`apps/web/src/features/assistant/message-thread/components/BlockifiedMarkdown.tsx:112,145`（`dangerouslySetInnerHTML={{ __html: mathHtml }}`）；`MessageThread.tsx`、`ShowcaseCard.tsx` 同类用法
- **评估**：采用 blockified markdown + KaTeX JS API 渲染公式，比 naive raw-HTML 注入安全；但需核实 remark/rehype 管线**未启用 `allowDangerousHtml`**、且 AI 输出的 markdown 不含绕过 sanitize 的原始 HTML。
- **建议**：审计 markdown 渲染管线，确保 `remark-rehype` 未开 `allowDangerousHtml`；对 `mathHtml` 输入做白名单校验。

#### P2-6 · 迁移文件元数据风格不一致
- **位置**：`0001`/`0067`/`0068` 用 `revision: str = "..."`（带类型注解），其余 19 个用 `revision = "..."`（无注解）
- **影响**：风格不统一；曾导致我的自动化链分析脚本误判多 head（实际单 head 0082）。
- **建议**：统一为无注解 `revision = "..."`（alembic 模板默认）。

### Low (P3)

#### P3-1 · 备份 pg_dump 存量路径待核实（C-04）
- **位置**：`packages/backups/service.py`
- **评估**：默认 `backup_method=pitr`（增量 PITR，规避明文临时目录），`pg_dump` 标注为"存量"。C-04 的 PITR 路径已规避明文落盘；需核实 pg_dump 存量路径是否仍写明文临时目录。
- **建议**：确认 pg_dump 路径"流式加密直传 MinIO、不留明文 tmp"。

---

## 2. 与上一轮审计对比（2026-07-30）

### 已修复（验证通过）
| 项 | 上一轮 | 现状 |
|----|--------|------|
| C-02 通用 Job 接口触发特权作业 | Critical | ✅ `JobKindPolicy`：`allow_general_submit` 标志 + 特权 kind 专用 API + worker 二次校验 + fencing token + 未知 kind 直接 failed（无 echo fallback） |
| C-03 RLS 绕过 + 跨租户 IDOR | Critical | ✅ DB 账号拆分（`irip` 迁移/superuser vs `irip_app` 运行/非 superuser）；`tenant_guc` SET LOCAL fail-closed（None→空串→RLS 空集）；env.py 注释明确角色分工 |
| H-01 不可变触发器保护错表 | High | ✅ 触发器现保护正确表 `audit_event`/`component_version`/`evidence_set_version`/`flow_definition_version`；0081 叠加 REVOKE UPDATE/DELETE on audit_event；0068 GUC `app.allow_immutable_delete` 受控逃生通道（仅迁移角色可 SET） |
| H-03 作业租约/重试 | High | ✅ `WorkerLeaseManager`：`acquire_with_fencing` + `renew_lease` + `release` + `LEASE_TTL_SECONDS=30` + fencing token（lock_version 乐观锁） |
| H-06 主密钥 fail-open | High | ✅ `EnvelopeCrypto.from_env`：非测试环境缺 `IRIP_MASTER_KEY` → `raise RuntimeError`（fail-closed）；解密失败 raise 不回退明文；支持 `IRIP_MASTER_KEY_OLD_v1/v2` 轮换 |
| F-14 错误码穷尽性 | — | ✅ CI `error-code-check` Job 扫描所有 `AppError(code=...)` 与 `ErrorCode.all_codes()` 比对 |
| F-18 供应链 | — | ✅ Actions 全部 pin 到 commit SHA；SBOM 生成 + 非空校验（M-11） |
| S-6 前端懒加载 | — | ✅ `router.tsx` 用 `lazy()` + `Suspense` 包裹各页面 |
| type:ignore 治理 | — | ✅ 505 → 228 |

### 仍存在（未修复 / 恶化）
| 项 | 上一轮 | 现状 |
|----|--------|------|
| H-02 fresh migration 不可靠 | High | ❌ **恶化**——4 个 CI Job 因 `alembic upgrade head` 后缺 `department`/`parameter`/`parameter_candidate` 表而失败；env.py 双重执行 + squashed baseline 漂移 |
| H-17 质量门失败 | High | ❌ **CI 连续 6 次全红** |
| H-14 AI 消息 DOM XSS | High | ⚠️ 部分修复（blockified markdown + KaTeX），需核实 sanitize 管线 |

### 新增（本轮发现）
| 项 | 级别 | 说明 |
|----|------|------|
| provenance.py 语法错误 | P0 | `from typing import Any` 注入 import 括号块中间，阻断 Mypy |
| Python 3.13 Enum 不兼容 | P0 | `_code_cache = None` 在 Enum 体内触发 TypeError，锁死本地 3.13 测试 |
| assistant.py 查死表 + 静默吞错 | P1 | 查已 DROP 的 `organization` 表，`except: pass` 掩盖 |
| jobs.py router 越界查 ORM | P1 | router 内 `sa.select` + 访问 service 私有 `_scoped_session()` |
| TS 构建错误 | P1 | PlanReviewCard / QueueStatus 阻断 `pnpm build` |
| CI 版本错配 | P1 | CI 仅 3.12，3.13 不兼容不可见 |
| env.py 双重执行 | P0 | `except RuntimeError` 二次执行迁移 |

---

## 3. 架构亮点（保留的好实践）

1. **分层清晰**：`apps/api/routers`（HTTP）→ `apps/api/composition`（Application Service 装配）→ `packages/*`（Domain Service + Repository）→ `entities`（ORM）。已落地的 router（parameters/facts 等）边界干净。
2. **Composition 模式**：`apps/api/composition/` 集中装配领域服务依赖，便于替换与测试。
3. **JobKindPolicy**：服务端策略注册表 + 通用接口白名单 + 特权接口隔离 + worker 二次校验 + fencing token，纵深防御完备。
4. **EnvelopeEncryption**：AES-256-GCM 信封加密 + 多版本 key 轮换 + fail-closed 启动 + 单例。
5. **WorkerLeaseManager**：fencing token（lock_version 乐观锁）防重复执行/竞态。
6. **RLS + GUC fail-closed**：`SET LOCAL app.current_dept_id` None→空串→RLS 返回空集。
7. **不可变触发器 + REVOKE 双保险**：audit_event 等表 trigger + 0081 REVOKE 双重防护。
8. **CI 供应链硬ening**：Actions pin SHA + SBOM 非空校验 + 错误码穷尽性门。
9. **迁移链单 head 连续**：0001→0082 单链（squash 0001 压缩 0002–0061），无多 head。
10. **前端 feature-based 架构 + 懒加载 + 状态分层**：`features/*` 按业务域切分，Zustand 仅用于 jobs/auth 局部态，服务端态交 TanStack Query。

---

## 4. 建议整改优先级

**第一波（解除 P0 阻断，目标 CI 可运行）**
1. 修 `provenance.py:27-28` 语法（移 `from typing import Any` 到顶部）→ 解除 Mypy/Ruff 解析阻断
2. 修 `error_codes.py:146` `_code_cache`（`_ignore_` 或模块级缓存）→ 解除 3.13 导入阻断
3. 修 `PlanReviewCard.tsx`/`QueueStatus.tsx` 2 个 TS 错 → 解除前端构建阻断
4. 修 `assistant.py:94` 死表查询
5. `ruff check --fix && ruff format` 清 35 项可自动修 + 手动 E501

**第二波（恢复 CI 全绿）**
6. 修 `migrations/env.py` 双重执行；本地空库 `alembic upgrade head` + schema diff 定位 squashed baseline 缺表根因并修复（H-02）
7. 修 unit test `test_no_session_factory_skips_db_check` 失败
8. 修 web unit `research-trusted-execution` 2 处 `调整计划` 文本匹配失败
9. CI 增加 3.13 矩阵（P1-6）

**第三波（架构与质量基线抬升）**
10. Router→Service 边界全面落地（禁 router `import sqlalchemy`）
11. 拆分 `packages/research/` 上帝包与超大文件
12. Mypy 范围扩至 `apps/worker`；Coverage 门限分阶段抬升至 60%
13. 核实 H-14 markdown sanitize 管线；核实 C-04 pg_dump 明文路径

---

## 5. UNCLEAR / 假设

- **P0-4 缺表精确根因**未完全定位（squashed baseline 文本含 `CREATE TABLE public.parameter`，但 fresh 运行后缺失）。受限于本地无 DB 依赖无法实跑 `alembic upgrade head`。建议工程师本地空库复现 + `pg_tables` vs `Base.metadata` diff 确认是 `_split_sql` 漏建还是迁移间 DROP/重建链路问题。
- **H-14** 未逐行审计 remark/rehype 配置，"部分修复"判断基于 blockified 架构推断，需前端核实 `allowDangerousHtml` 未启用。
- **C-04** pg_dump 存量路径未逐行核实明文 tmp 落盘。
- **生产运行 Python 版本**未明确（影响 P0-3 的生产严重度：若生产固定 3.12 则 P0-3 降为开发体验问题，若可能 3.13 则为生产 P0）。
