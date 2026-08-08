# PRD: 研究产物（子项目 3）

> **项目名称**: irip_research_products
>
> **编程语言/技术栈**: 后端 Python 3.12+ / FastAPI / SQLAlchemy(异步) / PostgreSQL 16 / Redis 7 / Celery；沙箱 Kubernetes Pod 或等效容器调度器（延续阶段 2）；前端 React 18 + TS / Vite / Ant Design 5 / TanStack Router+Query
>
> **日期**: 2026-08-06
>
> **状态**: 评审稿
>
> **依赖基线**: 阶段 1"研究域基础" + 阶段 2"可信执行"已完成并上线（`docs/prd-research-foundation.md` / `docs/arch-research-foundation.md` / `docs/prd-research-trusted-execution.md` / `docs/arch-research-trusted-execution.md`）

---

## 0. 原始需求复述

IRIP "研究分析与发布成果"模块设计方案（`docs/superpowers/specs/2026-08-05-research-analysis-and-publication-design.md`）建议拆为 5 个子项目分阶段建设。本期交付**第 3 个子项目"研究产物"**，在阶段 1"研究域基础"和阶段 2"可信执行"已交付的 Workspace、证据快照、Analysis Run、DAG 步骤编排、沙箱执行和候选输出预览区之上，实现"Run 产物 → 候选成果 → 用户确认 → 版本化研究产物"的完整链路。

**阶段 1 已交付基线**：
- Workspace 创建/列表/归档/删除/分叉
- ResearchQuestionVersion 研究问题版本管理
- WorkspaceEvidenceRef 数据引用管理（`source_namespace` 支持 `core:fact`）
- ResearchEvidenceSnapshot 证据快照冻结（SHA-256 哈希 + 权限包络 + 字段清单）
- CoreFactProvider 只读适配 + ResearchCatalog 接口占位（返回空列表）
- 功能开关 + `research:use` 权限
- 前端三栏布局（左栏证据面板 / 中栏研究画布 / 右栏 AI 助手占位）

**阶段 2 已交付基线**：
- AnalysisPlanVersion 不可变计划版本 + 计划级授权
- AnalysisRun 后台持久运行 + DAG 步骤编排（ResearchOrchestrator）
- ResearchAnalysisStep 步骤状态管理（pending/running/succeeded/failed/skipped/cancelled）
- **ResearchRunArtifact** 工件表：`artifact_type`（code/log/chart/data/intermediate）、`is_publishable` 标记、MinIO 存储（`research/artifacts/{run_id}/{step_id}/`）
- RunArtifactService 工件收集 + 白名单扫描 + 持久化
- ModelGateway 模型网关（TaskType: PLANNING/CODE_GEN/LONG_CONTEXT/INSIGHT/CONVERSATION）
- ContextRouter 上下文路由 + 500K 预算 + 覆盖率计算
- SandboxRuntime 沙箱执行（断网、非 root、只读、资源限制）
- ResearchScheduler 20 用户公平调度
- ResearchMemoryService 后台研究记忆
- AIConversationService AI 对话持久化
- 前端右栏 AI 助手激活 + 中栏 Run 进度 + 排队 UI + **候选输出预览区（基础缩略卡片）**

**本期范围**：
1. **Derived Dataset**：可再次计算的衍生数据，版本化，采用 metadata/points/series 三段式结构 + 轻量 field_manifest
2. **ResearchView**：静态图（高分辨率 PNG）及其数据、代码引用，版本化
3. **Insight**：用户确认的结构化解释，版本化，保留 AI 原稿和修改记录
4. **静态图生成**：高分辨率 PNG（可选 PDF），保存代码/环境/输入引用和图表说明
5. **候选成果管理**：从 Run 产物中识别候选数据/图表/Insight，用户预览、确认或拒绝
6. **ResearchCatalog 部分实现**：搜索已确认 Derived Dataset，支持 Fact → Derived → Derived 链路

**基线约束**：
- 延续阶段 1-2 的模块隔离原则——新模块不反向侵入老系统，所有研究域实体使用 `research_*` 命名空间，关闭或删除新模块后原系统正常工作
- 阶段 3 **不包含**成果包发布（那是阶段 4"发布与复用"）。阶段 3 的产出是"确认后的 Derived Dataset / View / Insight"（具备独立 ID 和版本），它们将在阶段 4 组装为研究成果包并发布
- 代码/API/字段英文，UI 中文

---

## 1. 产品目标

| # | 目标 | 衡量标准 |
|---|------|---------|
| G1 | **候选成果到确认产物的完整链路**：Run 完成后，系统从 RunArtifact（data/chart 类型、is_publishable=true）和 AI 响应中识别候选数据、候选图表和候选 Insight，用户预览后确认或拒绝，确认后创建具备独立 ID 和版本号的研究产物 | 候选产物在 Run 完成后自动出现在中栏预览区；用户可预览三段式数据结构、静态图缩略图和 Insight 结构化字段；确认操作创建 DerivedDataset/ResearchView/Insight 实体及不可变 v1 版本；拒绝操作记录拒绝原因并保留候选记录 |
| G2 | **三段式衍生数据与编辑规则**：Derived Dataset 采用 metadata/points/series 三段式结构 + 轻量 field_manifest，版本不可变；标题/摘要/标签/图注/展示顺序可编辑，points/series 数值不能无痕手改，计算方法变更必须产生新 Analysis Run | DerivedDatasetVersion 创建后不可修改；field_manifest 记录字段名、自动推断类型、可选单位、一句话说明、来源步骤、列顺序和基本形状；编辑 API 仅允许修改 stable identity 上的元数据字段（name/summary/tags/caption/display_order），不触碰 version 内容 |
| G3 | **静态图与完整溯源**：正式成果保存高分辨率 PNG（可选导出 PDF），同时保存绘图代码、沙箱环境（image_digest）、输入数据引用和图表说明；View 必须绑定具体数据版本、绘图代码和 Analysis Run；首期不发布 HTML/JavaScript 交互图表 | View 版本记录 image_storage_path、chart_code_artifact_id、image_digest、source_run_id、bound_dataset_version_id、chart_description；图表调整通过 AI 重新生成并形成新 View 版本；静态图由沙箱内 matplotlib/seaborn 渲染为 PNG |
| G4 | **Insight 生命周期与可信标注**：AI 自然语言回答不自动成为正式 Insight；每条候选 Insight 必须包含结论、适用范围、证据引用、分析方法、置信说明和限制条件，并标明依据来源（实验数据/内部知识库/模型推测）；只有用户接受或修改确认后的 Insight 才能发布 | InsightCandidate 包含全部 6 个必填字段 + evidence_source_label；用户接受创建 Insight + InsightVersion v1（保留 AI 原稿）；用户修改创建 InsightVersion v1（保留 AI 原稿 + 修改记录）；用户拒绝标记候选为 rejected |
| G5 | **衍生数据再衍生链路**：已确认 Derived Dataset 可作为后续分析输入，形成 Fact → Derived → Derived 链路 | ResearchCatalog 从阶段 1 的空占位升级为可搜索当前用户已确认 Derived Dataset；WorkspaceEvidenceRef 支持 `research:derived` 命名空间；证据快照冻结时可捕获 Derived Dataset 版本和内容哈希 |

---

## 2. 用户故事

**US-1 — 预览并确认候选数据**
> 作为研究人员，我想在 Analysis Run 完成后看到系统自动从 Run 产物中识别出的候选衍生数据，并预览其三段式结构（metadata/points/series）和字段清单，以便我判断哪些数据值得保留为正式研究产物，并一键确认创建版本化的 Derived Dataset。

**US-2 — 预览并确认候选图表**
> 作为研究人员，我想看到 Run 产生的高分辨率静态图表缩略图，查看其绑定的数据版本、绘图代码和运行来源，以便我确认有价值的图表为正式 View，并知道它可以追溯完整复现链路。

**US-3 — 审阅并处理候选 Insight**
> 作为研究人员，我想看到 AI 在分析过程中提出的结构化 Insight 候选（包含结论、适用范围、证据引用、方法、置信度和限制条件），并知道每条候选的依据来源是实验数据、知识库还是模型推测，以便我做出科研判断——接受、修改或拒绝，而不是把 AI 回答直接当作科研事实。

**US-4 — 编辑产物元数据**
> 作为研究人员，我想为已确认的研究产物编辑标题、摘要、标签和图注，并调整展示顺序，以便在后续发布时产物有清晰的描述和组织，同时确信数据内容本身不可被无痕修改。

**US-5 — 使用已确认衍生数据作为新分析输入**
> 作为研究人员，我想在新的 Workspace 中搜索并加入我自己已确认的 Derived Dataset 作为证据，以便在已有分析结果的基础上进行进一步研究，形成 Fact → Derived → Derived 的知识积累链路。

---

## 3. 需求池

### P0 — Must Have

| ID | 需求 | 验收标准 |
|----|------|---------|
| P0-1 | **DerivedDataset + DerivedDatasetVersion 实体**：DerivedDataset 为稳定身份（可编辑 name/summary/tags/status），DerivedDatasetVersion 为不可变版本（三段式数据 + field_manifest + 来源引用 + 内容哈希）。一个 Dataset 可有多个版本，版本号递增 | DerivedDatasetVersion 创建后不可 UPDATE/DELETE（应用层保证）；三段式数据以 JSONB 存储（metadata/points/series）；field_manifest 记录字段名、自动推断类型、可选单位、一句话说明、来源步骤、列顺序和基本形状；content_hash 为三段式数据 SHA-256 |
| P0-2 | **从 RunArtifact 创建 DerivedDataset**：用户从 Run 产物中选择 `artifact_type=data` 且 `is_publishable=true` 的工件，确认为 Derived Dataset。系统从工件内容解析三段式数据，自动推断 field_manifest，创建稳定身份 + v1 不可变版本 | 创建后 DerivedDataset 有独立 UUID；DerivedDatasetVersion v1 包含从工件解析的三段式数据 + field_manifest；记录 source_run_id / source_step_id / source_artifact_id；非 publishable 工件不允许创建 |
| P0-3 | **三段式数据结构校验**：DerivedDatasetVersion 的 metadata/points/series 必须符合设计文档 8.3 节规范。metadata 保存报告级描述（dict），points 保存独立单值指标（list of {name, value, unit}），series 保存普通表格/时间序列/曲线/多批次（list of {name, columns, rows}） | 校验不通过的工件无法创建 DerivedDatasetVersion，返回字段级错误信息；空 series 或空 points 允许（数据可能只有指标或只有表格）；metadata 不可保存大量分析结果（仅报告级描述） |
| P0-4 | **ResearchView + ResearchViewVersion 实体**：ResearchView 为稳定身份（可编辑 name/caption/display_order/status），ResearchViewVersion 为不可变版本（静态图存储路径 + 格式 + 绘图代码引用 + 沙箱环境 + 输入数据引用 + 来源 Run/Step/Artifact）。版本号递增 | ResearchViewVersion 创建后不可修改；记录 image_storage_path（MinIO 路径）、image_format（png/pdf）、image_content_hash、chart_code_artifact_id、image_digest、source_run_id、bound_dataset_version_id（可空）、chart_description |
| P0-5 | **从 RunArtifact 创建 ResearchView**：用户从 Run 产物中选择 `artifact_type=chart` 且 `is_publishable=true` 的工件，确认为 ResearchView。系统创建稳定身份 + v1 不可变版本，绑定图表 PNG、绘图代码工件、沙箱环境和来源 Run | View 版本绑定 source_run_id（必须）、chart_code_artifact_id（绘图代码引用，可空如果代码内联于步骤）；image 来自工件 MinIO 存储；image_digest 从 Run 记录继承 |
| P0-6 | **静态图生成与保存**：沙箱内 Python 步骤使用 matplotlib/seaborn 生成图表时，输出高分辨率 PNG（DPI ≥ 300）。RunArtifactService 收集 PNG 工件时记录分辨率和格式。可选导出 PDF | 沙箱科学计算镜像已预装 matplotlib/seaborn（阶段 2 已保证）；PNG 工件存储路径为 `research/artifacts/{run_id}/{step_id}/{key}.png`；工件记录 image 尺寸元数据 |
| P0-7 | **Insight + InsightVersion 实体**：Insight 为稳定身份（可编辑 name/status），InsightVersion 为不可变版本（结论 + 适用范围 + 证据引用 + 方法引用 + 置信说明 + 限制条件 + 证据来源标签 + AI 原稿 + 修改标记 + 来源 Run）。版本号递增 | InsightVersion 创建后不可修改；6 个必填字段全部存在（conclusion/scope/evidence_refs/method_refs/confidence_level/limitations）；evidence_source_label 取值为 `experimental_data` / `knowledge_base` / `model_inference` |
| P0-8 | **InsightCandidate 实体与生成**：Run 执行过程中，AI 产生的结构化 Insight 候选保存为 InsightCandidate，包含全部 6 个必填字段 + evidence_source_label + AI 原稿文本。候选状态为 pending/accepted/modified/rejected | 候选由 Orchestrator 在 LLM/混合步骤完成后提取（通过 ModelGateway 的 INSIGHT 任务类型，提示 AI 输出结构化 JSON）；AI 自然语言对话回答不自动成为候选；候选记录关联 run_id 和 step_id |
| P0-9 | **Insight 接受/修改/拒绝**：用户对候选 Insight 执行三种操作。接受：创建 Insight + InsightVersion v1（is_modified=false，保留 AI 原稿）。修改：创建 Insight + InsightVersion v1（is_modified=true，记录修改内容和 AI 原稿）。拒绝：标记候选为 rejected | 接受后候选 status=accepted，记录 accepted_insight_id；修改后候选 status=modified，记录 accepted_insight_id，InsightVersion 保留 ai_original_text + modification_note；拒绝后候选 status=rejected，不创建 Insight |
| P0-10 | **候选产物预览区增强**：中栏候选输出预览区（阶段 2 已预留基础缩略卡片）增强为三种候选类型的结构化预览。候选数据：显示三段式结构摘要（metadata 关键字段、points 指标列表、series 表格前 5 行）+ 字段清单 + "确认"按钮。候选图表：显示 PNG 缩略图 + 绑定信息 + "确认"按钮。候选 Insight：显示 6 个结构化字段 + 证据来源标签 + "接受"/"修改"/"拒绝"按钮 | 预览区在 Run 完成后自动填充候选产物列表；每种候选类型有对应预览组件；确认/接受/修改/拒绝操作后实时更新预览区状态 |
| P0-11 | **数据编辑规则**：标题（name）、摘要（summary）、标签（tags）、图注（caption，仅 View）、展示顺序（display_order，仅 View）可编辑。编辑仅作用于 stable identity 实体，不触碰 version 内容。points/series 中的数值和行列内容不能无痕手改 | 编辑 API 仅接受 stable identity 的元数据字段更新；尝试修改 version 内容的请求被拒绝；API 层和 Service 层双重校验 |
| P0-12 | **View 绑定规则**：View 必须绑定具体数据版本（bound_dataset_version_id，可空表示不绑定特定数据集）、绘图代码（chart_code_artifact_id）和 Analysis Run（source_run_id）。调整坐标/配色/标注通过 AI 重新生成，形成新 View 版本 | ViewVersion 记录全部绑定引用；不存在脱离 Run 的孤立 View；调整图表通过发起新 Run 步骤或 AI 重绘请求实现，生成新 ViewVersion |
| P0-13 | **ResearchCatalog 部分实现**：ResearchCatalog 从阶段 1 的空占位升级为可搜索当前用户已确认的 Derived Dataset（status=confirmed）。支持关键词搜索和按 Workspace 筛选。返回 DerivedDataset 摘要（id/name/current_version/workspace_id/owner） | 搜索结果仅包含当前用户拥有的已确认 DerivedDataset（跨 Workspace）；ResearchCatalogStub 替换为 ResearchCatalogImpl，调用 ResearchRepository；接口签名与阶段 1 占位一致 |
| P0-14 | **WorkspaceEvidenceRef 支持 research:derived**：WorkspaceEvidenceRef 的 source_namespace 扩展支持 `research:derived`。加入 Derived Dataset 作为证据时记录 dataset_id 和 version_number。证据快照冻结时捕获 DerivedDatasetVersion 的 content_hash | 加入 `research:derived` 证据时通过 ResearchCatalog 校验归属和版本；快照 source_refs 中增加 `{namespace: "research:derived", id: dataset_id, version: version_number}`；快照内容哈希计算包含 DerivedDataset 数据 |
| P0-15 | **产物列表**：Workspace 内展示已确认的全部研究产物（DerivedDataset/ResearchView/Insight），按类型分组，支持查看详情和版本历史 | 产物列表 API 返回当前 Workspace 的全部产物（含类型/名称/状态/当前版本号）；前端按类型分组展示；点击可查看版本历史和详情 |
| P0-16 | **权限继承**：研究产物继承其来源 Evidence Snapshot 的权限包络。产物的有效可见范围不超过来源数据权限交集。`research:use` 权限控制产物创建和管理操作 | 创建产物时记录 source_snapshot_id，继承其 permission_envelope；无 `research:use` 权限的用户无法创建/修改产物；ResearchCatalog 搜索结果仅返回当前用户有权的产物 |
| P0-17 | **审计事件**：DerivedDataset 创建/版本更新/元数据编辑；ResearchView 创建/版本更新/元数据编辑；Insight 创建/版本更新/元数据编辑；InsightCandidate 接受/修改/拒绝均产生审计记录 | 审计记录包含操作类型（如 `research.derived_dataset.create`）、操作者、时间、关联对象 ID；不含大体积数据内容 |
| P0-18 | **DerivedDatasetVersion 不可变保证**：DerivedDatasetVersion / ResearchViewVersion / InsightVersion 创建后不允许 UPDATE / DELETE。修正正式内容产生新版本（v2, v3...），旧版本保留 | 应用层拦截 UPDATE/DELETE 操作并拒绝；版本号严格递增；旧版本在版本历史中始终可查 |

### P1 — Should Have

| ID | 需求 | 验收标准 |
|----|------|---------|
| P1-1 | **PDF 导出**：静态图可选导出 PDF 格式。沙箱步骤可同时输出 PNG 和 PDF，或后续从 PNG 转换 | PDF 工件作为 RunArtifact 存储；ResearchViewVersion 的 image_format 支持 `pdf`；查看时可下载 PDF |
| P1-2 | **人工数据修订记录**：必要的 points/series 人工修订要作为显式步骤记录修改前后差异、理由和操作者。修订产生新 DerivedDatasetVersion | 修订 API 要求提供 revision_reason；新版本记录 before/after diff；审计事件记录操作者和理由；不直接覆盖旧版本 |
| P1-3 | **Insight 修改保留 AI 原稿**：用户修改 Insight 时，系统保留 AI 原稿（ai_original_text）并记录修改内容。每次修改产生新 InsightVersion | InsightVersion 记录 ai_original_text（从候选复制）和用户修改后的正式内容；is_modified=true；modification_note 记录修改原因 |
| P1-4 | **View 通过 AI 重新生成**：用户通过 AI 助手请求调整图表坐标/配色/标注时，系统发起新的沙箱步骤执行重绘，生成新 ViewVersion | 重新生成通过 AI 对话触发（"把 Y 轴改为对数刻度"）→ Orchestrator 创建新步骤执行 → 生成新 PNG → 创建新 ViewVersion；旧版本保留 |
| P1-5 | **产物元数据编辑 UI**：前端提供编辑面板，支持修改 DerivedDataset 的 name/summary/tags、ResearchView 的 name/caption/display_order、Insight 的 name | 编辑面板在产物详情中可用；保存后实时更新列表和详情；tags 支持添加/删除 |
| P1-6 | **候选 Insight 提取提示**：ModelGateway 的 INSIGHT 任务类型使用专门的系统提示词，要求 AI 输出包含 6 个必填字段的结构化 JSON，而非自由文本 | 提示词版本记录在 ModelGateway 调用元数据中；AI 输出格式校验不通过时标记候选为生成失败并保留 AI 原始文本 |
| P1-7 | **图表渲染失败处理**：图表渲染失败时数据结果可保留，View 不可创建。渲染失败的 chart 工件标记 is_publishable=false（阶段 2 已预留），用户可重新触发渲染 | 渲染失败的工件不出现在候选图表列表中；数据工件不受影响；可重新执行生成图表的步骤 |

### P2 — Nice to Have

| ID | 需求 | 验收标准 |
|----|------|---------|
| P2-1 | **产物搜索**：Workspace 内支持按名称搜索研究产物 | 搜索结果实时过滤 |
| P2-2 | **DerivedDataset 三段式预览渲染**：产物详情中以表格/图表形式渲染 points 和 series 内容，而非仅显示 JSON | points 以指标卡片展示；series 以表格展示（前 N 行 + 分页）；支持基本排序 |
| P2-3 | **图表说明编辑**：ResearchView 的 chart_description 可在 View 创建后通过编辑 caption 补充 | chart_description 在确认时由 AI 生成或用户填写；后续可在 View 详情中编辑 |
| P2-4 | **Insight 证据来源可视化**：Insight 详情中以标签/颜色区分证据来源（实验数据=蓝、知识库=紫、模型推测=橙） | 证据来源标签在候选预览和正式 Insight 详情中均可见；颜色编码一致 |

---

## 4. UI 设计概要

### 4.1 中栏 — 候选产物预览区（阶段 3 增强后）

```
┌──────────────────────────────────────────────────────────┐
│  Run #3  [succeeded]    覆盖率: 100% | LLM阅读率: 100%   │
│  ████████████████████████████████████████████████ 100%   │
│                                                          │
│  ┌─────────── 候选产物 (5) ───────────────────────────┐ │
│  │                                                      │ │
│  │  📊 候选数据 (2)                                     │ │
│  │  ┌──────────────────────────────────────────────┐   │ │
│  │  │ 批次特征提取结果                    [确认]   │   │ │
│  │  │ metadata: 批次曲线特征提取 2026-Q2          │   │ │
│  │  │ points: 平均峰值 18.4 MPa, 峰面积 52.1      │   │ │
│  │  │ series: 批次特征表 (12行 × 4列)             │   │ │
│  │  │ 字段: batch_id, peak, area, status          │   │ │
│  │  │ 来源: Step 2 [succeeded]                     │   │ │
│  │  └──────────────────────────────────────────────┘   │ │
│  │  ┌──────────────────────────────────────────────┐   │ │
│  │  │ 温度梯度统计表                      [确认]   │   │ │
│  │  │ ...                                          │   │ │
│  │  └──────────────────────────────────────────────┘   │ │
│  │                                                      │ │
│  │  📈 候选图表 (2)                                     │ │
│  │  ┌──────────────┐  ┌──────────────────────────┐     │ │
│  │  │  [PNG 缩略图] │  │  [PNG 缩略图]             │     │ │
│  │  │  批次峰值对比 │  │  温度-压力散点图           │     │ │
│  │  │  绑定: 数据v1 │  │  绑定: 数据v2              │     │ │
│  │  │  [确认]      │  │  [确认]                   │     │ │
│  │  └──────────────┘  └──────────────────────────┘     │ │
│  │                                                      │ │
│  │  💡 候选 Insight (1)                                 │ │
│  │  ┌──────────────────────────────────────────────┐   │ │
│  │  │ 证据来源: [实验数据]                          │   │ │
│  │  │ 结论: 批次B-003的峰值异常源于温度波动         │   │ │
│  │  │ 适用范围: 2026-Q2 生产的铝合金批次             │   │ │
│  │  │ 置信度: 中 (单批次验证，需扩大样本)           │   │ │
│  │  │ 限制: 未控制原料批次差异                       │   │ │
│  │  │ 证据: 数据v1(批次特征) | 方法: Step2(Python)  │   │ │
│  │  │                                                │   │ │
│  │  │  [✓ 接受]  [✎ 修改]  [✗ 拒绝]                │   │ │
│  │  └──────────────────────────────────────────────┘   │ │
│  └──────────────────────────────────────────────────────┘ │
│                                                          │
│  ┌─────────── 已确认产物 (3) ─────────────────────────┐ │
│  │  📊 批次特征数据 v1    📈 批次峰值图 v1            │ │
│  │  💡 温度波动结论 v1                                  │ │
│  └──────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────┘
```

**候选数据卡片**：
- 展示 metadata 关键字段（截断）、points 指标列表（name + value + unit）、series 表格摘要（行数 × 列数 + 列名）
- 显示自动推断的 field_manifest（字段名列表）
- 显示来源步骤名称和状态
- "确认"按钮 → 调用创建 DerivedDataset API → 成功后卡片变为"已确认"状态并移入"已确认产物"区

**候选图表卡片**：
- 展示 PNG 缩略图（固定尺寸预览）
- 显示绑定信息（关联数据版本、来源步骤、图表说明）
- "确认"按钮 → 调用创建 ResearchView API → 成功后卡片移入"已确认产物"区

**候选 Insight 卡片**：
- 顶部显示证据来源标签（颜色编码：实验数据=蓝 / 知识库=紫 / 模型推测=橙）
- 展示 6 个结构化字段（结论、适用范围、证据引用、方法引用、置信说明、限制条件）
- 展示 AI 原稿摘要（可展开查看完整原文）
- 三个操作按钮：
  - "接受" → 创建 Insight + v1（保留 AI 原稿，is_modified=false）
  - "修改" → 打开编辑面板，用户修改后创建 Insight + v1（保留 AI 原稿，is_modified=true）
  - "拒绝" → 标记候选为 rejected（可选填写拒绝原因）

**已确认产物区**：
- 列出当前 Workspace 已确认的全部产物（按类型分组）
- 每项显示类型图标、名称、当前版本号
- 点击进入产物详情视图

### 4.2 Insight 修改面板

```
┌──────────────────────────────────────────────────────┐
│  修改 Insight 候选                            [×]    │
├──────────────────────────────────────────────────────┤
│                                                      │
│  AI 原稿 (只读):                                     │
│  ┌──────────────────────────────────────────────┐   │
│  │ "批次B-003的峰值异常(17.8 MPa)源于温度波动   │   │
│  │  范围超出控制限..."                           │   │
│  └──────────────────────────────────────────────┘   │
│                                                      │
│  证据来源: [实验数据 ▾]                               │
│                                                      │
│  结论 *:                                              │
│  ┌──────────────────────────────────────────────┐   │
│  │ 批次B-003的峰值异常源于温度梯度超出工艺窗口   │   │
│  └──────────────────────────────────────────────┘   │
│                                                      │
│  适用范围 *:                                          │
│  ┌──────────────────────────────────────────────┐   │
│  │ 2026-Q2 生产的铝合金批次，温度敏感型配方      │   │
│  └──────────────────────────────────────────────┘   │
│                                                      │
│  证据引用 *:  [批次特征数据 v1] [+]                   │
│  方法引用 *:  [Run#3 Step2 (Python)] [+]              │
│                                                      │
│  置信说明 *:  [中 ▾]  说明: 单批次验证，需扩大样本    │
│  限制条件 *:                                          │
│  ┌──────────────────────────────────────────────┐   │
│  │ 未控制原料批次差异；温度记录间隔为30分钟      │   │
│  └──────────────────────────────────────────────┘   │
│                                                      │
│  修改原因:                                            │
│  ┌──────────────────────────────────────────────┐   │
│  │ 补充了温度敏感型配方的适用范围                │   │
│  └──────────────────────────────────────────────┘   │
│                                                      │
│         [取消]                    [确认修改]          │
└──────────────────────────────────────────────────────┘
```

- AI 原稿始终只读展示，用户不可修改
- 6 个必填字段可编辑（标 * 为必填）
- 证据引用和方法引用以标签形式展示，支持添加/删除
- 修改原因为必填（modification_note）
- 确认后创建 Insight + InsightVersion v1，保留 AI 原稿和修改记录

### 4.3 产物详情视图（Workspace 内）

```
┌──────────────────────────────────────────────────────────┐
│  ◀ 返回    批次特征提取结果 (Derived Dataset)            │
├──────────────────────────────────────────────────────────┤
│                                                            │
│  名称: 批次特征提取结果          [✎ 编辑]                  │
│  摘要: 批次曲线特征提取结果，2026-Q2    [✎ 编辑]           │
│  标签: [峰值分析] [Q2批次]  [+ 添加标签]                   │
│  状态: confirmed | 当前版本: v1                            │
│                                                            │
│  ┌─── 来源 ────────────────────────────────────────────┐  │
│  │  Run: #3 [succeeded]    Step: 2 (批次峰值比较)      │  │
│  │  Artifact: data_2026-08-06_hash8f3a                 │  │
│  │  Evidence Snapshot: snapshot #2                     │  │
│  └────────────────────────────────────────────────────┘  │
│                                                            │
│  ┌─── 版本历史 ───────────────────────────────────────┐  │
│  │  v1 | 2026-08-06 14:32 | 许清楚 | 当前版本          │  │
│  │      content_hash: 8f3a...                           │  │
│  └────────────────────────────────────────────────────┘  │
│                                                            │
│  ┌─── 数据预览 (v1) ───────────────────────────────────┐  │
│  │  📋 metadata                                        │  │
│  │  { "description": "批次曲线特征提取结果",           │  │
│  │    "analysis_scope": "2026-Q2" }                   │  │
│  │                                                      │  │
│  │  📊 points (2)                                       │  │
│  │  ┌──────────────┬───────────┬──────┐                │  │
│  │  │ 平均峰值      │ 18.4      │ MPa  │                │  │
│  │  │ 峰面积均值    │ 52.1      │ -    │                │  │
│  │  └──────────────┴───────────┴──────┘                │  │
│  │                                                      │  │
│  │  📈 series (1) — 批次特征表                          │  │
│  │  字段: batch_id, peak, area, status                  │  │
│  │  ┌─────────┬───────┬───────┬────────┐               │  │
│  │  │ batch_id │ peak  │ area  │ status │               │  │
│  │  ├─────────┼───────┼───────┼────────┤               │  │
│  │  │ B-001    │ 17.8  │ 52.1  │ normal │               │  │
│  │  │ B-002    │ 18.5  │ 48.3  │ normal │               │  │
│  │  │ B-003    │ 22.1  │ 67.8  │ alert  │               │  │
│  │  │ ... (共12行)       [显示全部]      │               │  │
│  │  └─────────┴───────┴───────┴────────┘               │  │
│  │                                                      │  │
│  │  📝 field_manifest                                  │  │
│  │  ┌───────────┬──────┬──────┬────────────────────┐   │  │
│  │  │ 字段名     │ 类型 │ 单位 │ 说明               │   │  │
│  │  ├───────────┼──────┼──────┼────────────────────┤   │  │
│  │  │ batch_id  │ str  │ -    │ 批次编号            │   │  │
│  │  │ peak      │ float│ MPa  │ 峰值压力            │   │  │
│  │  │ area      │ float│ -    │ 峰面积              │   │  │
│  │  │ status    │ str  │ -    │ 质量状态            │   │  │
│  │  └───────────┴──────┴──────┴────────────────────┘   │  │
│  └────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

**ResearchView 详情**：
- 展示当前版本的高分辨率 PNG 图片（可放大查看）
- 展示来源信息（Run/Step/Artifact、image_digest、绑定数据版本）
- 展示图表说明（chart_description）和图注（caption，可编辑）
- 版本历史列表（每个版本有缩略图 + 创建时间）
- 元数据编辑按钮（name/caption/display_order）

**Insight 详情**：
- 展示当前版本的结构化字段（结论/适用范围/证据引用/方法引用/置信说明/限制条件）
- 展示证据来源标签
- 展示 AI 原稿（只读）和修改记录（如果有修改）
- 版本历史列表
- 元数据编辑按钮（name）

### 4.4 左栏 — Evidence Set 扩展（支持 Derived Dataset 作为证据）

```
┌──────────┐
│ Evidence │
│   Set    │
│          │
│ 🔍 搜索  │
│          │
│ ──── 类型 ─── │
│ ○ 实验事实 │
│ ● 衍生数据 │
│          │
│ ──── 已选证据 ─ │
│ ✓ Fact-A  │
│   v3 权限 │
│ ✓ Fact-B │
│   v1 权限 │
│ ✓ 衍生:  │
│   批次特征 │
│   v1 权限 │
│          │
│ [冻结快照] │
└──────────┘
```

- 搜索区新增类型筛选：实验事实（Fact）/ 衍生数据（Derived Dataset）
- 选择"衍生数据"时调用 ResearchCatalog 搜索当前用户已确认的 DerivedDataset
- 已选证据列表中 Derived Dataset 显示"衍生:"前缀 + 名称 + 版本号 + 权限状态

---

## 5. 待确认问题

| # | 问题 | 影响范围 | 建议 |
|---|------|---------|------|
| Q1 | **Insight 候选提取时机**：InsightCandidate 在 Run 执行过程中逐步提取（每步 LLM/混合步骤完成后立即提取），还是在 Run 全部完成后统一提取？逐步提取可让用户在 Run 进行中就看到候选，但可能产生不完整的候选；统一提取更完整但延迟反馈。 | P0-8, P0-10 | 建议逐步提取：LLM/混合步骤完成后立即通过 ModelGateway INSIGHT 任务类型提取结构化候选。Run 结束后用户看到全部候选。这样用户在 Run 进行中就能预览已产生的候选。 |
| Q2 | **DerivedDatasetVersion 三段式数据的存储方式**：三段式数据（metadata/points/series）以 JSONB 直接存储在 PostgreSQL，还是大体积 series 存储到 MinIO 仅在 DB 存路径？JSONB 便于查询和预览但大表可能影响性能。 | P0-1, P0-3 | 建议 JSONB 直接存储在 PostgreSQL（与 Fact 详情结构一致），单条 DerivedDatasetVersion 限制 series 总行数（如 ≤10000 行）以控制体积。超出限制的工件提示用户截断或分拆。 |
| Q3 | **View 重新生成的编排方式**：用户通过 AI 对话请求调整图表时，是否需要走完整的 Analysis Run 流程（新建 Run + DAG 步骤），还是可以复用原 Run 的沙箱容器在保温窗口内直接执行重绘步骤？ | P1-4, P0-12 | 建议区分两种场景：(1) 保温窗口内（3 分钟）的快速调整可直接复用容器执行重绘，结果作为新 ViewVersion；(2) 超出保温窗口或涉及数据重新计算的调整需新建 Run 步骤。两种方式都保留完整代码和环境记录。 |
| Q4 | **ResearchCatalog 搜索范围**：阶段 3 的 ResearchCatalog 搜索范围是当前用户拥有的全部 Workspace 中的已确认 DerivedDataset，还是仅限当前 Workspace？跨 Workspace 支持知识积累，但可能增加权限复杂度。 | P0-13, P0-14 | 建议搜索范围为当前用户拥有的全部 Workspace 中的已确认 DerivedDataset（跨 Workspace），不做跨用户搜索（跨用户搜索和 ACL 过滤在阶段 4 发布后实现）。 |
| Q5 | **field_manifest 类型推断的精确度**：自动推断字段类型使用简单 Python 类型推断（int/float/str/bool），还是支持更细粒度的科学数据类型（如 datetime、category）？ | P0-1, P0-3 | 建议首期使用简单类型推断（int/float/str/bool/null），辅以列顺序和基本形状（行数/列数）。更细粒度的科学类型推断在后续阶段增强。 |
| Q6 | **产物与 Evidence Snapshot 的权限继承实现**：产物记录 source_snapshot_id 并继承其 permission_envelope，但 Snapshot 的 permission_envelope 是阶段 1 冻结时的快照。如果源数据权限在产物创建后收紧，产物的有效权限是否需要动态校验？ | P0-16 | 建议产物同时记录静态 permission_envelope（来自 Snapshot）和在发布/使用时动态校验源数据当前权限（阶段 4 发布时完整实现权限包络交集校验）。阶段 3 产物创建时记录来源，不做动态收紧。 |

---

## 6. 技术实现要点（供架构师参考）

### 6.1 后端包结构

```
packages/research/
├── entities.py          # ORM: 新增 ResearchDerivedDataset / ResearchDerivedDatasetVersion /
│                        #      ResearchView / ResearchViewVersion /
│                        #      ResearchInsight / ResearchInsightVersion /
│                        #      ResearchInsightCandidate
├── models.py            # 数据类: DerivedDatasetRef / DatasetVersionRef / DatasetDetail /
│                        #        ViewRef / ViewVersionRef / ViewDetail /
│                        #        InsightRef / InsightVersionRef / InsightDetail /
│                        #        InsightCandidateRef / CandidateProductSummary /
│                        #        ThreeSegmentData / FieldManifestEntry
├── repository.py        # 数据访问层: 扩展 ResearchRepository 新增产物 CRUD 方法
├── products.py          # 业务编排: ProductService（DerivedDataset/View/Insight 生命周期管理）
├── candidates.py        # 业务编排: CandidateService（候选产物识别 + 预览数据组装）
├── insight_extractor.py # Insight 候选提取: 从 LLM 响应提取结构化候选
├── catalog.py           # ResearchCatalog: 从 Stub 升级为 Impl（搜索已确认 DerivedDataset）
└── ...（阶段 1-2 已有文件保持不变）
```

### 6.2 API 路由

```
apps/api/routers/research_products.py
├── # ── 候选产物 ──
├── GET    /api/v1/research/workspaces/{id}/runs/{run_id}/candidates
│         # 列出 Run 的全部候选产物（data 工件 + chart 工件 + insight 候选）
│
├── # ── Derived Dataset ──
├── POST   /api/v1/research/workspaces/{id}/derived-datasets
│         # 从 RunArtifact 创建 DerivedDataset（body: {artifact_id, name, summary, tags}）
├── GET    /api/v1/research/workspaces/{id}/derived-datasets
│         # 列出 Workspace 内 DerivedDataset
├── GET    /api/v1/research/workspaces/{id}/derived-datasets/{dataset_id}
│         # DerivedDataset 详情（含当前版本数据预览）
├── PATCH  /api/v1/research/workspaces/{id}/derived-datasets/{dataset_id}
│         # 编辑元数据（name/summary/tags）
├── GET    /api/v1/research/workspaces/{id}/derived-datasets/{dataset_id}/versions
│         # 版本历史列表
├── GET    /api/v1/research/workspaces/{id}/derived-datasets/{dataset_id}/versions/{version_number}
│         # 版本详情（含三段式数据 + field_manifest）
│
├── # ── ResearchView ──
├── POST   /api/v1/research/workspaces/{id}/views
│         # 从 RunArtifact 创建 ResearchView（body: {artifact_id, name, caption, display_order}）
├── GET    /api/v1/research/workspaces/{id}/views
│         # 列出 Workspace 内 ResearchView
├── GET    /api/v1/research/workspaces/{id}/views/{view_id}
│         # ResearchView 详情（含当前版本图片 URL）
├── PATCH  /api/v1/research/workspaces/{id}/views/{view_id}
│         # 编辑元数据（name/caption/display_order）
├── GET    /api/v1/research/workspaces/{id}/views/{view_id}/versions
│         # 版本历史列表
├── GET    /api/v1/research/workspaces/{id}/views/{view_id}/versions/{version_number}
│         # 版本详情（含图片 URL + 绑定信息）
├── GET    /api/v1/research/workspaces/{id}/views/{view_id}/versions/{version_number}/image
│         # 下载图片（PNG/PDF）
│
├── # ── Insight ──
├── GET    /api/v1/research/workspaces/{id}/insights
│         # 列出 Workspace 内 Insight
├── GET    /api/v1/research/workspaces/{id}/insights/{insight_id}
│         # Insight 详情（含当前版本结构化字段 + AI 原稿 + 修改记录）
├── PATCH  /api/v1/research/workspaces/{id}/insights/{insight_id}
│         # 编辑元数据（name）
├── GET    /api/v1/research/workspaces/{id}/insights/{insight_id}/versions
│         # 版本历史列表
│
├── # ── Insight Candidate ──
├── GET    /api/v1/research/workspaces/{id}/runs/{run_id}/insight-candidates
│         # 列出 Run 的 Insight 候选
├── GET    /api/v1/research/workspaces/{id}/runs/{run_id}/insight-candidates/{candidate_id}
│         # 候选详情
├── POST   /api/v1/research/workspaces/{id}/runs/{run_id}/insight-candidates/{candidate_id}/accept
│         # 接受候选 → 创建 Insight + v1
├── POST   /api/v1/research/workspaces/{id}/runs/{run_id}/insight-candidates/{candidate_id}/modify
│         # 修改候选 → 创建 Insight + v1（body: 修改后的字段 + modification_note）
├── POST   /api/v1/research/workspaces/{id}/runs/{run_id}/insight-candidates/{candidate_id}/reject
│         # 拒绝候选（body: {reason?}）
│
├── # ── 产物列表 ──
├── GET    /api/v1/research/workspaces/{id}/products
│         # 列出 Workspace 全部已确认产物（按类型分组）
│
├── # ── ResearchCatalog（升级） ──
├── GET    /api/v1/research/catalog/search
│         # 搜索当前用户已确认 DerivedDataset（query, workspace_id?）
```

路由注册在 `apps/api/main.py` 中受功能开关控制（延续阶段 1-2 模式）。

### 6.3 前端结构

```
apps/web/src/features/research/
├── ...（阶段 1-2 已有组件保持不变）
├── CandidatePreviewPanel.tsx    # 中栏候选产物预览区（增强版）
├── CandidateDataCard.tsx       # 候选数据卡片（三段式摘要 + 字段清单 + 确认按钮）
├── CandidateChartCard.tsx      # 候选图表卡片（PNG 缩略图 + 绑定信息 + 确认按钮）
├── CandidateInsightCard.tsx    # 候选 Insight 卡片（结构化字段 + 证据来源标签 + 操作按钮）
├── InsightModifyModal.tsx      # Insight 修改面板（AI 原稿只读 + 字段编辑 + 修改原因）
├── ConfirmedProductsPanel.tsx  # 已确认产物列表（按类型分组）
├── ProductDetailView.tsx       # 产物详情视图（Workspace 内）
├── DatasetPreview.tsx          # DerivedDataset 三段式数据预览组件
├── ViewPreview.tsx             # ResearchView 静态图预览组件
├── InsightDetailView.tsx       # Insight 详情组件
├── EvidencePanel.tsx           # 左栏扩展：支持 Derived Dataset 类型筛选和搜索
└── api/
    └── researchProducts.ts     # 产物相关 API 函数
```

### 6.4 数据库表设计概要

**research_derived_dataset**
- `id` (UUID PK), `workspace_id` (FK→research_workspace CASCADE), `owner_user_id` (FK→app_user), `name` (TEXT), `summary` (TEXT nullable), `tags` (JSONB default '[]'), `status` (TEXT: draft/confirmed, default 'draft'), `current_version` (INT default 0), `source_run_id` (FK→research_analysis_run), `source_snapshot_id` (UUID, 逻辑引用 EvidenceSnapshot), `created_at`, `updated_at`, `lock_version`

**research_derived_dataset_version**
- `id` (UUID PK), `dataset_id` (FK→research_derived_dataset CASCADE), `version_number` (INT), `metadata_content` (JSONB), `points_content` (JSONB), `series_content` (JSONB), `field_manifest` (JSONB), `source_run_id` (FK→research_analysis_run), `source_step_id` (FK→research_analysis_step nullable), `source_artifact_id` (FK→research_run_artifact nullable), `content_hash` (TEXT), `created_at`, `created_by` (FK→app_user)
- 不可变：创建后不允许 UPDATE/DELETE
- 唯一约束：`UNIQUE (dataset_id, version_number)`

**research_view**
- `id` (UUID PK), `workspace_id` (FK→research_workspace CASCADE), `owner_user_id` (FK→app_user), `name` (TEXT), `caption` (TEXT nullable), `display_order` (INT default 0), `status` (TEXT: draft/confirmed, default 'draft'), `current_version` (INT default 0), `source_run_id` (FK→research_analysis_run), `created_at`, `updated_at`, `lock_version`

**research_view_version**
- `id` (UUID PK), `view_id` (FK→research_view CASCADE), `version_number` (INT), `image_storage_path` (TEXT), `image_format` (TEXT: png/pdf), `image_width` (INT nullable), `image_height` (INT nullable), `image_content_hash` (TEXT), `chart_code_artifact_id` (FK→research_run_artifact nullable), `image_digest` (TEXT), `source_run_id` (FK→research_analysis_run), `source_step_id` (FK→research_analysis_step nullable), `source_artifact_id` (FK→research_run_artifact nullable), `bound_dataset_version_id` (UUID nullable, 逻辑引用 DerivedDatasetVersion), `chart_description` (TEXT nullable), `created_at`, `created_by` (FK→app_user)
- 不可变：创建后不允许 UPDATE/DELETE
- 唯一约束：`UNIQUE (view_id, version_number)`

**research_insight**
- `id` (UUID PK), `workspace_id` (FK→research_workspace CASCADE), `owner_user_id` (FK→app_user), `name` (TEXT), `status` (TEXT: draft/confirmed, default 'draft'), `current_version` (INT default 0), `source_run_id` (FK→research_analysis_run nullable), `created_at`, `updated_at`, `lock_version`

**research_insight_version**
- `id` (UUID PK), `insight_id` (FK→research_insight CASCADE), `version_number` (INT), `conclusion` (TEXT), `scope` (TEXT), `evidence_refs` (JSONB: [{type, id, version}]), `method_refs` (JSONB: [{run_id, step_id, artifact_id}]), `confidence_level` (TEXT), `limitations` (TEXT), `evidence_source_label` (TEXT: experimental_data/knowledge_base/model_inference), `ai_original_text` (TEXT nullable), `is_modified` (BOOLEAN default false), `modification_note` (TEXT nullable), `source_candidate_id` (UUID nullable, 逻辑引用 InsightCandidate), `source_run_id` (FK→research_analysis_run nullable), `created_at`, `created_by` (FK→app_user)
- 不可变：创建后不允许 UPDATE/DELETE
- 唯一约束：`UNIQUE (insight_id, version_number)`

**research_insight_candidate**
- `id` (UUID PK), `workspace_id` (FK→research_workspace CASCADE), `run_id` (FK→research_analysis_run CASCADE), `step_id` (FK→research_analysis_step nullable), `conclusion` (TEXT), `scope` (TEXT), `evidence_refs` (JSONB), `method_refs` (JSONB), `confidence_level` (TEXT), `limitations` (TEXT), `evidence_source_label` (TEXT), `ai_raw_text` (TEXT), `status` (TEXT: pending/accepted/modified/rejected, default 'pending'), `accepted_insight_id` (UUID nullable), `rejection_reason` (TEXT nullable), `created_at`, `reviewed_at` (UTCDateTime nullable), `reviewed_by` (FK→app_user nullable)

> **注意**：7 张新表均以 `research_` 前缀命名，延续 `research_*` 命名空间。迁移编号延续 `0076`（阶段 1 为 `0074`，阶段 2 为 `0075`）。核心表无到研究产物表的外键。研究产物表到 `research_workspace` / `research_analysis_run` / `research_analysis_step` / `research_run_artifact` 的 FK 允许保留（同为研究域内部表）。

### 6.5 权限集成

延续阶段 1-2 的权限模型，阶段 3 **不新增权限点**：

- `research:use`：控制产物创建和管理操作（已在阶段 1 定义并分配给 `lab_director` / `lab_member`）
- `research:publish`：发布成果包权限（阶段 4 使用，阶段 3 不涉及）

权限继承机制：
- 产物创建时记录 `source_snapshot_id`（逻辑引用），继承 Evidence Snapshot 的 `permission_envelope`
- ResearchCatalog 搜索结果仅返回当前用户拥有的 DerivedDataset（通过 `owner_user_id` 过滤）
- 产物管理 API 使用 `require_permission("research:use")` 依赖

### 6.6 审计事件命名

| 操作 | action 字符串 | resource_type |
|------|--------------|---------------|
| 创建 DerivedDataset | `research.derived_dataset.create` | `research_derived_dataset` |
| DerivedDataset 新版本 | `research.derived_dataset.version` | `research_derived_dataset_version` |
| 编辑 DerivedDataset 元数据 | `research.derived_dataset.edit` | `research_derived_dataset` |
| 创建 ResearchView | `research.view.create` | `research_view` |
| ResearchView 新版本 | `research.view.version` | `research_view_version` |
| 编辑 ResearchView 元数据 | `research.view.edit` | `research_view` |
| 创建 Insight | `research.insight.create` | `research_insight` |
| Insight 新版本 | `research.insight.version` | `research_insight_version` |
| 编辑 Insight 元数据 | `research.insight.edit` | `research_insight` |
| Insight 候选接受 | `research.insight_candidate.accept` | `research_insight_candidate` |
| Insight 候选修改 | `research.insight_candidate.modify` | `research_insight_candidate` |
| Insight 候选拒绝 | `research.insight_candidate.reject` | `research_insight_candidate` |

### 6.7 与阶段 2 的集成点

| 阶段 2 组件 | 阶段 3 集成方式 |
|------------|---------------|
| ResearchRunArtifact（工件表） | 候选产物来源：`artifact_type=data` + `is_publishable=true` → 候选 DerivedDataset；`artifact_type=chart` + `is_publishable=true` → 候选 ResearchView |
| ResearchOrchestrator | Insight 候选提取：LLM/混合步骤完成后，通过 InsightExtractor 调用 ModelGateway INSIGHT 任务类型提取结构化候选并保存为 InsightCandidate |
| RunArtifactService | 产物创建时读取工件内容（MinIO 下载），解析三段式数据或图片元数据 |
| ModelGateway | INSIGHT 任务类型用于结构化候选提取（新增专门的系统提示词，要求输出 6 字段 JSON） |
| AnalysisRunService | `check_publish_eligibility` 用于校验候选产物的依赖闭包完整性 |
| ResearchScheduler | 不涉及新增集成（产物管理为同步操作，不占用沙箱槽位） |
| ResearchMemoryService | 产物确认事件更新研究记忆文档（`insight.accepted` / `insight.rejected` 事件已在阶段 2 预留） |

### 6.8 候选产物识别逻辑

```
Run 完成后，CandidateService.identify_candidates(run_id) 逻辑：

1. 查询 research_run_artifact WHERE run_id = ? AND is_publishable = true
2. 对 artifact_type = 'data' 的工件：
   - 下载工件内容（从 MinIO）
   - 尝试解析为三段式结构（metadata/points/series）
   - 校验通过 → 标记为候选 DerivedDataset
   - 校验失败 → 标记为不可用候选（显示原因）
3. 对 artifact_type = 'chart' 的工件：
   - 读取工件元数据（格式、尺寸）
   - 标记为候选 ResearchView
4. 查询 research_insight_candidate WHERE run_id = ? AND status = 'pending'
5. 汇总返回 CandidateProductSummary 列表（含类型、来源步骤、预览数据）
```

### 6.9 Insight 候选提取逻辑

```
Orchestrator._execute_step 完成后（method=llm 或 mixed）：

1. 检查步骤是否产生潜在 Insight（AI 响应中包含结论性陈述）
2. 调用 ModelGateway.call(task_type=INSIGHT, system_prompt=INSIGHT_EXTRACTION_PROMPT,
   data_context=步骤输出摘要, research_context=主问题+计划+已完成步骤)
3. AI 返回结构化 JSON（6 字段 + evidence_source_label）或 null（无 Insight）
4. 解析成功 → 创建 InsightCandidate（status=pending）
5. 解析失败 → 保留 AI 原始文本，创建 InsightCandidate（标记为生成失败，用户仍可查看原文）
6. 发布 SSE 事件通知前端有新候选
```

`INSIGHT_EXTRACTION_PROMPT` 要求 AI 输出如下 JSON：
```json
{
  "conclusion": "结论文本",
  "scope": "适用范围文本",
  "evidence_refs": [{"type": "dataset", "name": "...", "version": 1}],
  "method_refs": [{"run_id": "...", "step_key": "..."}],
  "confidence_level": "high|medium|low|说明文本",
  "limitations": "限制条件文本",
  "evidence_source_label": "experimental_data|knowledge_base|model_inference",
  "ai_raw_text": "AI 原始回答文本"
}
```
