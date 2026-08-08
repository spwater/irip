# PRD: 研究域基础（子项目 1）

> **项目名称**: irip_research_foundation
>
> **编程语言/技术栈**: 后端 Python 3.12+ / FastAPI / SQLAlchemy(异步) / PostgreSQL 16(pgvector) / Redis 7；前端 React 18 + TS / Vite / Ant Design 5 / TanStack Router+Query
>
> **日期**: 2026-08-05
>
> **状态**: 评审稿

---

## 0. 原始需求复述

IRIP "研究分析与发布成果"模块设计方案建议拆为 5 个子项目分阶段建设。本期交付**第 1 个子项目"研究域基础"**，目标是交付一个完整可运行的基础切片，作为后续四个子项目（可信执行、研究产物、发布与复用、统一溯源与知识接口）的地基。

具体范围包括：
1. 独立 `research_*` 命名空间模块壳，可通过功能开关整体关闭/删除，关闭后老系统不受影响。
2. 个人 Workspace 的创建、列表、归档、删除；一个 Workspace 一个主研究问题（允许子问题）；分叉 Workspace。
3. ResearchQuestionVersion（研究问题版本），创建后不可变，重大问题变更形成新版本。
4. WorkspaceEvidenceRef（证据引用），跨任务搜索有权限的 Fact 并加入数据引用，草稿期可增删。
5. ResearchEvidenceSnapshot（证据快照），第一次正式执行前冻结，记录源对象/版本/内容哈希/获取时间/权限包络/字段清单，不可变。
6. `research:use` 权限点；Workspace 只属于创建者本人（个人私有），不设置成员列表。
7. CoreFactProvider 只读搜索和获取 Fact 输入，不暴露核心数据库会话；ResearchCatalog 搜索已发布衍生数据（本期可留接口占位）。
8. 实验室运营三个 Tab（实验项目 / 研究分析 / 发布成果），功能开关控制挂载。

**基线约束**：新模块不反向侵入老系统。它只通过只读接口使用原数据；独立保存 Workspace、证据引用、证据快照和研究侧溯源；关闭或删除新模块后，原系统仍可正常运行。

---

## 1. 产品目标

| # | 目标 | 衡量标准 |
|---|------|---------|
| G1 | **模块隔离与可拔除**：建立独立 `research_*` 命名空间的模块壳和功能开关，关闭后实验项目和原核心 API 不受影响 | 功能开关关闭后，/lab-ops 恢复原 `parameters` 和 `models` Tab 行为；研究 API 和研究表可独立移除，核心表无反向依赖 |
| G2 | **Workspace 基础生命周期**：用户能创建、查看、归档、删除个人 Workspace，定义和版本化研究问题 | 创建的 Workspace 可在列表中看到；无发布成果引用时可删除；有引用时只能归档；研究问题版本不可变且可追溯 |
| G3 | **证据选择与快照冻结**：用户能跨任务搜索有权限的 Fact 并加入 Workspace 证据集，系统在正式执行前冻结不可变证据快照 | 可搜索并加入有权限的 Fact；草稿期可增删证据引用；快照记录完整的源对象/版本/内容哈希/获取时间/权限包络/字段清单且不可变 |

---

## 2. 用户故事

**US-1 — 创建研究 Workspace**
> 作为研究人员，我想创建一个个人研究 Workspace 并定义主研究问题，以便围绕一个明确的科研问题组织跨任务的证据和分析。

**US-2 — 搜索并加入实验事实**
> 作为研究人员，我想在 Workspace 中跨任务搜索所有有权访问的实验事实（Fact），并选择性加入作为证据，以便为研究问题积累数据基础。

**US-3 — 冻结证据快照**
> 作为研究人员，我想在开始正式分析前让系统冻结当前证据集为不可变快照，以便后续分析有明确且可追溯的输入边界，源数据变化不影响已冻结的快照。

**US-4 — 分叉 Workspace**
> 作为研究人员，我想从当前 Workspace 分叉出一个新的独立 Workspace（继承数据引用和上下文），以便在明显不同的研究方向上独立探索和独立发布，而不影响原 Workspace。

**US-5 — 管理员控制模块开关**
> 作为平台管理员，我想通过功能开关整体启用或关闭研究分析模块，以便在需要时可以安全移除研究功能而不影响实验项目和核心数据系统的正常工作。

---

## 3. 需求池

### P0 — Must Have

| ID | 需求 | 验收标准 |
|----|------|---------|
| P0-1 | **功能开关机制**：引入研究模块功能开关（如 `RESEARCH_MODULE_ENABLED` 环境变量或系统配置），控制研究 API 注册、前端 Tab 挂载 | 开关关闭时：研究 API 路由不注册；/lab-ops 恢复为实验项目 / 衍生数据 / 模型发布三个 Tab 及原有行为；开关开启时：Tab 变为实验项目 / 研究分析 / 发布成果 |
| P0-2 | **research_* 命名空间**：所有研究域数据库表以 `research_` 前缀命名，对象存储使用独立前缀，独立 Alembic 迁移文件（延续现有 0073+ 编号） | 研究表包含 `research_workspace`、`research_question_version`、`research_workspace_evidence_ref`、`research_evidence_snapshot`；核心表无到研究表的外键 |
| P0-3 | **research:use 权限点**：在 `Permission` 类中新增 `RESEARCH_USE = "research:use"`，并按角色分配（lab_director / lab_member 拥有；lab_viewer 不拥有） | 无 `research:use` 权限的用户无法创建 Workspace 或加入证据；有权限的用户可正常操作 |
| P0-4 | **ResearchWorkspace 创建**：用户可创建个人 Workspace，设置名称和主研究问题文本；系统记录创建者、创建时间 | 创建后返回 Workspace ID；列表中可见；只属于创建者本人（owner_user_id），无成员列表 |
| P0-5 | **ResearchWorkspace 列表**：研究分析首页展示当前用户的 Workspace 列表，支持按活跃 / 归档 / 更新时间筛选 | 新用户看到空状态和"新建 Workspace"主操作；已有用户默认看到最近 Workspace |
| P0-6 | **ResearchWorkspace 归档**：已产生发布成果引用的 Workspace 只能归档，不能彻底删除 | 归档后不出现在默认列表中，但可在"归档"筛选下查看 |
| P0-7 | **ResearchWorkspace 删除**：没有发布成果引用时允许删除 Workspace 及其关联数据 | 删除前系统检查发布成果引用；无引用时允许删除，有关联时拒绝并提示归档 |
| P0-8 | **ResearchQuestionVersion 不可变版本**：创建研究问题时生成 v1 版本记录；重大问题变更形成新版本（v2, v3...），旧版本保留且不可变 | 每个 Workspace 有一个主研究问题的版本链；版本记录包含问题文本、版本号、创建时间；旧版本不可修改 |
| P0-9 | **WorkspaceEvidenceRef 证据引用**：用户可跨任务搜索有权限的 Fact，加入 Workspace 作为证据引用；草稿期可增删 | 搜索结果按当前用户权限过滤（参照 Fact 的 `visible_departments` / `visibility_scope`）；引用记录保存源对象命名空间与 ID |
| P0-10 | **CoreFactProvider 只读适配**：封装只读接口用于搜索和获取 Fact 数据，不暴露核心数据库会话 | 接口方法如 `search_facts(query, principal)` / `get_fact(fact_id, principal)`；内部使用独立 session，调用方无法获得核心 session 引用 |
| P0-11 | **ResearchEvidenceSnapshot 证据快照**：第一次正式执行前冻结当前证据集为不可变快照，记录源对象命名空间与 ID、源版本、内容哈希、获取时间、权限包络、字段清单 | 快照创建后不可修改；包含所有必需字段；源数据后续变化不影响已冻结快照 |
| P0-12 | **导航入口**：实验室运营页面 Tab 在功能开关开启时变为"实验项目 / 研究分析 / 发布成果" | "研究分析"占用原"衍生数据"（parameters）位置；"发布成果"占用原"模型发布"（models）位置；功能开关关闭时恢复原 Tab |
| P0-13 | **审计事件**：Workspace 创建、归档、删除；证据加入、移除、快照冻结均产生审计记录 | 审计记录包含操作类型、操作者、时间、关联对象 ID；不含大体积数据内容 |

### P1 — Should Have

| ID | 需求 | 验收标准 |
|----|------|---------|
| P1-1 | **分叉 Workspace**：从当前 Workspace 分叉出新 Workspace，继承数据引用和必要上下文（主研究问题、证据引用列表），后续独立运行 | 分叉后的 Workspace 独立于源 Workspace；继承的证据引用为副本而非共享引用 |
| P1-2 | **子问题支持**：一个 Workspace 允许在主研究问题下定义多个子问题 | 子问题挂载在 Workspace 下，不形成独立版本链（仅主问题版本化） |
| P1-3 | **ResearchCatalog 接口占位**：定义 `ResearchCatalog` 只读接口用于搜索已发布衍生数据，本期返回空列表（因发布能力在子项目 4 交付） | 接口已定义且可调用；返回空结果不报错；后续子项目实现时无需修改接口签名 |
| P1-4 | **Workspace 源数据新版本提示**：当已引用的源 Fact 出现新版本或状态变化时，Workspace 提示用户（但不自动更新已冻结快照） | 提示信息显示在证据列表中对应引用旁；旧 Run 继续绑定旧快照不受影响 |
| P1-5 | **权限运行期校验**：选择证据、冻结快照时重新校验权限 | 源数据无权访问时不加入或不冻结，显示权限原因，不泄露内容 |

### P2 — Nice to Have

| ID | 需求 | 验收标准 |
|----|------|---------|
| P2-1 | **Workspace 搜索/排序**：Workspace 列表支持按名称搜索和按更新时间排序 | 搜索结果实时过滤 |
| P2-2 | **证据引用字段预览**：加入证据引用时展示 Fact 的字段清单预览 | 预览信息从 Fact 的 `metadata / points / series` 结构中提取关键字段名 |
| P2-3 | **快照刷新**：用户主动刷新证据生成新的 Evidence Snapshot 和新的 Run（为后续子项目 2 预留交互入口） | 本期提供刷新 API 接口；前端交互可在后续子项目实现 |

---

## 4. UI 设计概要

### 4.1 研究分析首页（Workspace 列表）

```
┌─────────────────────────────────────────────────────────────────┐
│  实验室运营  [实验项目] [研究分析] [发布成果]                      │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  研究分析                                                        │
│                                                                  │
│  筛选: [全部▾] [活跃|归档]  排序: [更新时间↓]   [🔍 搜索]          │
│                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │  + 新建       │  │ Workspace A  │  │ Workspace B  │          │
│  │  Workspace   │  │ 主问题: ...  │  │ 主问题: ...  │          │
│  │              │  │ 证据: 5       │  │ 证据: 12     │          │
│  │              │  │ 更新: 2h前    │  │ 更新: 昨天   │          │
│  └──────────────┘  └──────────────┘  └──────────────┘          │
│                                                                  │
│  [归档的 Workspace ▾]                                            │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

- **空状态**：新用户看到空状态引导，主操作按钮"新建 Workspace"。
- **Workspace 卡片**：显示名称、主研究问题摘要、证据数量、最近更新时间。
- **归档区**：折叠展示已归档的 Workspace，点击可展开查看。
- **功能开关关闭时**：整个研究分析 Tab 不挂载，恢复为原"衍生数据"Tab。

### 4.2 Workspace 三栏布局

```
┌──────────┬──────────────────────┬──────────────┐
│ Evidence │      研究画布          │   AI 助手     │
│   Set    │                       │              │
│          │                       │              │
│ 🔍 搜索  │  主研究问题 [v2]       │  AI 对话区    │
│ 证据     │  ┌─────────────────┐  │              │
│          │  │ "不同批次间峰值  │  │  [建议...]    │
│ ──────── │  │  差异的来源是什么│  │  [解释...]    │
│ 已选证据  │  │  ？是否有系统   │  │              │
│          │  │  性因素？"     │  │              │
│ ✓ Fact-A │  └─────────────────┘  │              │
│   v3 权限 │                       │              │
│ ✓ Fact-B │  子问题:               │              │
│   v1 权限 │  · 温度梯度的影响      │              │
│ ✓ Fact-C │  · 原料批次差异        │              │
│   v2 权限 │                       │              │
│          │  ──────────────────   │              │
│ [冻结快照] │  证据集 (5)  [冻结快照] │              │
│          │                       │              │
│          │  (本期画布展示证据集   │              │
│          │   和问题；分析计划、   │              │
│          │   Run 和交互图表在    │              │
│          │   子项目 2-3 实现)     │              │
└──────────┴──────────────────────┴──────────────┘
```

**左栏 — Evidence Set**：
- 顶部搜索框，跨任务搜索有权限的 Fact。
- 下方列出已选证据引用，显示源对象名称、版本号、权限状态（tree/explicit/all 标识）。
- 草稿期可增删；每项有删除按钮。
- 底部"冻结快照"按钮，点击后系统冻结当前证据集为不可变快照。
- 冻结后左栏切换为只读快照视图，显示快照时间戳和哈希摘要。

**中栏 — 研究画布**：
- 顶部展示主研究问题文本和当前版本号（如 v2），支持编辑（编辑触发新版本创建）。
- 下方列出子问题。
- 证据集区域展示当前引用数量和快照状态。
- 本期画布聚焦证据选择和问题管理；分析计划、Run 进度、交互图表和候选成果在后续子项目实现。

**右栏 — AI 助手**：
- 本期为预留区域，显示"AI 科研助手将在后续版本中启用"占位。
- 子项目 2（可信执行）交付后填充：持续对话、主动建议、计划说明等功能。

### 4.3 功能开关关闭时的 Tab 行为

```
┌─────────────────────────────────────────────────────────────────┐
│  实验室运营  [实验项目] [衍生数据] [模型发布]                      │
│                         (恢复原 ParameterPage)  (恢复原占位)      │
└─────────────────────────────────────────────────────────────────┘
```

---

## 5. 待确认问题

| # | 问题 | 影响范围 | 建议 |
|---|------|---------|------|
| Q1 | 功能开关的存储方式：使用环境变量（`RESEARCH_MODULE_ENABLED`）还是数据库系统配置表？环境变量更简单但需重启生效；数据库配置可运行时切换但需额外管理 UI。 | P0-1, P0-12 | 建议首期使用环境变量，后续可升级为数据库配置 |
| Q2 | 研究域 Alembic 迁移编号：延续现有 0074+ 编号，还是使用独立迁移文件命名空间（如 `research_001`）？独立编号更容易整体移除，但偏离现有迁移管理惯例。 | P0-2 | 建议延续现有编号（0074+），移除时通过 down migration 处理 |
| Q3 | 证据快照的"内容哈希"计算范围：是计算 Fact 完整数据（`metadata / points / series` 全部内容）的哈希，还是仅计算实际引用字段的哈希？完整哈希更安全但开销大。 | P0-11 | 建议计算实际引用字段清单对应数据的哈希，配合字段清单记录 |
| Q4 | `research:use` 权限是否需要同时获得 `fact:read` 权限才能搜索和引用 Fact？还是 `research:use` 独立校验，CoreFactProvider 内部再校验 `fact:read`？ | P0-3, P0-9, P0-10 | 建议两层校验：`research:use` 控制模块入口，CoreFactProvider 内部校验数据级权限（fact:read + 可见性） |
| Q5 | 分叉 Workspace 时继承的"必要上下文"具体包含哪些？是否包括研究问题版本历史、AI 对话记录、研究记忆文档？本期子项目 1 尚无 Run 和 AI 对话，仅需确认后续继承范围。 | P1-1 | 本期分叉仅继承主研究问题（最新版本）和证据引用列表；Run/对话/记忆在后续子项目实现时补充继承逻辑 |
| Q6 | "发布成果引用"的判断依据：本期尚无发布成果能力（子项目 4），Workspace 删除检查是否仅需检查是否有关联的证据快照即可阻止删除，还是本期允许无限制删除？ | P0-7 | 本期无发布成果，允许无限制删除（有证据引用和快照时级联删除）；后续子项目 4 实现后增加发布成果引用检查 |

---

## 6. 技术实现要点（供架构师参考）

### 6.1 后端包结构

```
packages/research/
├── __init__.py
├── entities.py        # ORM: research_workspace, research_question_version,
│                      #      research_workspace_evidence_ref, research_evidence_snapshot
├── repository.py      # 数据访问层
├── service.py         # 业务编排: WorkspaceService（创建/列表/归档/删除/分叉）
├── core_adapter.py    # CoreFactProvider 只读适配
├── catalog.py         # ResearchCatalog 接口占位
└── snapshots.py       # EvidenceSnapshotService（冻结逻辑 + 哈希计算）
```

### 6.2 API 路由

```
apps/api/routers/research.py
├── POST   /api/research/workspaces              # 创建 Workspace
├── GET    /api/research/workspaces              # 列表（支持筛选）
├── GET    /api/research/workspaces/{id}         # 详情
├── PATCH  /api/research/workspaces/{id}         # 更新（名称等可变字段）
├── DELETE /api/research/workspaces/{id}         # 删除（检查发布引用）
├── POST   /api/research/workspaces/{id}/archive # 归档
├── POST   /api/research/workspaces/{id}/fork    # 分叉
├── PUT    /api/research/workspaces/{id}/question # 更新研究问题（生成新版本）
├── POST   /api/research/workspaces/{id}/evidence # 加入证据引用
├── DELETE /api/research/workspaces/{id}/evidence/{ref_id} # 移除证据引用
├── GET    /api/research/workspaces/{id}/evidence  # 证据列表
├── POST   /api/research/workspaces/{id}/snapshot  # 冻结证据快照
└── GET    /api/research/workspaces/{id}/snapshots # 快照列表
```

路由注册需在 `apps/api/main.py` 中受功能开关控制。

### 6.3 前端结构

```
apps/web/src/features/research/
├── ResearchPage.tsx          # 研究分析首页（Workspace 列表）
├── WorkspaceDetail.tsx       # Workspace 三栏布局
├── EvidencePanel.tsx         # 左栏：Evidence Set
├── ResearchCanvas.tsx        # 中栏：研究画布
├── AiAssistantPanel.tsx      # 右栏：AI 助手占位
├── WorkspaceCard.tsx         # 列表卡片组件
├── CreateWorkspaceModal.tsx  # 创建 Workspace 对话框
└── api/
    └── research.ts           # TanStack Query hooks
```

LabOpsPage 改造：功能开关开启时 Tab 定义为 `['flows', 'research', 'publication']`，关闭时保持 `['flows', 'parameters', 'models']`。

### 6.4 数据库表设计概要

**research_workspace**
- `id` (UUID PK), `owner_user_id` (FK→app_user), `name` (TEXT), `status` (TEXT: draft/archived), `current_question_version` (INT), `forked_from_id` (UUID nullable), `created_at`, `updated_at`, `lock_version`

**research_question_version**
- `id` (UUID PK), `workspace_id` (FK→research_workspace), `version_number` (INT), `question_text` (TEXT), `sub_questions` (JSONB), `created_at`, `created_by` (FK→app_user)
- 不可变：创建后不允许 UPDATE

**research_workspace_evidence_ref**
- `id` (UUID PK), `workspace_id` (FK→research_workspace), `source_namespace` (TEXT: "core:fact"), `source_id` (UUID), `source_version` (TEXT), `source_name` (TEXT), `added_at`, `added_by` (FK→app_user), `status` (TEXT: active/removed)
- 草稿期可软删除（status→removed），不物理删除

**research_evidence_snapshot**
- `id` (UUID PK), `workspace_id` (FK→research_workspace), `snapshot_number` (INT), `content_hash` (TEXT), `captured_at` (UTCDateTime), `permission_envelope` (JSONB), `field_manifest` (JSONB), `source_refs` (JSONB: [{namespace, id, version}]), `created_by` (FK→app_user)
- 不可变：创建后不允许 UPDATE/DELETE（通过应用层保证，非 DB 级 IMMUTABLE）

> **注意**：核心表（fact、evidence_set 等）不建立到研究表的外键。跨模块关系保存为带命名空间的逻辑引用（`source_namespace` + `source_id`），而非数据库级外键约束。

### 6.5 权限集成

在 `packages/auth/permissions.py` 的 `Permission` 类中新增：

```python
# 研究分析（IRIP Research Module - 子项目1）
RESEARCH_USE: str = "research:use"
```

在 `BUILTIN_ROLES` 中为 `lab_director` 和 `lab_member` 添加 `Permission.RESEARCH_USE`；`lab_viewer` 和 `platform_auditor` 不添加（只读角色不创建 Workspace）。

### 6.6 功能开关实现

```python
# packages/common/feature_flags.py (新增)
RESEARCH_MODULE_ENABLED = os.getenv("RESEARCH_MODULE_ENABLED", "true").lower() == "true"
```

- 后端：`apps/api/main.py` 中条件注册 `research_router`
- 前端：通过 API 响应或环境变量获取开关状态，LabOpsPage 条件渲染 Tab
