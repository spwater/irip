# PRD: 发布与复用（子项目 4）

> **项目名称**: irip_research_publish
>
> **编程语言/技术栈**: 后端 Python 3.12+ / FastAPI / SQLAlchemy(异步) / PostgreSQL 16 / Redis 7 / Celery；前端 React 18 + TS / Vite / Ant Design 5 / TanStack Router+Query
>
> **日期**: 2026-08-06
>
> **状态**: 评审稿
>
> **依赖基线**: 阶段 1"研究域基础" + 阶段 2"可信执行" + 阶段 3"研究产物"已完成并上线（`docs/prd-research-foundation.md` / `docs/arch-research-foundation.md` / `docs/prd-research-trusted-execution.md` / `docs/arch-research-trusted-execution.md` / `docs/prd-research-products.md` / `docs/arch-research-products.md`）

---

## 0. 原始需求复述

IRIP "研究分析与发布成果"模块设计方案（`docs/superpowers/specs/2026-08-05-research-analysis-and-publication-design.md`）建议拆为 5 个子项目分阶段建设。本期交付**第 4 个子项目"发布与复用"**，在阶段 1-3 已交付的 Workspace、证据快照、Analysis Run、候选产物管理、DerivedDataset/ResearchView/Insight 确认产物之上，实现"确认产物 → 组装成果包 → 发布版本 → 搜索发现 → 引用复用"的完整闭环。

**阶段 1 已交付基线**：
- Workspace 创建/列表/归档/删除/分叉
- ResearchQuestionVersion 研究问题版本管理
- WorkspaceEvidenceRef 数据引用管理（`source_namespace` 支持 `core:fact`）
- ResearchEvidenceSnapshot 证据快照冻结（SHA-256 哈希 + 权限包络 + 字段清单）
- CoreFactProvider 只读适配 + ResearchCatalog 接口占位
- 功能开关 + `research:use` 权限
- 前端三栏布局

**阶段 2 已交付基线**：
- AnalysisPlanVersion 不可变计划版本 + 计划级授权
- AnalysisRun 后台持久运行 + DAG 步骤编排（ResearchOrchestrator）
- ResearchAnalysisStep 步骤状态管理
- ResearchRunArtifact 工件表（`is_publishable` 标记）
- ModelGateway 模型网关 + ContextRouter 上下文路由 + 500K 预算
- SandboxRuntime 沙箱执行 + ResearchScheduler 20 用户公平调度
- ResearchMemoryService 后台研究记忆
- AIConversationService AI 对话持久化
- 前端右栏 AI 助手 + 中栏 Run 进度 + 排队 UI + 候选输出预览区

**阶段 3 已交付基线**：
- DerivedDataset + DerivedDatasetVersion（三段式 metadata/points/series + field_manifest，版本不可变）
- ResearchView + ResearchViewVersion（静态 PNG/PDF + 绑定数据版本/代码/Run，版本不可变）
- Insight + InsightVersion（6 个结构化字段 + 证据来源标签 + AI 原稿保留，版本不可变）
- InsightCandidate（pending/accepted/modified/rejected 生命周期）
- CandidateService（候选产物识别 + 预览数据组装）
- ProductService（产物生命周期管理）
- ThreeSegmentValidator（三段式校验 + field_manifest 推断 + content_hash 计算）
- ResearchCatalogImpl（搜索当前用户已确认 DerivedDataset，跨 Workspace，owner 过滤）
- WorkspaceEvidenceRef 支持 `research:derived` 命名空间
- 前端候选产物预览区（增强版）+ 产物详情视图 + 元数据编辑
- 7 张 `research_*` 表，迁移编号 0076

**本期范围**：
1. **研究成果包版本**：ResearchResult（稳定身份）+ ResearchResultVersion（不可变发布版本），包含标题/摘要/标签/发布说明 + 多个 DerivedDatasetVersion + 多个 ResearchView + 零或多个 Insight + Evidence Snapshot 和 Analysis Run 引用 + 发布者/发布时间/内容哈希
2. **ACL**：成果包内部对象独立引用但统一继承成果包 ACL；ResultAclRevision 记录权限变更（仅追加）；需要 `research:publish` 权限；权限包络交集校验 + `research:declassify` 放宽授权
3. **搜索**：已发布成果包的跨用户搜索和发现，按当前权限动态过滤
4. **引用**：成果包内部对象（DerivedDataset/View/Insight）的独立引用和详情查看
5. **再次输入**：已发布成果包中的 DerivedDataset 可作为新 Workspace 的证据输入（`research:published` 命名空间），形成 Fact → Derived → Derived 链路延续

**基线约束**：
- 延续阶段 1-3 的模块隔离原则——新模块不反向侵入老系统，所有研究域实体使用 `research_*` 命名空间，关闭或删除新模块后原系统正常工作
- 阶段 4 **不包含**联邦式统一溯源图和受限占位节点（那是阶段 5"统一溯源与知识接口"）。阶段 4 的成果详情页展示来源信息（Workspace/Run/Snapshot/发布者/版本），完整的跨边界联邦溯源在阶段 5 实现
- 代码/API/字段英文，UI 中文
- 发布不需要双人审批，但需要 `research:publish` 权限；发布动作永远由用户主动确认，AI 不能自动完成

---

## 1. 产品目标

| # | 目标 | 衡量标准 |
|---|------|---------|
| G1 | **确认产物到成果包发布的完整链路**：用户在 Workspace 内勾选已确认的 DerivedDataset/ResearchView/Insight，填写标题/摘要/标签/发布说明，选择可见范围后发布为不可变版本；发布前系统校验依赖闭包完整性、权限包络交集和 `research:publish` 权限 | 发布确认页可选择产物、填写元数据、设置 ACL 并展示权限包络校验结果；发布后创建 ResearchResult（如首次）+ ResearchResultVersion（不可变）；版本包含标题/摘要/标签/发布说明 + 包含的产物版本引用 + Snapshot/Run 引用 + 发布者/时间/内容哈希；部分成功 Run 的成果包标注源 Run 状态 |
| G2 | **ACL 与权限包络**：成果包 ACL 沿用 private/tree/explicit/all，默认 private；成果包内部对象统一继承成果包 ACL，不设置独立 ACL；有效可见范围不超过所有源数据当前权限包络的交集；ACL 修改产生独立 ResultAclRevision（仅追加），不产生数据新版本；扩大到交集之外需 `research:declassify` + 理由 + 审计 | 发布时权限包络校验通过后才允许发布；ACL 修改创建新 Revision（revision_number 递增，旧 Revision 保留）；源权限收紧后成果有效可见范围同步收紧；`research:declassify` 操作记录理由和审计事件；搜索结果按当前权限动态过滤 |
| G3 | **已发布成果包的搜索与发现**：已发布成果包可通过发布成果页被有权限的用户检索和发现；支持关键词搜索、多维筛选（发布者/时间/标签/研究问题/数据类型）和收藏 | 发布成果页默认展示当前用户有权查看的全部成果包；搜索结果按当前权限过滤，不依赖创建时静态授权；支持"全部成果/我发布的/我收藏的"切换；支持多维筛选和关键词搜索 |
| G4 | **引用与复用闭环**：成果包内部对象（DerivedDataset/View/Insight）可通过独立 ID 被引用和查看；已发布 DerivedDataset 可作为新 Workspace 的证据输入，形成 Fact → Derived → Derived 知识链路延续 | 成果包内每个产物有独立引用路径；已发布 DerivedDataset 可通过 `research:published` 命名空间加入新 Workspace；证据快照冻结时捕获已发布 DerivedDataset 的版本和内容哈希；"加入当前 Workspace"和"基于此成果新建 Workspace"操作可用 |

---

## 2. 用户故事

**US-1 — 组装并发布研究成果包**
> 作为研究人员，我想在 Workspace 中选择已确认的衍生数据、图表和 Insight，填写标题、摘要、标签和发布说明，选择可见范围后一键发布为研究成果包，以便我的分析成果以版本化、可追溯、可复用的形式发布给团队，而不是散落在各处。

**US-2 — 搜索并发现已发布成果**
> 作为研究人员，我想在发布成果页搜索和浏览平台上有权限查看的全部研究成果包，按发布者、时间、标签和数据类型筛选，以便发现与我的研究相关的已有成果，避免重复劳动并在此基础上继续研究。

**US-3 — 查看成果详情与溯源**
> 作为研究人员，我想查看某个已发布成果包的完整详情，包括包含的数据集、图表、Insight、版本历史、权限状态和来源信息（Workspace、研究问题、Evidence Snapshot、Analysis Run），以便我评估成果的可信度和适用范围。

**US-4 — 复用已发布衍生数据作为新分析输入**
> 作为研究人员，我想将某个已发布成果包中的 DerivedDataset 作为新 Workspace 的证据输入，以便在已有分析结果的基础上进行进一步研究，形成 Fact → Derived → Derived 的知识积累链路。

**US-5 — 管理成果包权限**
> 作为拥有 `research:publish` 权限的研究人员，我想在发布后修改成果包的可见范围（如从 private 改为 tree），或使用 `research:declassify` 在说明理由后突破源数据权限限制，以便在安全可控的范围内分享我的研究成果。

---

## 3. 需求池

### P0 — Must Have

| ID | 需求 | 验收标准 |
|----|------|---------|
| P0-1 | **ResearchResult 实体**：ResearchResult 为稳定身份，可变字段为状态和当前版本号。一个 Workspace 可有多个成果包。创建时状态为 draft，发布后变为 published | `research_research_result` 表：id / workspace_id / owner_user_id / title / summary / tags / status(draft\|published\|archived) / current_version(默认 0) / created_at / updated_at / lock_version；status 为 draft 时可编辑和删除，published 后不可删除 |
| P0-2 | **ResearchResultVersion 不可变发布版本**：发布时创建 ResearchResultVersion，包含标题/摘要/标签/发布说明 + 包含的产物版本引用 + Snapshot/Run 引用 + 发布者/时间/内容哈希。版本号递增，创建后不可 UPDATE/DELETE | `research_research_result_version` 表：id / result_id / version_number / workspace_id / title / summary / tags / release_notes / content_hash / published_by / published_at / status(published\|superseded\|withdrawn) / snapshot_id / source_run_ids(JSONB) / workspace_question / created_at；UNIQUE(result_id, version_number)；不可变（应用层保证） |
| P0-3 | **research_result_item 关联表**：记录成果包版本包含的产物引用，每条记录对应一个产物版本（DerivedDatasetVersion / ResearchViewVersion / InsightVersion） | `research_result_item` 表：id / result_version_id(FK CASCADE) / item_type(derived_dataset\|view\|insight) / item_id(逻辑引用稳定身份) / item_version_id(逻辑引用版本) / item_version_number / item_name(快照) / display_order / created_at |
| P0-4 | **research:publish 权限点**：在 Permission 类中新增 `research:publish`，按角色分配（lab_director / lab_member 拥有，lab_viewer 不拥有）。发布操作要求调用者持有 `research:publish` | 无 `research:publish` 权限的用户无法执行发布操作（API 返回 403）；有权限的用户可正常发布；Workspace 归属者不自动获得 publish 权限，需角色分配 |
| P0-5 | **PublicationService — 成果包草稿管理**：用户可创建成果包草稿，从 Workspace 已确认产物中选择要发布的项，填写标题/摘要/标签。草稿期可增删产物、编辑元数据 | 创建草稿返回 ResearchResult（status=draft）；添加产物时校验产物属于当前 Workspace 且 status=confirmed；同一产物版本不可重复添加；草稿可删除（未发布时） |
| P0-6 | **PublicationService — 发布操作**：用户填写发布说明并选择可见范围后点击"确认发布"，系统执行：(1) 权限校验（research:publish）→ (2) 依赖闭包校验 → (3) 权限包络交集计算 → (4) ACL 校验 → (5) 创建不可变版本 → (6) 创建初始 ACL Revision → (7) 审计事件 | 发布后 ResearchResultVersion 不可变；content_hash 计算包含标题+摘要+标签+发布说明+所有产物版本 ID 及其 content_hash 的排序拼接 SHA-256；发布后 ResearchResult.status 变为 published，current_version 更新；部分成功 Run 的成果包在 source_run_ids 中标注 run_status |
| P0-7 | **成果包至少包含一个产物**：发布时成果包必须包含至少一个 DerivedDatasetVersion 或一个 ResearchView。Insight 可为零个。空成果包不允许发布 | 发布校验失败时返回明确错误信息"成果包至少需要包含一个数据集或图表" |
| P0-8 | **依赖闭包校验**：发布前校验所有选中产物的依赖闭包完整性。来自部分成功 Run 的产物允许发布，但成果包版本中标注源 Run 为 partially_succeeded | 校验通过：所有产物的 source_run_id 对应的 Run 状态为 succeeded 或 partially_succeeded；cancelled 或 failed Run 的产物不允许发布；partially_succeeded Run 标注在 source_run_ids 中 |
| P0-9 | **ACL — 可见范围**：成果包 ACL 沿用 private / tree / explicit / all 四级。发布时默认 private，用户可在发布确认页选择其他范围。成果包内部对象不设置独立 ACL，全部继承成果包权限 | 发布 API 接受 acl_scope 参数；默认为 private；explicit 需提供 explicit_user_ids 列表；ACL 存储在 research_result_acl_revision 表中（revision_number=1 的初始 Revision） |
| P0-10 | **ACL — ResultAclRevision 仅追加**：ACL 修改不产生数据版本，而产生独立 ACL Revision（仅追加，不可修改/删除）。每次修改记录 revision_number（递增）、变更前后 ACL、操作者、时间和原因 | `research_result_acl_revision` 表：id / result_id / revision_number / acl_scope / explicit_user_ids / previous_acl_scope / previous_explicit_user_ids / changed_by / changed_at / change_reason / is_declassify / declassify_reason / source_envelope_snapshot(JSONB) / UNIQUE(result_id, revision_number)；创建后不可 UPDATE/DELETE |
| P0-11 | **ACL — 权限包络交集校验**：发布和 ACL 修改时，有效可见范围 = 请求的 ACL ∩ 所有源数据当前权限包络的交集。超出交集时拒绝操作，除非持有 `research:declassify` 并提供理由 | 计算逻辑：收集成果包内所有产物的源 EvidenceSnapshot 的 permission_envelope → 对每个 envelope 查询源数据当前权限 → 计算交集 → 校验请求的 ACL 在交集内；超出交集且无 declassify 权限时返回 403 并展示当前可用范围 |
| P0-12 | **ACL — 运行期权限动态过滤**：搜索索引和成果列表按当前权限过滤，不依赖创建时的静态授权快照。源权限收紧后，成果的有效可见范围同步收紧 | 搜索/列表 API 在查询时动态计算当前用户对每个成果的有效可见范围；源数据权限已收紧的成果对部分用户变为不可见或标注"权限受限" |
| P0-13 | **已发布成果包搜索**：发布成果页搜索已发布成果包（跨用户），支持关键词搜索（标题/摘要/标签/研究问题）和多维筛选（发布者/时间范围/标签/数据类型/来源 Workspace） | `GET /api/v1/research/publications` 支持分页；搜索结果仅包含当前用户有权查看的成果包（ACL 过滤 + 源数据权限动态校验）；支持 query / publisher / tags / date_from / date_to / data_type / workspace_id 等筛选参数 |
| P0-14 | **发布成果页**：实验室运营"发布成果"Tab 展示已发布成果包列表，支持"全部成果/我发布的/我收藏的"切换和筛选 | 页面默认展示当前用户有权查看的全部已发布成果包；卡片显示标题、发布者、发布时间、标签、包含产物数量和版本号；支持切换"我发布的"和"我收藏的" |
| P0-15 | **成果详情页**：查看已发布成果包的完整详情，沿用 FactDetail 阅读习惯但不复用 Fact 实体。左侧展示来源信息（Workspace、研究问题、源 Fact/Derived、Evidence Snapshot、Analysis Run、发布者、版本），右侧展示 metadata/points/series、View、Insight 和版本历史 Tab | 详情 API 返回成果包当前版本的全部信息 + 版本历史列表 + 当前 ACL；前端左侧展示来源链，右侧按 Tab 展示数据/图表/Insight；无权访问的上游在来源信息中按权限裁剪（完整联邦溯源在阶段 5 实现） |
| P0-16 | **成果包内部对象独立引用**：成果包内每个产物（DerivedDataset/View/Insight）可通过独立 API 路径引用和查看，不需要加载整个成果包 | `GET /api/v1/research/publications/{result_id}/items/{item_type}/{item_id}` 返回该产物的当前版本详情；引用者权限通过成果包 ACL 校验；返回数据格式与阶段 3 产物详情一致 |
| P0-17 | **复用 — 已发布 DerivedDataset 作为新 Workspace 证据**：已发布成果包中的 DerivedDataset 可作为新 Workspace 的证据输入。WorkspaceEvidenceRef 的 source_namespace 扩展支持 `research:published` | 加入 `research:published` 证据时校验成果包 ACL 和源数据当前权限；记录 result_id / result_version_number / dataset_id / dataset_version_number；证据快照冻结时从已发布 DerivedDatasetVersion 获取 content_hash 纳入哈希计算 |
| P0-18 | **复用 — "加入当前 Workspace"和"基于此成果新建 Workspace"**：在成果详情页提供两个操作：将成果包中的 DerivedDataset 加入当前活跃 Workspace，或基于此成果新建 Workspace（继承研究问题和 DerivedDataset 引用） | "加入当前 Workspace"调用 evidence API（source_namespace=research:published）；"基于此成果新建 Workspace"创建新 Workspace + 添加 DerivedDataset 证据引用 + 继承研究问题文本 |
| P0-19 | **版本管理 — superseded / withdrawn**：旧版本可标记为 superseded（被新版本替代）或 withdrawn（撤回），但不物理删除。修正正式内容产生新版本，v1 保留在版本历史中 | supersede 操作将旧版本 status 改为 superseded；withdraw 操作需提供撤回原因，将版本 status 改为 withdrawn；superseded/withdrawn 版本在版本历史中可见但标注状态；不可物理删除 |
| P0-20 | **内容哈希**：ResearchResultVersion 的 content_hash 计算包含标题+摘要+标签+发布说明+所有产物版本 ID 及其 content_hash 的排序拼接 SHA-256，确保发布版本防篡改 | content_hash 在发布时计算并存储；版本详情中展示 content_hash；任意字段篡改后哈希不匹配 |
| P0-21 | **审计事件**：成果包创建/发布/新版本/supersede/withdraw、ACL 修改/权限包络自动收紧/declassify 使用均产生审计记录 | 审计记录包含操作类型（如 `research.result.publish`）、操作者、时间、关联对象 ID；不含大体积数据内容 |
| P0-22 | **不可变保证**：ResearchResultVersion 和 ResultAclRevision 创建后不允许 UPDATE/DELETE。ResearchResultVersion 的 status 字段更新（superseded/withdrawn）仅通过专用 API 操作，不允许直接修改版本内容 | 应用层拦截非法 UPDATE/DELETE 操作并拒绝；Repository 层不提供版本实体和 ACL Revision 的 update/delete 方法；status 变更通过专用 supersede/withdraw API |
| P0-23 | **ResearchCatalog 扩展**：ResearchCatalog 从搜索当前用户已确认 DerivedDataset 升级为支持搜索已发布成果包中的 DerivedDataset（跨用户，ACL 过滤） | 搜索结果包含已发布成果包中的 DerivedDataset（通过 research_result_item 关联到 research_research_result_version，过滤 status=published）；返回结果包含 result_id / result_version_number / dataset_id / dataset_version_number / dataset_name；ACL 过滤确保仅返回当前用户有权查看的成果 |

### P1 — Should Have

| ID | 需求 | 验收标准 |
|----|------|---------|
| P1-1 | **research:declassify 权限点**：在 Permission 类中新增 `research:declassify`，按角色分配（lab_director 拥有，lab_member 不拥有）。用于突破源数据权限包络限制 | 无 `research:declassify` 权限的用户无法执行 declassify 操作；有权限的用户需填写 declassify_reason 才能执行；declassify 操作创建 ACL Revision（is_declassify=true）+ 审计事件 |
| P1-2 | **research:manage 权限点**：在 Permission 类中新增 `research:manage`，按角色分配（lab_director / platform_admin 拥有）。用于撤回他人成果、处理归属和异常内容 | 有 `research:manage` 权限的用户可 withdraw 他人发布的成果包版本；普通用户只能 withdraw 自己的成果包 |
| P1-3 | **ACL 修改 UI**：成果详情页提供 ACL 管理面板，展示当前可见范围和变更历史，支持修改可见范围（创建新 Revision） | 面板展示当前 ACL scope + explicit users 列表 + 变更历史（Revision 列表）；修改时展示权限包络校验结果；declassify 操作有独立入口需填写理由 |
| P1-4 | **成果包收藏**：用户可收藏/取消收藏已发布成果包，发布成果页支持"我收藏的"筛选 | `research_publication_favorite` 表：id / user_id / result_id / created_at；UNIQUE(user_id, result_id)；收藏列表 API 返回用户收藏的成果包 |
| P1-5 | **成果包撤回**：发布者或拥有 `research:manage` 权限的用户可撤回已发布版本，需提供撤回原因。撤回后版本 status 变为 withdrawn，不出现在搜索结果中但保留在版本历史 | 撤回 API 需 body.reason；撤回后搜索 API 不返回该版本；版本历史中标注 withdrawn + 撤回原因 + 操作者 |
| P1-6 | **版本对比**：成果包版本历史中支持查看不同版本之间的差异（新增/移除的产物、元数据变更） | 版本对比 API 返回两个版本之间的产物差异列表和元数据字段差异；前端以差异视图展示 |
| P1-7 | **发布确认页**：前端提供完整的发布确认页，包含基本信息编辑、产物选择（带预览）、权限设置（带包络校验提示）和溯源信息展示 | 发布确认页从 Workspace 产物列表发起；产物选择支持勾选和预览；权限设置实时展示包络校验结果；溯源信息展示 Snapshot 和 Run 摘要 |
| P1-8 | **成果列表卡片增强**：发布成果页的成果卡片展示标题、发布者、发布时间、标签、包含产物类型摘要（数据 N / 图表 N / Insight N）和当前版本号 | 卡片信息从搜索 API 返回的摘要数据渲染；点击卡片进入成果详情页 |
| P1-9 | **Workspace 删除校验增强**：阶段 1 允许无限制删除（无发布成果），阶段 4 增加发布成果引用检查——有已发布成果包的 Workspace 只能归档不能删除 | 删除 API 检查 research_research_result WHERE workspace_id = ? AND status = 'published'；存在则拒绝删除并提示归档 |
| P1-10 | **权限包络自动收紧提示**：源数据权限收紧后，相关成果包在详情页和搜索结果中展示"权限已收紧"提示 | 搜索 API 对源数据权限已变化的成果包标注 `acl_adjusted=true`；详情页展示当前有效可见范围和原始发布范围的差异 |

### P2 — Nice to Have

| ID | 需求 | 验收标准 |
|----|------|---------|
| P2-1 | **语义搜索**：已发布成果包支持基于向量相似度的语义搜索（利用 PostgreSQL pgvector） | 搜索 API 支持 `semantic=true` 参数；返回结果按相似度排序；向量基于标题+摘要+标签构建 |
| P2-2 | **成果包导出**：将成果包版本导出为包含数据、图表和 Insight 的打包文件（JSON + PNG 附件） | 导出 API 返回包含全部产物内容的 JSON + 图片下载链接 |
| P2-3 | **聊天分享成果引用**：在现有聊天中分享动态成果版本引用（非内容复制），接收者权限被撤销后引用显示"无权访问" | 分享生成成果引用链接；引用者通过 ACL 校验后可查看；权限撤销后引用标记为无权 |
| P2-4 | **成果引用格式**：为已发布成果包生成结构化引用文本（类似学术引用格式），支持复制 | 成果详情页展示引用文本（作者、标题、版本、发布时间、平台标识）；支持一键复制 |
| P2-5 | **来源任务筛选**：发布成果页支持按来源任务（源 Fact 所属的实验项目/任务）筛选成果包 | 搜索 API 支持 source_task_id 筛选参数；结果按来源任务过滤 |

---

## 4. UI 设计概要

### 4.1 发布确认页（从 Workspace 产物列表发起）

```
┌──────────────────────────────────────────────────────────────┐
│  发布研究成果包                                       [×]      │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌─── 基本信息 ──────────────────────────────────────────┐  │
│  │  标题 *:  批次峰值差异分析报告                          │  │
│  │  摘要:     2026-Q2 批次间峰值差异来源分析              │  │
│  │  标签:     [峰值分析] [Q2批次] [铝合金]  [+ 添加标签]   │  │
│  │  发布说明: 首次发布，包含批次特征数据和关键结论        │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌─── 选择成果 ──────────────────────────────────────────┐  │
│  │                                                        │  │
│  │  📊 衍生数据 (2/2 已选)                                │  │
│  │  ☑ 批次特征提取结果 v1    来源: Run#3 [succeeded]      │  │
│  │  ☑ 温度梯度统计表 v1      来源: Run#3 [succeeded]      │  │
│  │                                                        │  │
│  │  📈 图表 (2/2 已选)                                    │  │
│  │  ☑ 批次峰值对比图 v1      绑定: 批次特征数据 v1        │  │
│  │  ☑ 温度-压力散点图 v1     绑定: 温度梯度统计表 v1      │  │
│  │                                                        │  │
│  │  💡 Insight (1/1 已选)                                 │  │
│  │  ☑ 温度波动结论 v1        来源: 实验数据              │  │
│  │                                                        │  │
│  │  ⚠ Run#3 状态为 partially_succeeded                    │  │
│  │    发布后将标注源 Run 为部分成功                        │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌─── 权限设置 ──────────────────────────────────────────┐  │
│  │  可见范围:                                             │  │
│  │  ○ 私有 (private)  — 仅自己可见                        │  │
│  │  ● 部门内 (tree)   — 同部门可见                        │  │
│  │  ○ 指定用户 (explicit) — [选择用户...]                 │  │
│  │  ○ 全部 (all)     — 所有人可见                         │  │
│  │                                                        │  │
│  │  📋 权限包络校验:                                      │  │
│  │  源数据权限交集: tree (材料研发部)                    │  │
│  │  当前选择: tree ✓ 在权限包络内                         │  │
│  │  可选范围: private / tree                              │  │
│  │  超出范围需申请 declassify                             │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌─── 溯源信息 ──────────────────────────────────────────┐  │
│  │  来源 Workspace: 批次差异研究                          │  │
│  │  研究问题: 不同批次间峰值差异的来源是什么？            │  │
│  │  Evidence Snapshot: #2 (2026-08-06, hash: 8f3a...)   │  │
│  │  Analysis Run: #3 [partially_succeeded]                │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│                [取消]              [确认发布]                   │
└──────────────────────────────────────────────────────────────┘
```

- **基本信息**：标题必填，摘要/标签/发布说明可选
- **选择成果**：列出当前 Workspace 全部已确认产物（status=confirmed），支持勾选/取消；展示来源 Run 状态；部分成功 Run 标注警告
- **权限设置**：四级可见范围选择；实时展示权限包络校验结果——源数据权限交集、当前选择是否在包络内、可选范围；超出范围时禁用"确认发布"并提示 declassify
- **溯源信息**：展示来源 Workspace、研究问题、Evidence Snapshot 摘要和 Analysis Run 状态
- **确认发布**：调用发布 API，成功后跳转成果详情页

### 4.2 发布成果页（实验室运营"发布成果"Tab）

```
┌──────────────────────────────────────────────────────────────┐
│  实验室运营  [实验项目] [研究分析] [发布成果]                  │
├──────────────────────────────────────────────────────────────┤
│                                                                │
│  发布成果                                                      │
│                                                                │
│  [全部成果] [我发布的] [我收藏的]    [🔍 搜索]                  │
│                                                                │
│  筛选: [发布者▾] [时间范围▾] [标签▾] [数据类型▾]              │
│                                                                │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  批次峰值差异分析报告                     v2 (当前)    │  │
│  │  发布者: 许清楚  |  2026-08-06  |  v2 发布              │  │
│  │  标签: [峰值分析] [Q2批次] [铝合金]                    │  │
│  │  📊 数据×2  📈 图表×2  💡 Insight×1                    │  │
│  │  来源: 批次差异研究 Workspace                           │  │
│  │  权限: tree (部门内可见)                                │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                                │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  温度梯度对铝合金性能的影响              v1 (当前)     │  │
│  │  发布者: 李研究  |  2026-08-05  |  v1 发布              │  │
│  │  标签: [温度] [铝合金] [力学性能]                      │  │
│  │  📊 数据×3  📈 图表×1  💡 Insight×2                    │  │
│  │  权限: explicit (3人可见)                               │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                                │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  ...更多成果包...                                       │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                                │
│  共 15 个成果包  |  第 1 / 3 页  [上一页] [下一页]            │
│                                                                │
└──────────────────────────────────────────────────────────────┘
```

- **Tab 切换**：全部成果（当前用户有权查看的全部）/ 我发布的（当前用户作为 owner）/ 我收藏的（当前用户收藏的）
- **搜索栏**：关键词搜索（匹配标题、摘要、标签、研究问题）
- **筛选器**：发布者（下拉选择）、时间范围（日期选择器）、标签（多选）、数据类型（衍生数据/图表/Insight 存在性）
- **成果卡片**：标题、当前版本号、发布者、发布时间、标签、产物类型摘要（数据 N / 图表 N / Insight N）、来源 Workspace 名称、权限状态
- **分页**：支持分页，每页 20 条
- **功能开关关闭时**：恢复为原"模型发布"占位 Tab

### 4.3 成果详情页

```
┌──────────────────────────────────────────────────────────────────┐
│  ◀ 返回    批次峰值差异分析报告                  v2 (当前)       │
│            [★ 收藏]  [加入当前 Workspace]  [基于此成果新建 WS]   │
├───────────────────────┬──────────────────────────────────────────┤
│  衍生来源              │  📋 metadata | 📊 points | 📈 series    │
│                       │  | 📊 图表 | 💡 Insight | 📜 版本历史    │
│  Workspace:           │                                          │
│   批次差异研究         │  ┌─── 数据预览 (v2) ────────────────┐   │
│                       │  │  📊 DerivedDataset (2)             │   │
│  研究问题:            │  │  ┌──────────────────────────────┐ │   │
│   不同批次间峰值      │  │  │ 批次特征提取结果 v1          │ │   │
│   差异的来源是什么？  │  │ │ metadata: 2026-Q2 特征提取   │ │   │
│                       │  │ │ points: 平均峰值 18.4 MPa    │ │   │
│  Evidence Snapshot:   │  │ │ series: 批次特征表 (12行)    │ │   │
│   #2 (2026-08-06)     │  │ │ [查看详情]                   │ │   │
│   hash: 8f3a...       │  │ └──────────────────────────────┘ │   │
│                       │  │  ┌──────────────────────────────┐ │   │
│  Analysis Run:        │  │  │ 温度梯度统计表 v1            │ │   │
│   #3 [partially_     │  │  │ [查看详情]                   │ │   │
│    succeeded]         │  │ └──────────────────────────────┘ │   │
│                       │  └──────────────────────────────────┘   │
│  发布者: 许清楚       │                                          │
│  发布时间: 2026-08-06 │  ┌─── 图表 (2) ────────────────────┐   │
│  版本: v2             │  │  ┌──────────┐  ┌──────────────┐ │   │
│  内容哈希: a1b2...    │  │  │ [PNG 缩略] │  │ [PNG 缩略]   │ │   │
│                       │  │  │ 批次峰值   │  │ 温度-压力    │ │   │
│  权限状态:            │  │  │ 对比图 v1  │  │ 散点图 v1    │ │   │
│  tree (部门内可见)    │  │  └──────────┘  └──────────────┘ │   │
│  [✎ 修改权限]         │  └──────────────────────────────────┘   │
│                       │                                          │
│  版本历史:            │  ┌─── Insight (1) ─────────────────┐   │
│  v2 | 当前 | 08-06   │  │  证据来源: [实验数据]             │   │
│  v1 | superseded     │  │  结论: 批次B-003峰值异常源于...   │   │
│      | 08-05         │  │  置信度: 中                      │   │
│                       │  │  [查看详情]                       │   │
│                       │  └──────────────────────────────────┘   │
├───────────────────────┴──────────────────────────────────────────┤
│  权限变更历史                                                    │
│  R2 | tree → tree  | 08-06 14:30 | 许清楚 | 无变更               │
│  R1 | → private    | 08-05 10:00 | 许清楚 | 初始发布              │
└──────────────────────────────────────────────────────────────────┘
```

- **左侧"衍生来源"**：展示来源 Workspace、研究问题、Evidence Snapshot、Analysis Run、发布者、发布时间、版本、内容哈希、权限状态和权限变更历史。无权访问的上游按权限裁剪（完整联邦溯源图在阶段 5 实现）
- **右侧 Tab**：
  - **metadata**：成果包级别的标题、摘要、标签和发布说明
  - **points / series**：聚合展示成果包内所有 DerivedDataset 的数据（点击进入单个数据集详情）
  - **图表**：展示成果包内所有 ResearchView 的 PNG 图片
  - **Insight**：展示成果包内所有 Insight 的结构化字段
  - **版本历史**：版本列表（版本号、状态、发布时间、发布说明）
- **顶部操作**：收藏、加入当前 Workspace（将 DerivedDataset 加入活跃 Workspace 证据集）、基于此成果新建 Workspace
- **权限变更历史**：底部展示 ACL Revision 列表（Revision 号、变更前后 ACL、时间、操作者、原因）

### 4.4 ACL 管理面板（成果详情页内）

```
┌──────────────────────────────────────────────────────────┐
│  权限管理                                        [×]      │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  当前可见范围:  tree (部门内可见)                         │
│  指定用户:     (无)                                       │
│                                                          │
│  ── 修改可见范围 ──                                       │
│                                                          │
│  ○ 私有 (private)  — 仅自己可见                           │
│  ● 部门内 (tree)   — 同部门可见                           │
│  ○ 指定用户 (explicit) — [选择用户...]                    │
│  ○ 全部 (all)     — 所有人可见                            │
│                                                          │
│  📋 权限包络校验:                                         │
│  源数据权限交集: tree (材料研发部)                        │
│  当前选择: tree ✓ 在权限包络内                            │
│                                                          │
│  变更原因 (可选): ┌──────────────────────────────────┐   │
│                   └──────────────────────────────────┘   │
│                                                          │
│  ── 超出权限包络 ──                                       │
│  如需扩大到权限包络之外（如 all），需使用 declassify:     │
│  [申请 declassify]                                        │
│                                                          │
│  ── 变更历史 ──                                           │
│  R2 | tree → tree    | 08-06 14:30 | 许清楚               │
│  R1 | → private      | 08-05 10:00 | 许清楚 | 初始发布    │
│                                                          │
│              [取消]              [确认修改]                │
└──────────────────────────────────────────────────────────┘
```

- 展示当前 ACL 和变更历史
- 修改时实时校验权限包络
- 超出包络时禁用"确认修改"并提供 declassify 入口
- declassify 需额外填写理由

### 4.5 Workspace 产物列表增加发布入口

```
┌─────────── 已确认产物 (3) ───────────────────────────┐
│                                                        │
│  📊 数据 (2)                                           │
│  ┌──────────────────────┐  ┌──────────────────────┐  │
│  │ 批次特征提取结果 v1    │  │ 温度梯度统计表 v1     │  │
│  └──────────────────────┘  └──────────────────────┘  │
│                                                        │
│  📈 图表 (2)                                           │
│  ┌──────────────┐  ┌──────────────────────────┐       │
│  │ 批次峰值对比  │  │ 温度-压力散点图           │       │
│  └──────────────┘  └──────────────────────────┘       │
│                                                        │
│  💡 Insight (1)                                        │
│  ┌──────────────────────────────────────────────┐     │
│  │ 温度波动结论 v1                               │     │
│  └──────────────────────────────────────────────┘     │
│                                                        │
│  [📦 发布研究成果包]                                    │
└────────────────────────────────────────────────────────┘
```

- "发布研究成果包"按钮打开发布确认页
- 按钮仅在用户持有 `research:publish` 权限时显示

### 4.6 左栏 Evidence Set 扩展（支持已发布 DerivedDataset）

```
┌──────────┐
│ Evidence │
│   Set    │
│          │
│ 🔍 搜索  │
│          │
│ ──── 类型 ─── │
│ ○ 实验事实 │
│ ○ 我的衍生 │
│ ● 已发布  │
│          │
│ ──── 已选证据 ─ │
│ ✓ Fact-A  │
│   v3 权限 │
│ ✓ 衍生:  │
│   批次特征 │
│   v1 权限 │
│ ✓ 已发布: │
│   温度梯度 │
│   统计 v1 │
│   来源: 成果│
│   包"温度 │
│   影响"v1 │
│   权限: tree│
│          │
│ [冻结快照] │
└──────────┘
```

- 搜索区新增类型筛选：实验事实 / 我的衍生数据 / 已发布成果
- 选择"已发布成果"时调用 ResearchCatalog 搜索已发布 DerivedDataset（跨用户，ACL 过滤）
- 已选证据列表中已发布 DerivedDataset 显示"已发布:"前缀 + 名称 + 版本号 + 来源成果包 + 权限状态

---

## 5. 待确认问题

| # | 问题 | 影响范围 | 建议 |
|---|------|---------|------|
| Q1 | **权限包络交集的动态计算策略**：权限包络需基于源数据当前权限动态计算，但源数据权限可能频繁变化。是每次搜索/访问时实时计算所有源数据权限交集（准确但开销大），还是在发布时缓存交集并在访问时做增量校验？ | P0-11, P0-12 | 建议发布时缓存权限包络快照（source_envelope_snapshot），搜索/列表时基于缓存的包络做快速过滤。对于源数据权限已变化的情况，在详情页访问时做一次实时校验并标注"权限已收紧"。完整实时校验可通过后台异步任务批量刷新。 |
| Q2 | **ResearchResultVersion 的产物引用存储方式**：成果包版本包含的产物引用（DerivedDatasetVersion / ResearchViewVersion / InsightVersion）以独立关联表（research_result_item）存储，还是以 JSONB 数组直接存储在版本表中？独立表便于查询和索引但增加 JOIN；JSONB 简单但不便于单产物查询。 | P0-3 | 建议使用独立关联表（research_result_item），便于按产物类型查询、统计包含产物数量和独立引用查询。产物版本 ID 为逻辑引用（不建 FK 到 DerivedDatasetVersion 表），因为跨表 FK 增加耦合。 |
| Q3 | **已发布 DerivedDataset 作为证据时的权限校验深度**：加入已发布 DerivedDataset 作为证据时，是否需要递归校验该 DerivedDataset 的源数据权限（即 Fact → Derived → 成果包 → 新 Workspace 链路中所有节点的权限），还是仅校验成果包 ACL 和成果包的源 Snapshot 权限包络？ | P0-17 | 建议仅校验成果包 ACL + 成果包源 Snapshot 的权限包络。递归校验在多级 Derived 链路中开销过大且可能导致合理的复用被拒绝。完整的跨边界权限校验在阶段 5 统一溯源中通过受限节点机制处理。 |
| Q4 | **搜索实现方式**：已发布成果包搜索使用 PostgreSQL 全文搜索（tsvector/tsquery），还是使用简单的 ILIKE 模糊匹配？全文搜索性能好但不支持中文分词；ILIKE 简单但大数据量下性能不足。 | P0-13 | 建议首期使用 ILIKE 模糊匹配（标题/摘要/标签/研究问题），配合 PostgreSQL 索引优化。中文全文搜索和语义搜索作为 P2 能力后续增强（pgvector 已在阶段 1 预留）。 |
| Q5 | **同一成果包的版本间产物引用是否允许变更**：v1 包含 DerivedDataset A v1 + View B v1，v2 是否可以移除 A 并新增 DerivedDataset C v1？还是 v2 只能在 v1 基础上新增产物，不能移除？ | P0-2, P0-3 | 建议允许版本间任意增删产物。每个版本独立记录包含的产物引用，版本间不要求产物集合单调递增。版本对比功能（P1-6）展示差异。这符合"修正正式内容产生 v2"的语义——用户可能需要移除有问题的产物或新增补充产物。 |
| Q6 | **ACL 修改是否需要重新校验权限包络**：发布后修改 ACL（如从 private 改为 tree）时，源数据权限可能已变化。是否每次 ACL 修改都需要重新计算权限包络交集？ | P0-10, P0-11 | 建议每次 ACL 修改都重新计算权限包络交集，确保修改后的 ACL 不超出当前源数据权限限制。如果源数据权限已收紧导致目标 ACL 超出包络，拒绝修改并提示当前可选范围。 |
| Q7 | **成果包 owner 转移**：发布者离开团队后，成果包的 owner 如何处理？是否需要 owner 转移机制，还是由 `research:manage` 权限者代管？ | P1-2 | 建议首期不提供 owner 转移功能。`research:manage` 权限者可 withdraw 异常成果包。owner 转移作为后续能力。 |

---

## 6. 技术实现要点（供架构师参考）

### 6.1 后端包结构

```
packages/research/
├── publication.py       # PublicationService — 成果包创建/草稿管理/发布/版本管理/
│                        #   ACL 修改/撤回/supersede/产物选择/权限包络校验
├── acl.py               # AclService — 权限包络计算/ACL Revision 管理/
│                        #   declassify 校验/运行期权限动态过滤
├── result_search.py     # ResultSearchService — 已发布成果包搜索和发现/
│                        #   多维筛选/ACL 过滤/收藏管理
├── reuse.py             # ReuseService — 已发布 DerivedDataset 作为新 Workspace
│                        #   证据输入/ResearchCatalog 扩展搜索
├── entities.py          # ORM: 新增 ResearchResearchResult /
│                        #      ResearchResearchResultVersion /
│                        #      ResearchResultItem /
│                        #      ResearchResultAclRevision /
│                        #      ResearchPublicationFavorite
├── models.py            # 数据类: ResultRef / ResultVersionRef / ResultDetail /
│                        #        ResultItemRef / AclRevisionRef / AclState /
│                        #        PublicationSearchResult / PermissionEnvelope /
│                        #        PublishRequest / PublishResult
├── repository.py        # 数据访问层: 扩展 ResearchRepository 新增成果包 CRUD 方法
├── catalog.py           # ResearchCatalog 扩展: 新增搜索已发布 DerivedDataset
│                        #   (跨用户, ACL 过滤)
└── ...（阶段 1-3 已有文件保持不变）
```

### 6.2 API 路由

```
apps/api/routers/research_publish.py
├── # ── 成果包草稿管理（Workspace 内） ──
├── POST   /api/v1/research/workspaces/{id}/results
│         # 创建成果包草稿（body: {title, summary?, tags?}）
├── GET    /api/v1/research/workspaces/{id}/results
│         # 列出 Workspace 内成果包（含草稿和已发布）
├── GET    /api/v1/research/workspaces/{id}/results/{result_id}
│         # 成果包详情（含版本列表和当前 ACL）
├── PATCH  /api/v1/research/workspaces/{id}/results/{result_id}
│         # 编辑草稿元数据（title/summary/tags，仅 draft 状态）
├── DELETE /api/v1/research/workspaces/{id}/results/{result_id}
│         # 删除未发布的成果包草稿（已发布的不允许删除）
│
├── # ── 成果包产物选择 ──
├── POST   /api/v1/research/workspaces/{id}/results/{result_id}/items
│         # 添加产物到成果包（body: {item_type, item_id, item_version_id?}）
├── DELETE /api/v1/research/workspaces/{id}/results/{result_id}/items/{item_id}
│         # 从成果包移除产物
├── GET    /api/v1/research/workspaces/{id}/results/{result_id}/items
│         # 列出成果包中的产物
│
├── # ── 发布操作 ──
├── POST   /api/v1/research/workspaces/{id}/results/{result_id}/publish
│         # 发布成果包版本
│         # (body: {title, summary?, tags?, release_notes?, acl_scope, explicit_user_ids?})
│         # → 权限校验 + 依赖闭包校验 + 权限包络校验 + 创建不可变版本 + 初始 ACL Revision
│
├── # ── 版本管理 ──
├── GET    /api/v1/research/workspaces/{id}/results/{result_id}/versions
│         # 版本历史列表
├── GET    /api/v1/research/workspaces/{id}/results/{result_id}/versions/{version_number}
│         # 版本详情（含包含的产物列表 + 溯源信息 + content_hash）
├── POST   /api/v1/research/workspaces/{id}/results/{result_id}/versions/{version_number}/supersede
│         # 标记版本为已替代
├── POST   /api/v1/research/workspaces/{id}/results/{result_id}/versions/{version_number}/withdraw
│         # 撤回版本（body: {reason}）
│
├── # ── ACL 管理 ──
├── GET    /api/v1/research/workspaces/{id}/results/{result_id}/acl
│         # 查看 ACL 当前状态和历史（Revision 列表）
├── PUT    /api/v1/research/workspaces/{id}/results/{result_id}/acl
│         # 修改 ACL（body: {acl_scope, explicit_user_ids?, reason?}）
├── POST   /api/v1/research/workspaces/{id}/results/{result_id}/declassify
│         # 突破权限包络（body: {acl_scope, explicit_user_ids?, declassify_reason}）
│         # 需 research:declassify 权限
│
├── # ── 已发布成果包搜索与发现（跨 Workspace） ──
├── GET    /api/v1/research/publications
│         # 搜索已发布成果包
│         # (query, publisher?, tags?, date_from?, date_to?, data_type?,
│         #  workspace_id?, page, page_size, tab=all|mine|favorites)
├── GET    /api/v1/research/publications/{result_id}
│         # 已发布成果包详情（公开视图，含当前版本 + 版本历史 + ACL 状态）
├── GET    /api/v1/research/publications/{result_id}/versions/{version_number}
│         # 已发布版本详情
├── GET    /api/v1/research/publications/{result_id}/items/{item_type}/{item_id}
│         # 成果包内部对象独立引用详情（ACL 校验通过后返回）
├── GET    /api/v1/research/publications/{result_id}/provenance
│         # 成果包来源信息（Workspace/问题/Snapshot/Run/发布者，非联邦溯源图）
│
├── # ── 复用 ──
├── POST   /api/v1/research/workspaces/{id}/evidence/from-publication
│         # 从已发布成果包添加 DerivedDataset 到当前 Workspace
│         # (body: {result_id, dataset_id, dataset_version_number?})
├── POST   /api/v1/research/workspaces/from-publication/{result_id}
│         # 基于已发布成果包新建 Workspace（继承研究问题 + 添加 DerivedDataset 证据）
│
├── # ── 收藏 ──
├── POST   /api/v1/research/publications/{result_id}/favorite
├── DELETE /api/v1/research/publications/{result_id}/favorite
├── GET    /api/v1/research/publications/favorites
│
├── # ── ResearchCatalog 扩展（搜索已发布 DerivedDataset） ──
├── GET    /api/v1/research/catalog/search-published
│         # 搜索已发布成果包中的 DerivedDataset（跨用户, ACL 过滤）
│         # (query, tags?, result_id?, page, page_size)
```

路由注册在 `apps/api/main.py` 中受功能开关控制（延续阶段 1-3 模式）。

### 6.3 前端结构

```
apps/web/src/features/research/
├── ...（阶段 1-3 已有组件保持不变）
├── PublishConfirmModal.tsx      # 发布确认页（基本信息 + 产物选择 + 权限设置 + 溯源信息）
├── PublicationListPage.tsx      # 发布成果页（搜索 + 筛选 + 卡片列表 + 分页）
├── PublicationCard.tsx          # 成果包卡片（标题/发布者/时间/标签/产物摘要/权限）
├── PublicationDetailView.tsx    # 成果详情页（左侧来源 + 右侧 Tab：数据/图表/Insight/版本历史）
├── PublicationVersionList.tsx   # 版本历史列表组件
├── ResultItemDetailView.tsx     # 成果包内单个产物详情视图（独立引用查看）
├── AclManagePanel.tsx           # ACL 管理面板（可见范围修改 + 包络校验 + 变更历史 + declassify）
├── AclRevisionList.tsx          # ACL 变更历史列表
├── EvidencePanel.tsx           # 左栏扩展：新增"已发布成果"类型筛选和搜索
├── ConfirmedProductsPanel.tsx  # 修改：增加"发布研究成果包"按钮入口
└── api/
    └── researchPublish.ts       # 发布相关 API 函数
```

LabOpsPage 改造：阶段 1 已将"发布成果"Tab 替换原"模型发布"占位。阶段 4 在此 Tab 下挂载 `PublicationListPage`。

### 6.4 数据库表设计概要

**research_research_result**
- `id` (UUID PK), `workspace_id` (FK→research_workspace CASCADE), `owner_user_id` (FK→app_user), `title` (TEXT), `summary` (TEXT nullable), `tags` (JSONB default '[]'), `status` (TEXT: draft/published/archived, default 'draft'), `current_version` (INT default 0), `created_at`, `updated_at`, `lock_version`
- `status`: draft（草稿，可编辑/删除）/ published（已发布，不可删除）/ archived（已归档）
- `current_version`: 最新已发布版本号，0 表示尚未发布
- 可编辑字段：`title` / `summary` / `tags`（仅 draft 状态）

**research_research_result_version**
- `id` (UUID PK), `result_id` (FK→research_research_result CASCADE), `version_number` (INT), `workspace_id` (FK→research_workspace), `title` (TEXT), `summary` (TEXT nullable), `tags` (JSONB), `release_notes` (TEXT nullable), `content_hash` (TEXT), `published_by` (FK→app_user), `published_at` (UTCDateTime), `status` (TEXT: published/superseded/withdrawn, default 'published'), `snapshot_id` (UUID, 逻辑引用 EvidenceSnapshot), `source_run_ids` (JSONB: [{run_id, run_status}]), `workspace_question` (TEXT, 发布时研究问题快照), `created_at`
- 不可变：创建后不允许 UPDATE/DELETE（应用层保证）
- 唯一约束：`UNIQUE (result_id, version_number)`
- `status` 变更仅通过专用 supersede/withdraw API（应用层特殊路径，非通用 update）
- `content_hash`: SHA-256(标题 + 摘要 + 标签 + 发布说明 + 排序后的产物版本 ID + 产物 content_hash 拼接)

**research_result_item**
- `id` (UUID PK), `result_version_id` (FK→research_research_result_version CASCADE), `item_type` (TEXT: derived_dataset/view/insight), `item_id` (UUID, 逻辑引用稳定身份), `item_version_id` (UUID, 逻辑引用版本), `item_version_number` (INT), `item_name` (TEXT, 发布时名称快照), `display_order` (INT default 0), `created_at`
- `item_id` / `item_version_id` 为逻辑引用，不建数据库级 FK 到 DerivedDatasetVersion / ViewVersion / InsightVersion 表
- 查询索引：`INDEX (result_version_id, item_type)`

**research_result_acl_revision**
- `id` (UUID PK), `result_id` (FK→research_research_result), `revision_number` (INT), `acl_scope` (TEXT: private/tree/explicit/all), `explicit_user_ids` (JSONB, nullable), `previous_acl_scope` (TEXT nullable), `previous_explicit_user_ids` (JSONB nullable), `changed_by` (FK→app_user), `changed_at` (UTCDateTime), `change_reason` (TEXT nullable), `is_declassify` (BOOLEAN default false), `declassify_reason` (TEXT nullable), `source_envelope_snapshot` (JSONB nullable, 权限包络快照)
- 不可变：创建后不允许 UPDATE/DELETE（仅追加）
- 唯一约束：`UNIQUE (result_id, revision_number)`
- `revision_number` 从 1 开始递增

**research_publication_favorite**
- `id` (UUID PK), `user_id` (FK→app_user), `result_id` (FK→research_research_result), `created_at`
- 唯一约束：`UNIQUE (user_id, result_id)`

> **注意**：5 张新表均以 `research_` 前缀命名，延续 `research_*` 命名空间。迁移编号延续 `0077`（阶段 1 为 `0074`，阶段 2 为 `0075`，阶段 3 为 `0076`）。核心表无到研究发布表的外键。研究发布表到 `research_workspace` 的 FK 允许保留（同为研究域内部表）。产物引用（`item_id` / `item_version_id`）为逻辑引用，不建数据库级 FK。

### 6.5 权限集成

在 `packages/auth/permissions.py` 的 `Permission` 类中新增：

```python
# 研究分析 — 发布与复用（IRIP Research Module - 子项目4）
RESEARCH_PUBLISH: str = "research:publish"      # 自行发布成果，不需双人审批
RESEARCH_DECLASSIFY: str = "research:declassify" # 突破源数据权限包络
RESEARCH_MANAGE: str = "research:manage"        # 撤回成果、处理归属和异常内容
```

在 `BUILTIN_ROLES` 中分配：

| 角色 | research:use | research:publish | research:declassify | research:manage |
|------|:---:|:---:|:---:|:---:|
| lab_director | ✓ | ✓ | ✓ | ✓ |
| lab_member | ✓ | ✓ | — | — |
| lab_viewer | — | — | — | — |
| platform_admin | — | — | — | ✓ |
| platform_auditor | — | — | — | — |

权限使用：
- `research:publish`：发布操作（POST .../publish）、ACL 修改（PUT .../acl）
- `research:declassify`：突破权限包络（POST .../declassify）
- `research:manage`：撤回他人成果（POST .../withdraw 对非 owner 成果）
- `research:use`：搜索已发布成果、收藏、加入 Workspace（延续阶段 1）

### 6.6 权限包络交集计算逻辑

```
发布或 ACL 修改时的权限包络校验逻辑：

1. 收集成果包内所有产物的源 EvidenceSnapshot：
   - 对每个 DerivedDatasetVersion → 通过 DerivedDataset.source_snapshot_id 获取 Snapshot
   - 对每个 ResearchView → 通过 View.source_run_id → Run → 获取关联 Snapshot
   - 对每个 Insight → 通过 Insight.source_run_id → Run → 获取关联 Snapshot
   （去重：多个产物可能来自同一 Snapshot）

2. 从每个 Snapshot 获取 permission_envelope（JSONB，记录冻结时的权限包络）

3. 对每个 permission_envelope，查询源数据当前权限：
   - core:fact 类型的源 → 通过 CoreFactProvider 查询 Fact 当前 visible_departments / visibility_scope
   - research:derived 类型的源 → 递归查询其源 Snapshot 的权限包络
   （首期可简化为使用 Snapshot 冻结时的 permission_envelope，标注"基于快照"）

4. 计算所有源数据当前权限包络的交集：
   - ACL 限制级别：private > explicit > tree > all
   - 交集取最严格的限制

5. 校验请求的 ACL 是否在交集内：
   - requested_acl ≤ intersection → 通过
   - requested_acl > intersection → 拒绝（除非 research:declassify + 理由）

6. 存储权限包络快照到 ResultAclRevision.source_envelope_snapshot
```

### 6.7 审计事件命名

| 操作 | action 字符串 | resource_type |
|------|--------------|---------------|
| 创建成果包草稿 | `research.result.create` | `research_research_result` |
| 发布成果包版本 | `research.result.publish` | `research_research_result_version` |
| 成果包新版本 | `research.result.new_version` | `research_research_result_version` |
| 编辑成果包草稿元数据 | `research.result.edit` | `research_research_result` |
| 删除成果包草稿 | `research.result.delete` | `research_research_result` |
| 版本 supersede | `research.result.supersede` | `research_research_result_version` |
| 版本 withdraw | `research.result.withdraw` | `research_research_result_version` |
| ACL 修改 | `research.result.acl_change` | `research_result_acl_revision` |
| 权限包络自动收紧 | `research.result.acl_tightened` | `research_research_result` |
| declassify 使用 | `research.result.declassify` | `research_result_acl_revision` |
| 添加产物到成果包 | `research.result.add_item` | `research_result_item` |
| 从成果包移除产物 | `research.result.remove_item` | `research_result_item` |
| 收藏成果包 | `research.result.favorite` | `research_publication_favorite` |
| 取消收藏 | `research.result.unfavorite` | `research_publication_favorite` |
| 从已发布成果加入证据 | `research.result.reuse_evidence` | `research_workspace_evidence_ref` |
| 基于成果新建 Workspace | `research.result.reuse_workspace` | `research_workspace` |

### 6.8 与阶段 3 的集成点

| 阶段 3 组件 | 阶段 4 集成方式 |
|------------|---------------|
| ProductService | PublicationService 调用 ProductService 获取已确认产物列表和版本详情，用于成果包产物选择 |
| ResearchCatalogImpl | 扩展搜索范围：从仅搜索当前用户已确认 DerivedDataset 升级为同时搜索已发布成果包中的 DerivedDataset（跨用户，ACL 过滤） |
| WorkspaceEvidenceRef | 扩展 source_namespace 支持 `research:published`，记录 result_id / result_version_number / dataset_id / dataset_version_number |
| EvidenceSnapshotService | 快照冻结时新增 `research:published` 命名空间分支：从已发布 DerivedDatasetVersion 获取 content_hash 纳入哈希计算 |
| DerivedDatasetVersion | 成果包通过 research_result_item 逻辑引用特定版本（item_version_id），发布时快照产物名称和版本号 |
| ResearchViewVersion | 同上，成果包引用 View 版本 |
| InsightVersion | 同上，成果包引用 Insight 版本 |
| ConfirmedProductsPanel | 前端增加"发布研究成果包"按钮入口 |
| EvidencePanel | 前端增加"已发布成果"类型筛选和搜索 |
| WorkspaceService | add_evidence() 增加 `research:published` 命名空间分支；delete_workspace() 增加发布成果引用检查 |

### 6.9 与阶段 5 的衔接预留

阶段 4 的成果详情页展示来源信息（Workspace/问题/Snapshot/Run/发布者/版本），但**不实现**完整的跨边界联邦溯源图和受限占位节点。阶段 5 将在此基础上：
- 通过 `GET /api/v1/research/publications/{result_id}/provenance` 入口升级为联邦溯源图查询
- 引入 CoreProvenanceAdapter + ResearchLineageAdapter 拼接完整溯源链路
- 无权访问的上游节点以受限占位节点表示

阶段 4 预留：
- 成果详情页的"衍生来源"区域设计为可替换组件，阶段 5 替换为联邦溯源图
- `GET .../provenance` API 返回简化来源信息，阶段 5 升级为完整图查询
- ResearchLineageEdge 表在阶段 5 创建，阶段 4 不建此表

### 6.10 发布操作时序

```
用户点击"确认发布"
  │
  ▼
PublicationService.publish(workspace_id, result_id, publish_request)
  │
  ├─ 1. 权限校验: require_permission("research:publish")
  │
  ├─ 2. 加载成果包草稿: ResearchResult (status=draft)
  │
  ├─ 3. 加载已选产物: research_result_item (临时选择) 或从草稿中读取
  │     → ProductService.get_dataset/view/insight 详情
  │     → 校验产物 status=confirmed
  │     → 校验至少包含一个 Dataset 或 View
  │
  ├─ 4. 依赖闭包校验: 对每个产物的 source_run_id
  │     → Run.status 必须为 succeeded 或 partially_succeeded
  │     → cancelled/failed Run 的产物拒绝发布
  │     → partially_succeeded 标注在 source_run_ids 中
  │
  ├─ 5. 权限包络交集计算: AclService.compute_envelope_intersection(items)
  │     → 收集所有源 Snapshot 的 permission_envelope
  │     → 计算交集
  │
  ├─ 6. ACL 校验: AclService.validate_acl(requested_acl, intersection)
  │     → requested_acl ≤ intersection → 通过
  │     → requested_acl > intersection → 拒绝（需 declassify）
  │
  ├─ 7. 计算 content_hash: SHA-256(标题+摘要+标签+发布说明+产物版本排序)
  │
  ├─ 8. 创建 ResearchResultVersion (不可变)
  │     → version_number = result.current_version + 1
  │     → status = 'published'
  │
  ├─ 9. 创建 research_result_item 记录 (产物版本引用快照)
  │
  ├─ 10. 创建初始 ResultAclRevision (revision_number=1)
  │      → acl_scope = requested_acl
  │      → source_envelope_snapshot = 交集快照
  │
  ├─ 11. 更新 ResearchResult: status='published', current_version=version_number
  │
  ├─ 12. 审计事件: research.result.publish
  │
  └─ 13. 返回 PublishResult (result_id, version_number, content_hash)
```
