# PRD: 统一溯源与知识接口（子项目 5）

> **项目名称**: irip_research_lineage

> **编程语言/技术栈**: 后端 Python 3.12+ / FastAPI / SQLAlchemy(异步) / PostgreSQL 16(pgvector) / Redis 7 / Celery；前端 React 18 + TS / Vite / Ant Design 5 / TanStack Router+Query

> **日期**: 2026-08-06

> **状态**: 评审稿

> **依赖基线**: 阶段 1"研究域基础" + 阶段 2"可信执行" + 阶段 3"研究产物" + 阶段 4"发布与复用"已完成并上线（`docs/prd-research-foundation.md` / `docs/arch-research-foundation.md` / `docs/prd-research-trusted-execution.md` / `docs/arch-research-trusted-execution.md` / `docs/prd-research-products.md` / `docs/arch-research-products.md` / `docs/prd-research-publish.md` / `docs/arch-research-publish.md`）

---

## 0. 原始需求复述

IRIP "研究分析与发布成果"模块设计方案（`docs/superpowers/specs/2026-08-05-research-analysis-and-publication-design.md`）建议拆为 5 个子项目分阶段建设。本期交付**第 5 个子项目"统一溯源与知识接口"**，在阶段 1-4 已交付的完整研究闭环之上，实现联邦式统一溯源查询、受限来源占位和 KnowledgeProvider 只读接入，补齐"证据可追溯、知识可引用"的最后一环。

**阶段 1 已交付基线**：
- Workspace 创建/列表/归档/删除/分叉
- ResearchQuestionVersion 研究问题版本管理
- WorkspaceEvidenceRef 数据引用管理（`source_namespace` 支持 `core:fact` + `research:derived`）
- ResearchEvidenceSnapshot 证据快照冻结（SHA-256 哈希 + 权限包络 + 字段清单）
- CoreFactProvider 只读适配 + ResearchCatalog 接口
- 功能开关 + `research:use` 权限
- 前端三栏布局（左栏证据面板 / 中栏研究画布 / 右栏 AI 助手）

**阶段 2 已交付基线**：
- AnalysisPlanVersion 不可变计划版本 + 计划级授权
- AnalysisRun 后台持久运行 + DAG 步骤编排（ResearchOrchestrator）
- ResearchAnalysisStep 步骤状态管理
- ResearchRunArtifact 工件表（is_publishable 标记 + MinIO 存储）
- ModelGateway 模型网关 + ContextRouter 上下文路由 + 500K 预算
- SandboxRuntime 沙箱执行 + ResearchScheduler 20 用户公平调度
- **ResearchMemoryService 后台研究记忆 + AIConversationService AI 对话持久化**

**阶段 3 已交付基线**：
- DerivedDataset + DerivedDatasetVersion（三段式数据 + field_manifest + content_hash）
- ResearchView + ResearchViewVersion（静态图 PNG/PDF + 绑定引用 + 版本不可变）
- Insight + InsightVersion（6 个结构化字段 + 证据来源标签 + AI 原稿 + 修改记录）
- InsightCandidate（候选提取 + 接受/修改/拒绝）
- ProductService / CandidateService / ThreeSegmentValidator / InsightExtractor
- ResearchCatalogImpl（搜索当前用户已确认 DerivedDataset）
- WorkspaceEvidenceRef 支持 `research:derived` 命名空间

**阶段 4 已交付基线**：
- ResearchResult + ResearchResultVersion（成果包版本 + 内容哈希 + 版本不可变）
- ResultAclRevision（ACL 变更仅追加记录）
- `research:publish` + `research:declassify` 权限点
- 权限包络校验（requested_acl ∩ source_permission_envelopes）
- 成果包搜索 / 发现 / 详情页 / 收藏 / 撤回
- 成果包内部对象独立引用（统一继承成果包 ACL）
- ResearchCatalog 跨用户升级（搜索已发布成果包中的 DerivedDataset）
- WorkspaceEvidenceRef 支持 `research:published_derived` 命名空间
- 发布成果页（Tab 激活）
- **ResearchLineageEdge 表已创建**（溯源边仅追加，edge_type 含 workspace_to_result / dataset_to_result / view_to_result / insight_to_result / fact_to_snapshot / snapshot_to_run / run_to_dataset / run_to_view / dataset_to_insight / view_to_insight）
- **发布时已创建溯源边记录**（为阶段 5 联邦溯源预留数据入口）
- 成果详情页预留"溯源"Tab（P2-4 受限溯源节点预览入口）

**本期范围**：
1. **联邦式统一 Provenance 查询**：UnifiedProvenanceQueryService 由 CoreProvenanceAdapter（只读查询核心 Fact / DerivationRun / ParameterVersion 等节点）和 ResearchLineageAdapter（只读查询 Evidence Snapshot / Analysis Run / Derived Dataset / View / Insight / 成果版本）组成，跨边界拼接为完整溯源图
2. **图拼接与查询控制**：节点命名空间 ID、跨边界边拼接、循环保护、深度限制、权限裁剪和展示标签
3. **受限来源占位节点**：用户无权访问的上游节点显示为不含名称/ID/属性/内容的"受限来源"占位节点，保证链路不断裂；权限策略可截断不应暴露的分支
4. **KnowledgeProvider 只读接入合同**：外部知识库的只读检索接口合同，返回 document_id / document_version / title / section / page / chunk_id / relevance_score / source_uri / content_hash / 精确检索片段
5. **知识引用快照**：模型引用知识库时保存被实际引用的段落快照、文档版本和哈希，确保外部知识库更新后已发布 Insight 仍能解释当时依据
6. **溯源 UI**：成果详情页"溯源"Tab 激活为完整联邦溯源图；Workspace 内产物溯源视图；溯源图中的受限占位节点展示

**基线约束**：
- 延续阶段 1-4 的模块隔离原则——新模块不反向侵入老系统，核心表不写入研究节点，不建立到研究表的外键
- 统一服务只读查询核心 Provenance，不修改核心表
- 产品和查询层面统一使用"数据溯源（Provenance）"概念，"Lineage"仅作为研究模块内部实现术语，不对用户形成第二套概念
- 知识库、实验数据和模型推测必须使用不同证据标签，不能混写成一个无来源的结论
- 默认只向知识库发送研究问题和用户确认的关键词，不发送完整 Fact 原始数据
- 代码/API/字段英文，UI 中文

---

## 1. 产品目标

| # | 目标 | 衡量标准 |
|---|------|---------|
| G1 | **联邦溯源图完整拼接**：UnifiedProvenanceQueryService 将核心 Provenance（Fact / DerivationRun / ParameterVersion 等）与研究 Lineage（Evidence Snapshot / Analysis Run / Derived Dataset / View / Insight / 成果版本）拼接为一张完整溯源图，用户在成果详情页和产物详情页可查看从源 Fact 到已发布成果的完整链路 | CoreProvenanceAdapter 只读查询核心节点（不修改核心表）；ResearchLineageAdapter 读取 research_lineage_edge 和研究域实体；跨边界边（如 `core:fact:<id> → research:evidence_snapshot:<id>`）正确拼接；溯源图包含从 Fact → Snapshot → Run → Dataset/View/Insight → Result Version 的完整路径；循环保护和深度限制生效 |
| G2 | **受限来源占位不泄露信息**：用户无权访问的上游节点显示为"受限来源"占位节点，不含名称、ID、属性和内容，保证已可见成果的链路不无解释地断裂；权限策略可截断不应暴露的分支 | 无权访问的节点在溯源图中显示为受限占位节点（仅显示"受限来源"标签，无任何可识别信息）；占位节点是查询时生成的临时表示，不提供稳定可枚举标识；权限裁剪后已可见节点的链路保持完整；安全测试验证受限节点不泄露名称、稳定 ID 和属性 |
| G3 | **KnowledgeProvider 只读接入与引用快照**：研究模块通过 KnowledgeProvider 只读合同检索外部知识库，模型引用知识库时保存被实际引用的段落快照、文档版本和哈希，确保外部知识库更新后已发布 Insight 仍能解释当时依据 | KnowledgeProvider 接口返回设计文档 13 节规定的全部字段；检索时仅发送研究问题和用户确认的关键词（不发送完整 Fact 原始数据）；引用快照保存段落文本、document_id、document_version、content_hash、source_uri 和检索时间；KnowledgeProvider 不可用时按降级策略处理（非必要步骤降级为仅数据分析并标注，必要步骤失败） |
| G4 | **溯源 UI 体验统一**：用户在成果详情页"溯源"Tab 和 Workspace 产物溯源视图中看到统一的联邦溯源图，包含节点类型标签、边类型说明和受限占位节点，并能从溯源图节点跳转到对应实体详情 | 成果详情页"溯源"Tab 激活为完整联邦溯源图；溯源图以 DAG 可视化展示（节点 + 有向边）；节点显示类型标签和名称（受限节点除外）；边显示关系类型；用户可从可见节点跳转到对应详情（Fact 详情、产物详情、Run 详情等）；溯源图支持折叠/展开和深度控制 |

---

## 2. 用户故事

**US-1 — 查看成果的完整溯源链路**
> 作为研究人员，我想在已发布成果包的详情页打开"溯源"Tab，看到从源实验 Fact 到 Evidence Snapshot、Analysis Run、Derived Dataset / View / Insight 直到成果版本的完整溯源图，以便我理解这些成果是如何从原始实验数据一步步推导出来的，并验证每一步都有据可查。

**US-2 — 查看产物的溯源链路**
> 作为研究人员，我想在 Workspace 内的产物（Derived Dataset / View / Insight）详情中查看其溯源链路，看到它来自哪个 Run、哪些 Evidence Snapshot、最终追溯到哪些源 Fact，以便我确认分析过程的完整性。

**US-3 — 理解受限来源占位**
> 作为研究人员，我想在溯源图中看到无权访问的上游节点显示为"受限来源"占位节点，而不是链路突然断裂或显示我无权查看的敏感信息，以便我理解成果的来源结构，同时确信系统不会泄露我没有权限的数据。

**US-4 — AI 引用知识库并保存快照**
> 作为研究人员，我想让 AI 在分析过程中检索内部知识库获取相关文献，并确信系统保存了 AI 实际引用的段落快照、文档版本和哈希，以便即使知识库后续更新，我已发布的 Insight 仍能解释当时的依据来源。

**US-5 — 区分不同证据来源**
> 作为研究人员，我想在溯源图和 Insight 详情中清楚区分哪些证据来自实验数据、哪些来自知识库、哪些来自模型推测，以便我做出科研判断时明确知道每个结论的依据类型，而不是面对一个无来源的混合结论。

**US-6 — 管理员查看知识引用快照**
> 作为拥有 `research:manage` 权限的管理员，我想查看已发布 Insight 引用的知识库段落快照，以便在需要时核查知识引用的准确性和时效性。

---

## 3. 需求池

### P0 — Must Have

| ID | 需求 | 验收标准 |
|----|------|---------|
| P0-1 | **CoreProvenanceAdapter**：只读查询核心系统 Provenance 节点（Fact、现有 DerivationRun、ParameterVersion、EvidenceSet、EvidenceSetVersion 等），不修改核心表，不暴露核心数据库会话 | CoreProvenanceAdapter 实现 `query_node(namespace, node_id)` 和 `query_outgoing_edges(namespace, node_id)` 接口；返回核心节点的类型标签和展示属性（名称、类型、版本摘要）；只读访问核心表，不产生任何 INSERT/UPDATE/DELETE；不暴露核心 DB session（通过独立查询方法封装） |
| P0-2 | **ResearchLineageAdapter**：只读查询研究域 Lineage 节点（Evidence Snapshot、Analysis Run、Analysis Step、Derived Dataset、Derived Dataset Version、Research View、Research View Version、Insight、Insight Version、Research Result Version、Workspace、ResearchQuestionVersion）和溯源边（research_lineage_edge 表） | ResearchLineageAdapter 实现 `query_node(namespace, node_id)` 和 `query_outgoing_edges(namespace, node_id)` 接口；读取 research_lineage_edge 表（阶段 4 已创建）获取溯源边；返回研究域节点类型标签和展示属性；溯源边覆盖全部 edge_type（workspace_to_result / dataset_to_result / view_to_result / insight_to_result / fact_to_snapshot / snapshot_to_run / run_to_dataset / run_to_view / dataset_to_insight / view_to_insight） |
| P0-3 | **UnifiedProvenanceQueryService**：联邦式统一溯源查询服务，协调 CoreProvenanceAdapter 和 ResearchLineageAdapter，拼成一张完整溯源图 | `query_provenance_graph(target_namespace, target_id, principal, options)` 接口返回 ProvenanceGraph（节点列表 + 边列表）；根据 target 的命名空间自动路由到正确的 Adapter；跨边界边正确拼接（如 `core:fact` 节点的下游边由 research_lineage_edge 提供）；节点使用命名空间 ID（`core:fact:<id>`、`research:evidence_snapshot:<id>` 等），避免不同模块 UUID 语义冲突 |
| P0-4 | **图拼接逻辑**：统一服务负责图拼接，从目标节点（如成果版本）向上游追溯，递归查询每个节点的入边和来源节点，直到到达根节点（如 Fact）或深度上限 | 图拼接从 target 节点开始，沿 research_lineage_edge 和核心 Provenance 边向上游递归追溯；跨边界边（如 `core:fact:<id> → research:evidence_snapshot:<id>`）正确识别并跨越；拼接结果为完整 DAG（有向无环图）；遇到核心节点时调用 CoreProvenanceAdapter 继续追溯核心侧上游 |
| P0-5 | **循环保护**：溯源图查询过程中防止循环（如 A → B → A 的环路），避免无限递归 | 查询过程维护已访问节点集合（namespace + node_id）；遇到已访问节点时停止递归该分支；查询结果中不出现重复节点；图边不形成环 |
| P0-6 | **深度限制**：溯源图查询设置最大深度限制（默认 20 层），防止过深追溯导致性能问题 | 查询接受 max_depth 参数（默认 20）；超过 max_depth 的分支停止递归并在结果中标记为"已截断"；被截断的节点在溯源图中显示"继续追溯"提示（用户可手动展开下一层） |
| P0-7 | **权限裁剪**：溯源图查询时根据当前用户的权限裁剪不可见节点。用户无权访问的节点显示为受限占位节点 | 查询时对每个节点校验当前 principal 的访问权限；无权访问的节点替换为受限占位节点（RestrictedNode：仅含 `node_type="restricted"` 和 `reason="no_permission"`，不含名称/ID/属性/内容）；有权限的节点正常返回展示属性；权限裁剪在图拼接后统一执行（不在递归过程中提前判断，避免权限检查次数过多） |
| P0-8 | **受限占位节点规则**：用户无权访问某个上游节点时，图中保留不含名称、ID、属性和内容的"受限来源"占位节点。若连节点存在本身也不应暴露，权限策略可直接截断该分支 | 受限占位节点在 ProvenanceGraph 中表示为 `{node_type: "restricted", display_label: "受限来源", attributes: {}}`，不含任何可识别信息；占位节点是本次查询生成的临时表示，不提供稳定可枚举标识（每次查询生成新的临时 ID）；权限策略支持 "truncate_branch" 选项：当连节点存在都不应暴露时，直接截断该分支（不显示占位节点） |
| P0-9 | **节点展示标签**：统一服务为每个节点生成展示标签（显示名称、类型图标、版本摘要），便于前端统一渲染 | 每个可见节点返回 `display_label`（如 "Fact: 拉伸强度测试-001"）、`node_type_label`（如 "实验事实" / "证据快照" / "分析运行" / "衍生数据" / "图表" / "Insight" / "成果版本"）、`version_summary`（如 "v2"）、`namespace`（如 "core:fact"）；受限节点返回 `display_label: "受限来源"` |
| P0-10 | **KnowledgeProvider 接口合同**：定义外部知识库的只读检索接口合同，返回设计文档 13 节规定的全部字段 | KnowledgeProvider 接口定义 `search(query, options) -> list[KnowledgeSearchResult]` 和 `get_document(document_id) -> KnowledgeDocument`；KnowledgeSearchResult 包含 document_id / document_version / title / section / page / chunk_id / relevance_score / source_uri / content_hash / 精确检索片段（snippet）；接口为只读合同，研究模块不维护知识库内容 |
| P0-11 | **KnowledgeProvider 注册与发现**：平台支持注册多个 KnowledgeProvider 实例，Orchestrator 按配置路由检索请求 | KnowledgeProvider 注册配置存储在平台配置中（provider_name / endpoint / auth_config / enabled）；检索请求按 provider_name 路由到对应实例；支持多个 provider 并行检索并合并结果；provider 不可用时返回降级标记 |
| P0-12 | **知识引用快照保存**：模型引用知识库时，研究模块保存被实际引用的段落快照、文档版本和哈希（不复制整篇文献） | 引用快照保存：snippet_text（引用段落文本）、document_id、document_version、content_hash、source_uri、retrieval_time、provider_name、research_question_context（检索时的研究问题上下文）；快照存储在研究域对象存储（`research/knowledge_refs/{workspace_id}/{run_id}/`）；快照创建后不可变 |
| P0-13 | **KnowledgeReference 实体**：记录 AI 分析过程中引用的知识库段落快照，关联到 Insight 和 Analysis Step | research_knowledge_reference 表：id / workspace_id / run_id / step_id / insight_id（可空）/ document_id / document_version / chunk_id / snippet_text / content_hash / source_uri / retrieval_time / provider_name / created_at；仅追加（append-only）；通过 insight_id 关联到 Insight，通过 run_id + step_id 关联到 Analysis Step |
| P0-14 | **知识库检索安全**：默认只向知识库发送研究问题和用户确认的关键词，不发送完整 Fact 原始数据 | KnowledgeProvider.search 的 query 参数仅包含研究问题文本和用户确认的检索关键词；不发送 Fact 的 points/series 原始数据；检索请求记录发送内容摘要（不含原始数据）用于审计；ContextRouter 在路由到知识库检索步骤时标记为 "keyword_only" 模式 |
| P0-15 | **证据来源标签区分**：知识库、实验数据和模型推测使用不同证据标签。溯源图中知识引用节点的类型标签为"知识库引用"，与实验数据节点和模型推测节点区分 | 溯源图中 KnowledgeReference 节点的 `node_type_label` 为"知识库引用"；Fact / Derived Dataset 节点为"实验数据" / "衍生数据"；Insight 的 evidence_source_label 已在阶段 3 实现（experimental_data / knowledge_base / model_inference）；溯源图边类型区分 `knowledge_ref_to_insight` 与 `dataset_to_insight` |
| P0-16 | **KnowledgeProvider 降级处理**：知识库不可用时按降级策略处理——非必要步骤降级为仅数据分析并标注，必要步骤失败 | KnowledgeProvider 检索失败时：若该步骤为非必要（分析计划中标记为 optional），降级为仅数据分析并在 Run 日志和覆盖声明中标注"知识库不可用，已降级"；若为必要步骤（required），该步骤标记为 failed；降级事件记录在 AnalysisStep 的 error 分类中；覆盖声明显示知识库检索覆盖率 |
| P0-17 | **成果详情页溯源 Tab 激活**：阶段 4 预留的"溯源"Tab 激活为完整联邦溯源图展示 | 成果详情页"溯源"Tab 展示 UnifiedProvenanceQueryService 返回的 ProvenanceGraph；以 DAG 可视化展示（节点 + 有向边）；从成果版本节点开始向上游追溯；节点显示类型标签和名称（受限节点显示"受限来源"）；边显示关系类型；支持折叠/展开和深度控制（默认展示 5 层，可展开更多） |
| P0-18 | **产物溯源视图**：Workspace 内产物（Derived Dataset / View / Insight）详情中展示该产物的溯源链路 | 产物详情页新增"溯源"区域，调用 UnifiedProvenanceQueryService 查询该产物版本的溯源图；溯源图从该产物版本节点开始向上游追溯；展示范围与成果详情页溯源图一致（含受限占位节点和深度控制） |
| P0-19 | **溯源图节点跳转**：溯源图中的可见节点可点击跳转到对应实体详情页 | Fact 节点点击跳转到原系统 Fact 详情页；Derived Dataset / View / Insight 节点点击跳转到研究模块产物详情；Analysis Run 节点点击跳转到 Run 详情；Evidence Snapshot 节点点击展示快照摘要（无独立详情页）；KnowledgeReference 节点点击展示引用快照内容（snippet_text + 文档元数据）；受限占位节点不可点击 |
| P0-20 | **溯源查询审计**：溯源图查询操作产生审计记录 | 审计记录包含 `research.provenance.query`、操作者、时间、查询目标（namespace + id）、查询深度、返回节点数、受限节点数；不含溯源图具体内容（防止审计日志泄露被裁剪的信息） |
| P0-21 | **溯源边完整性补充**：阶段 4 发布时创建了部分溯源边（workspace_to_result / dataset_to_result / view_to_result / insight_to_result）。阶段 5 补充 Analysis Run 执行过程中和产物创建过程中的溯源边，确保 research_lineage_edge 表覆盖完整链路 | Analysis Run 执行时创建溯源边：evidence_snapshot → analysis_run、analysis_run → analysis_step、analysis_step → run_artifact；产物确认时创建溯源边：run_artifact → derived_dataset、run_artifact → research_view、analysis_run → insight；知识引用时创建溯源边：knowledge_reference → insight；证据选择时创建溯源边：fact → evidence_snapshot（跨边界边）、published_derived → evidence_snapshot。所有溯源边仅追加 |
| P0-22 | **ResearchLineageEdge 跨边界边记录**：跨边界溯源边（如 `core:fact:<id> → research:evidence_snapshot:<id>`）由研究模块保存，核心表不写入研究节点 | 跨边界边的 source_namespace 为核心命名空间（如 `core:fact`），target_namespace 为研究命名空间（如 `research:evidence_snapshot`）；边记录在 research_lineage_edge 表中（阶段 4 已创建表结构）；核心表不保存任何到研究表的外键或引用 |
| P0-23 | **KnowledgeProvider 合同测试**：对 KnowledgeProvider 接口进行合同测试，确保第三方知识库接入符合接口规范 | 合同测试覆盖 search 和 get_document 两个接口；验证返回字段完整性（document_id / document_version / title / section / page / chunk_id / relevance_score / source_uri / content_hash / snippet）；验证只读接口不产生副作用；Mock KnowledgeProvider 用于测试 |

### P1 — Should Have

| ID | 需求 | 验收标准 |
|----|------|---------|
| P1-1 | **溯源图导出**：用户可将溯源图导出为图片（PNG）或结构化文件（JSON），便于离线分享和审计 | 导出 PNG 包含完整可见溯源图（受限节点以"受限来源"显示）；导出 JSON 包含节点和边的结构化数据（受限节点不含属性）；导出操作产生审计记录 |
| P1-2 | **溯源图搜索与高亮**：溯源图支持关键词搜索，匹配的节点高亮显示 | 搜索框支持按节点名称/类型搜索；匹配节点高亮，非匹配节点降低透明度；搜索不跨受限节点（受限节点不参与匹配） |
| P1-3 | **知识引用快照查看**：拥有 `research:manage` 权限的用户可在 Insight 详情中查看引用的知识库段落快照 | Insight 详情展示关联的 KnowledgeReference 列表（snippet_text + document_id + document_version + source_uri + retrieval_time）；快照内容只读不可修改；无 `research:manage` 权限的用户不展示快照详情（仅展示文档标题和来源链接） |
| P1-4 | **知识库检索覆盖率声明**：分析过程中展示知识库检索覆盖率声明，类似数据覆盖率声明 | 覆盖声明格式："知识库检索: 已检索 / 未检索（知识库不可用） / 不适用"；在 Run 进度面板和覆盖声明区展示；知识库不可用时标注降级状态 |
| P1-5 | **溯源图权限变化实时反映**：源数据权限变化后，溯源图查询结果实时反映新的权限裁剪结果 | 源数据权限收紧后，溯源图中对应节点变为受限占位节点；不依赖创建时的静态权限快照；每次查询动态校验当前权限 |
| P1-6 | **多 KnowledgeProvider 并行检索**：当配置多个 KnowledgeProvider 时，Orchestrator 并行检索并合并去重结果 | 并行检索请求超时独立控制（单个 provider 超时不影响其他）；结果按 relevance_score 排序去重（基于 content_hash）；合并结果展示来源 provider_name |
| P1-7 | **溯源图节点计数与统计**：溯源图展示节点统计摘要（总节点数、各类型节点数、受限节点数） | 统计摘要展示在溯源图顶部或侧边栏；受限节点数单独标注（提示用户链路中有不可见部分）；统计随深度展开实时更新 |

### P2 — Nice to Have

| ID | 需求 | 验收标准 |
|----|------|---------|
| P2-1 | **溯源图布局算法优化**：支持多种图布局算法（层次布局 / 力导向布局），用户可切换 | 默认使用层次布局（DAG 从上到下）；力导向布局适用于复杂图；切换布局不重新查询数据 |
| P2-2 | **知识引用过期提醒**：当知识库文档版本更新时，提醒引用了旧版本的 Insight 可能需要重新验证 | 系统定期检查已引用文档的当前版本与快照版本是否一致；版本不一致时在 Insight 详情中展示"知识库文档已更新"提示；不自动修改已发布 Insight |
| P2-3 | **溯源图差异对比**：对比两个版本的成果包溯源图差异，展示新增/移除的来源节点 | 版本对比展示 v1 vs v2 溯源图的节点和边差异；新增节点高亮，移除节点灰色标注 |
| P2-4 | **KnowledgeProvider 健康检查**：平台定期检查已注册 KnowledgeProvider 的可用性，不可用的 provider 在检索时自动跳过 | 健康检查定期调用 provider 的 ping/health 接口；不可用 provider 标记为 disabled 并通知管理员；检索时自动跳过 disabled provider |

---

## 4. UI 设计概要

### 4.1 成果详情页 — 溯源 Tab（激活）

```
┌──────────────────────────────────────────────────────────────────┐
│  ◀ 返回    批次峰值分析研究报告                                    │
│            v2 (最新)  |  v1 (superseded)                          │
├──────────────────────────────────────────────────────────────────┤
│                                                                    │
│  [metadata] [points] [series] [Views] [Insights] [溯源]            │
│                                                                    │
│  ┌── 数据溯源 ────────────────────────────────────────────────┐  │
│  │                                                              │  │
│  │  📊 节点统计: 总 12 | 实验 3 | 快照 2 | 运行 2 | 产物 4 |    │  │
│  │             成果 1 | 🔒受限 2                                │  │
│  │                                                              │  │
│  │  深度: [5层 ▾]  布局: [层次 ▾]  🔍 搜索  [导出 PNG] [导出 JSON]│  │
│  │                                                              │  │
│  │         ┌──────────────┐                                     │  │
│  │         │ 📦 成果版本   │                                     │  │
│  │         │ v2 批次峰值   │                                     │  │
│  │         └──────┬───────┘                                     │  │
│  │           ┌────┴────┐                                        │  │
│  │      ┌────┴────┐ ┌──┴──────┐ ┌───────┐                      │  │
│  │      │📊 数据v1│ │📈 图表v1│ │💡Insight│                      │  │
│  │      └────┬────┘ └────┬────┘ └───┬───┘                      │  │
│  │      ┌────┴────┐     │          ┌─┴─────┐                    │  │
│  │      │▶ Run #3│     │          │📚知识库│                    │  │
│  │      │[succeeded]│   │          │引用快照│                    │  │
│  │      └────┬────┘   ┌┴─────┐    └───────┘                    │  │
│  │      ┌────┴────┐   │Run #5│                                  │  │
│  │      │📋 快照#2│   └──┬───┘                                  │  │
│  │      └────┬────┘    ┌─┴─────┐                                │  │
│  │      ┌────┴────┐   │快照#3 │                                │  │
│  │      │🔬 Fact-A│   └──┬────┘                                │  │
│  │      │ 拉伸测试│    ┌─┴─────┐                                │  │
│  │      └─────────┘   │🔬Fact-B│                                │  │
│  │                    └───────┘                                  │  │
│  │      ┌─────┐                                                 │  │
│  │      │🔒受限│  ← 无权访问的上游来源                            │  │
│  │      │来源  │                                                 │  │
│  │      └─────┘                                                 │  │
│  │                                                              │  │
│  │  [展开更多层级]                                               │  │
│  └──────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

- **节点统计栏**：展示总节点数、各类型节点数、受限节点数
- **控制栏**：深度选择（默认 5 层）、布局切换、搜索、导出
- **溯源图**：以 DAG 可视化展示，从成果版本节点向下展开
  - 📦 成果版本（紫色）
  - 📊 衍生数据（蓝色）
  - 📈 图表（青色）
  - 💡 Insight（橙色）
  - ▶ 分析运行（绿色）
  - 📋 证据快照（浅蓝）
  - 🔬 实验事实（深蓝，可跳转原系统）
  - 📚 知识库引用（紫色，可展开快照）
  - 🔒 受限来源（灰色，不可点击）
- **节点交互**：可见节点可点击跳转到对应详情；受限节点不可点击
- **展开更多**：超过当前深度的节点显示"展开更多层级"按钮

### 4.2 产物溯源视图（Workspace 内）

```
┌──────────────────────────────────────────────────────────┐
│  ◀ 返回    批次特征提取结果 (Derived Dataset)            │
├──────────────────────────────────────────────────────────┤
│                                                            │
│  名称: 批次特征提取结果          [✎ 编辑]                  │
│  ...                                                       │
│                                                            │
│  ┌── 数据溯源 ────────────────────────────────────────┐   │
│  │                                                      │   │
│  │  📊 节点统计: 总 6 | 实验 2 | 快照 1 | 运行 1 | 产物 1│   │
│  │                                                      │   │
│  │  深度: [5层 ▾]                [导出 PNG] [导出 JSON]  │   │
│  │                                                      │   │
│  │         ┌──────────────┐                              │   │
│  │         │ 📊 衍生数据   │                              │   │
│  │         │ v1 批次特征  │                              │   │
│  │         └──────┬───────┘                              │   │
│  │           ┌────┴────┐                                 │   │
│  │           │▶ Run #3 │                                 │   │
│  │           │[succeeded]│                                │   │
│  │           └────┬────┘                                 │   │
│  │           ┌────┴────┐                                 │   │
│  │           │📋 快照#2│                                 │   │
│  │           └────┬────┘                                 │   │
│  │      ┌─────┴─────┐                                   │   │
│  │      │🔬 Fact-A  │  🔬 Fact-B                         │   │
│  │      │ 拉伸测试  │  压缩测试                           │   │
│  │      └───────────┘                                    │   │
│  │                                                      │   │
│  └──────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────┘
```

- 产物详情页（DerivedDataset / ResearchView / Insight）新增"数据溯源"区域
- 溯源图从该产物当前版本节点开始向上游追溯
- 展示范围和交互与成果详情页溯源 Tab 一致

### 4.3 知识库引用快照查看（Insight 详情）

```
┌──────────────────────────────────────────────────────────┐
│  ◀ 返回    温度波动结论 (Insight)                        │
├──────────────────────────────────────────────────────────┤
│                                                            │
│  证据来源: [📚 知识库]                                     │
│  结论: 批次B-003的峰值异常源于温度波动...                  │
│  ...                                                       │
│                                                            │
│  ┌── 知识库引用快照 (2) ──────────────────────────────┐   │
│  │                                                      │   │
│  │  📄 铝合金热处理工艺规范                              │   │
│  │  文档版本: v3 | 检索时间: 2026-08-06 14:25           │   │
│  │  来源: knowledge-provider-internal                  │   │
│  │  ┌──────────────────────────────────────────────┐   │   │
│  │  │ "当温度梯度超出 ±5°C 控制窗口时，铝合金峰值  │   │   │
│  │  │ 压力可能出现 10-15% 的偏移..."                │   │   │
│  │  │ (Section 4.2, Page 23, Chunk 7)              │   │   │
│  │  └──────────────────────────────────────────────┘   │   │
│  │  content_hash: 7a2b...                                │   │
│  │  [查看来源文档 →]                                     │   │
│  │                                                      │   │
│  │  📄 材料力学性能手册                                  │   │
│  │  文档版本: v5 | 检索时间: 2026-08-06 14:25           │   │
│  │  来源: knowledge-provider-internal                  │   │
│  │  ┌──────────────────────────────────────────────┐   │   │
│  │  │ "峰值偏移与温度敏感性呈正相关关系..."         │   │   │
│  │  │ (Section 2.1, Page 12, Chunk 3)              │   │   │
│  │  └──────────────────────────────────────────────┘   │   │
│  │  content_hash: 3f8c...                                │   │
│  │  [查看来源文档 →]                                     │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                            │
│  ⚠ 管理员可见: 完整快照内容 | 普通用户可见: 文档标题和来源链接│
└──────────────────────────────────────────────────────────┘
```

- Insight 详情中展示关联的 KnowledgeReference 列表
- 每条引用展示：文档标题、文档版本、检索时间、来源 provider、引用段落文本、位置信息（Section/Page/Chunk）、content_hash
- `research:manage` 权限用户可查看完整快照内容（snippet_text）
- 普通用户仅可见文档标题和来源链接（不展示完整段落文本）
- "查看来源文档"链接跳转到 source_uri（需外部系统支持）

### 4.4 右栏 AI 助手 — 知识库检索声明

```
┌──────────────────────────────────────────┐
│  AI 科研助手                               │
│                                            │
│  ...                                      │
│                                            │
│  ┌── 覆盖声明 ──────────────────────────┐ │
│  │ 自动模式: 混合分析                     │ │
│  │ 数据覆盖率: 100% | LLM 阅读率: 100%    │ │
│  │ 知识库检索: ✅ 已检索 (2 篇文献)        │ │
│  │ 是否抽样: 否 | 预计 4 批               │ │
│  └────────────────────────────────────────┘ │
│                                            │
└──────────────────────────────────────────┘
```

- 覆盖声明区（阶段 2 已实现）新增"知识库检索"状态
- 状态取值：✅ 已检索（N 篇文献）/ ⚠ 降级（知识库不可用）/ — 不适用
- 知识库不可用时标注降级状态和原因

---

## 5. 待确认问题

| # | 问题 | 影响范围 | 建议 |
|---|------|---------|------|
| Q1 | **CoreProvenanceAdapter 的核心节点覆盖范围**：设计文档提到查询 Fact、现有 DerivationRun、ParameterVersion 等核心节点。是否需要覆盖 EvidenceSet / EvidenceSetVersion？这些是 L2.5 Provenance 的实体，研究模块是否需要追溯到 L2.5 层级？ | P0-1, P0-4 | 建议首期覆盖 Fact 和现有 DerivationRun（作为 Fact 的上游推导），ParameterVersion 暂不接入（研究分析通常不直接引用参数版本）。EvidenceSet / EvidenceSetVersion 可作为 Fact 的上游节点展示（如果存在 L2.5 推导链），但不在首期强制实现。CoreProvenanceAdapter 接口预留扩展能力。 |
| Q2 | **溯源图的图布局技术选型**：溯源图可视化的前端渲染使用 ECharts Graph、AntV G6 还是 D3.js？需要支持 DAG 层次布局、节点折叠/展开、大量节点性能。 | P0-17, P0-18 | 建议使用 AntV G6（与 Ant Design 生态一致，原生支持 DAG 布局和大规模图渲染）。阶段 1-3 已使用 ECharts 做交互图表，但溯源图更适合专用图可视化库。 |
| Q3 | **KnowledgeProvider 的具体实现来源**：设计文档说"消费用户在其他系统维护的只读 KnowledgeProvider"。具体是哪些系统？是否已有可接入的知识库？还是需要提供一个 Mock 实现用于测试？ | P0-10, P0-11 | 建议首期定义接口合同和 Mock 实现，同时提供一个内部 KnowledgeProvider 适配器（对接 IRIP 现有文档管理系统如果存在）。具体外部知识库接入在后续根据实际系统对接。 |
| Q4 | **知识引用快照的存储位置和大小控制**：引用快照保存段落文本（snippet_text），可能包含较长文本。快照存储在 MinIO 还是 PostgreSQL？单条快照的最大长度限制？ | P0-12, P0-13 | 建议短文本（≤4KB）直接存储在 PostgreSQL research_knowledge_reference 表的 snippet_text 字段；长文本（>4KB）存储到 MinIO（`research/knowledge_refs/`），表中存路径。单条快照限制 64KB（超出截断并标注）。 |
| Q5 | **溯源边的补充创建时机**：阶段 5 需要补充 Analysis Run 执行过程和产物创建过程中的溯源边。这些边是在阶段 5 新增代码中创建，还是需要回溯修改阶段 1-3 的代码在已有流程中插入边创建逻辑？ | P0-21 | 建议采用事件驱动方式：在 ResearchOrchestrator 的关键事件（快照冻结、Run 启动、步骤完成、产物确认）中新增溯源边创建逻辑。由于阶段 1-3 已上线，需要通过 Event Hook 或 Service 包装层在已有流程的关键节点插入边创建调用，尽量不修改已有核心代码逻辑。 |
| Q6 | **跨边界边的创建时机**：`core:fact:<id> → research:evidence_snapshot:<id>` 这条跨边界边应该在什么时机创建？是在证据选择时（加入 Evidence Set 时），还是在快照冻结时？ | P0-22 | 建议在快照冻结时创建（EvidenceSnapshotService.freeze_snapshot 中），因为此时确定了实际使用的 Fact ID 和版本。证据选择时 Fact 可能被移除，不宜提前创建边。 |
| Q7 | **KnowledgeProvider 检索的 token 消耗归属**：知识库检索是否消耗 500K 数据预算？检索返回的片段是否计入 LLM 上下文？ | P0-14 | 建议知识库检索本身不消耗 500K 数据预算（检索是独立步骤，不是 LLM 调用）。但检索返回的片段在后续 LLM 步骤中作为上下文输入时，计入该步骤的有效数据预算。覆盖声明中知识库检索覆盖率与数据覆盖率分开统计。 |
| Q8 | **溯源图查询的性能基准**：一个成果版本可能有 10+ 个产物、5+ 个 Run、3+ 个 Snapshot、多个 Fact。溯源图查询的目标响应时间？是否需要缓存？ | P0-3, P0-6 | 建议目标响应时间 P95 < 2 秒（含权限裁剪）。对热点成果包的溯源图查询结果可缓存（按 target + principal 权限维度缓存，TTL 5 分钟，权限变化时失效）。首期可不实现缓存，性能不达标时再增加。 |

---

## 6. 技术实现要点（供架构师参考）

### 6.1 后端包结构

```
packages/research/
├── entities.py          # ORM: 新增 ResearchKnowledgeReference
├── models.py            # 数据类: ProvenanceNode / ProvenanceEdge / ProvenanceGraph /
│                        #        RestrictedNode / KnowledgeSearchResult /
│                        #        KnowledgeDocument / KnowledgeReferenceRef /
│                        #        NodeDisplayLabel / LineageEdgeRef
├── provenance.py        # 业务编排: UnifiedProvenanceQueryService（联邦溯源查询编排）
├── adapters/
│   ├── core_provenance.py    # CoreProvenanceAdapter（只读核心 Provenance 适配器）
│   └── research_lineage.py  # ResearchLineageAdapter（只读研究 Lineage 适配器）
├── knowledge.py         # 业务编排: KnowledgeProviderService（知识库检索编排 + 引用快照保存）
├── knowledge_provider.py    # 接口合同: KnowledgeProvider Protocol（search / get_document）
├── lineage_writer.py    # 溯源边写入: LineageWriterService（在关键事件中创建溯源边）
└── ...（阶段 1-4 已有文件保持不变）
```

### 6.2 API 路由

```
apps/api/routers/research_lineage.py
├── # ── 联邦溯源查询 ──
├── GET    /api/v1/research/provenance/graph
│         # 查询联邦溯源图（query: target_namespace, target_id, max_depth?）
│         # 返回 ProvenanceGraph（nodes + edges + stats）
├── GET    /api/v1/research/provenance/graph/result/{result_id}/version/{version_number}
│         # 查询成果版本的溯源图（便捷端点）
├── GET    /api/v1/research/provenance/graph/dataset/{dataset_id}/version/{version_number}
│         # 查询衍生数据版本的溯源图（便捷端点）
├── GET    /api/v1/research/provenance/graph/view/{view_id}/version/{version_number}
│         # 查询 View 版本的溯源图（便捷端点）
├── GET    /api/v1/research/provenance/graph/insight/{insight_id}/version/{version_number}
│         # 查询 Insight 版本的溯源图（便捷端点）
├── GET    /api/v1/research/provenance/node/{namespace}/{node_id}
│         # 查询单个溯源节点详情（校验权限）
│
├── # ── 知识库检索 ──
├── GET    /api/v1/research/knowledge/search
│         # 检索知识库（query: search_query, provider_name?）
│         # 返回 KnowledgeSearchResult 列表
├── GET    /api/v1/research/knowledge/references/{insight_id}
│         # 查看 Insight 关联的知识引用快照列表（需 research:manage 权限查看完整内容）
├── GET    /api/v1/research/knowledge/references/{reference_id}
│         # 查看单个知识引用快照详情（需 research:manage 权限查看完整内容）
│
├── # ── 溯源导出 ──
├── POST   /api/v1/research/provenance/graph/export
│         # 导出溯源图（body: {target_namespace, target_id, format: png/json}）
│         # 返回导出文件下载链接
```

路由注册在 `apps/api/main.py` 中受功能开关控制（延续阶段 1-4 模式）。

### 6.3 前端结构

```
apps/web/src/features/research/
├── ...（阶段 1-4 已有组件保持不变）
├── ProvenanceGraphView.tsx       # 联邦溯源图可视化组件（AntV G6）
├── ProvenanceNodeCard.tsx        # 溯源节点卡片（类型标签 + 名称 + 跳转链接）
├── RestrictedNodeCard.tsx        # 受限占位节点卡片（"受限来源"）
├── ProvenanceControls.tsx        # 溯源图控制栏（深度/布局/搜索/导出）
├── ProvenanceStats.tsx           # 节点统计摘要
├── ResultProvenanceTab.tsx        # 成果详情页溯源 Tab（激活）
├── ProductProvenanceSection.tsx  # 产物溯源视图（Workspace 内）
├── KnowledgeReferenceList.tsx    # 知识引用快照列表组件
├── KnowledgeReferenceCard.tsx   # 知识引用快照卡片
├── KnowledgeSearchStatus.tsx     # 知识库检索覆盖声明组件
└── api/
    └── researchLineage.ts        # 溯源和知识库相关 API 函数
```

### 6.4 数据库表设计概要

**research_knowledge_reference**
- `id` (UUID PK), `workspace_id` (FK→research_workspace CASCADE), `run_id` (FK→research_analysis_run), `step_id` (FK→research_analysis_step nullable), `insight_id` (UUID nullable, 逻辑引用 research_insight), `document_id` (TEXT), `document_version` (TEXT), `title` (TEXT), `section` (TEXT nullable), `page` (INT nullable), `chunk_id` (TEXT nullable), `snippet_text` (TEXT nullable, ≤4KB 直接存储), `snippet_storage_path` (TEXT nullable, >4KB 存 MinIO), `content_hash` (TEXT), `source_uri` (TEXT), `retrieval_time` (UTCDateTime), `provider_name` (TEXT), `research_question_context` (TEXT nullable), `created_at` (UTCDateTime)
- 仅追加：创建后不允许 UPDATE/DELETE
- 索引：`(insight_id)` 和 `(run_id, step_id)` 和 `(document_id, document_version)`
- 引用快照存储在 MinIO 时路径为 `research/knowledge_refs/{workspace_id}/{run_id}/{reference_id}.json`

**research_lineage_edge**（阶段 4 已创建，阶段 5 补充数据）
- 表结构不变
- 阶段 5 新增 edge_type：`knowledge_ref_to_insight`（知识引用 → Insight）
- 阶段 5 在关键事件中补充创建溯源边（见 P0-21）

> **注意**：1 张新表以 `research_` 前缀命名，延续 `research_*` 命名空间。迁移编号延续 `0078`（阶段 1 为 `0074`，阶段 2 为 `0075`，阶段 3 为 `0076`，阶段 4 为 `0077`）。核心表无到研究溯源表的外键。

### 6.5 UnifiedProvenanceQueryService 查询逻辑

```
UnifiedProvenanceQueryService.query_provenance_graph(
    target_namespace, target_id, principal, options
) 流程：

1. 确定起始节点：根据 target_namespace 路由到对应 Adapter
   - core:* → CoreProvenanceAdapter
   - research:* → ResearchLineageAdapter

2. 图拼接（BFS 从 target 向上游追溯）：
   a. 初始化：队列 = [(target_namespace, target_id, depth=0)]
   b. 已访问集合 = {}，节点列表 = [], 边列表 = []
   c. 循环直到队列为空或达到 max_depth：
      i.   出队 (ns, id, depth)
      ii.  若 (ns, id) 已在已访问集合中 → 跳过（循环保护）
      iii. 标记 (ns, id) 为已访问
      iv.  根据 ns 路由到对应 Adapter
      v.   Adapter.query_node(ns, id) → 节点信息
      vi.  Adapter.query_incoming_edges(ns, id) → 入边列表
      vii. 将节点和入边加入结果
      viii.对每条入边的 source 节点：
           - 若 source 属于另一个命名空间域 → 路由到对应 Adapter
           - 入队 (source_ns, source_id, depth+1)
           - 若 depth+1 > max_depth → 标记为"已截断"，不入队

3. 权限裁剪：
   a. 对节点列表中每个节点，校验 principal 的访问权限
   b. 无权访问的节点替换为 RestrictedNode：
      - 原节点信息移除（名称、ID、属性、内容）
      - 替换为 {node_type: "restricted", display_label: "受限来源", attributes: {}}
   c. 若权限策略配置为 truncate_branch：移除该节点及其全部上游分支
   d. 边列表中涉及被替换节点的边保留（target 端替换为受限占位节点的临时 ID）

4. 生成展示标签：
   a. 为每个可见节点生成 display_label / node_type_label / version_summary
   b. 为每条边生成 edge_type_label

5. 统计信息：
   - total_nodes, nodes_by_type, restricted_nodes_count, truncated_count

6. 返回 ProvenanceGraph(nodes, edges, stats)
```

### 6.6 CoreProvenanceAdapter 接口

```python
class CoreProvenanceAdapter(Protocol):
    """只读核心 Provenance 适配器。

    查询核心系统的 Fact、DerivationRun、EvidenceSet 等节点，
    不修改核心表，不暴露核心数据库会话。
    """

    def query_node(self, namespace: str, node_id: UUID) -> ProvenanceNode | None:
        """查询单个核心节点的展示信息。

        namespace 取值: core:fact / core:derivation_run / core:evidence_set
        返回节点类型标签和展示属性（名称、类型、版本摘要）。
        不返回节点的内容数据（如 Fact 的 points/series）。
        """

    def query_incoming_edges(self, namespace: str, node_id: UUID) -> list[ProvenanceEdge]:
        """查询节点的入边（上游来源）。

        对于 core:fact: 通常无上游（Fact 是实验事实，是溯源链的根）
        对于 core:derivation_run: 上游可能为 EvidenceSet / EvidenceSetVersion
        对于 core:evidence_set: 上游为其他 Fact 或 DerivationRun
        """

    def check_permission(self, namespace: str, node_id: UUID, principal: Principal) -> bool:
        """校验 principal 对核心节点的访问权限。

        复用核心系统现有权限校验逻辑。
        """
```

### 6.7 ResearchLineageAdapter 接口

```python
class ResearchLineageAdapter(Protocol):
    """只读研究 Lineage 适配器。

    查询研究域的 Evidence Snapshot、Analysis Run、Derived Dataset、
    View、Insight、成果版本等节点和溯源边。
    """

    def query_node(self, namespace: str, node_id: UUID) -> ProvenanceNode | None:
        """查询单个研究域节点的展示信息。

        namespace 取值:
        - research:evidence_snapshot
        - research:analysis_run
        - research:analysis_step
        - research:derived_dataset / research:derived_dataset_version
        - research:view / research:view_version
        - research:insight / research:insight_version
        - research:result_version
        - research:workspace
        - research:knowledge_reference
        """

    def query_incoming_edges(self, namespace: str, node_id: UUID) -> list[ProvenanceEdge]:
        """查询节点的入边（上游来源）。

        从 research_lineage_edge 表查询 target_namespace + target_id 匹配的边。
        跨边界边（source_namespace 为 core:*）由本方法返回，
        统一服务根据 source_namespace 路由到 CoreProvenanceAdapter 继续追溯。
        """

    def check_permission(self, namespace: str, node_id: UUID, principal: Principal) -> bool:
        """校验 principal 对研究域节点的访问权限。

        复用阶段 1-4 的权限校验逻辑：
        - Evidence Snapshot: 校验源数据当前权限
        - Analysis Run: 校验 Workspace 归属或成果包 ACL
        - 产物: 校验成果包 ACL（如已发布）或 Workspace 归属（如未发布）
        - 成果版本: 校验成果包 ACL
        - Knowledge Reference: 校验关联 Insight 的访问权限
        """
```

### 6.8 KnowledgeProvider 接口合同

```python
class KnowledgeProvider(Protocol):
    """外部知识库只读检索接口合同。

    研究模块不维护知识库内容，只消费只读接口。
    """

    async def search(
        self, query: str, options: KnowledgeSearchOptions | None = None
    ) -> list[KnowledgeSearchResult]:
        """检索知识库。

        query: 研究问题和用户确认的关键词（不含 Fact 原始数据）。
        options: max_results, filter_tags, timeout 等。
        返回按 relevance_score 排序的检索结果列表。
        """

    async def get_document(self, document_id: str) -> KnowledgeDocument | None:
        """获取文档元数据（不含全文内容）。

        返回 document_id / document_version / title / source_uri 等。
        """

    async def health_check(self) -> bool:
        """健康检查，用于平台定期检测 provider 可用性。
        """


@dataclass
class KnowledgeSearchResult:
    document_id: str
    document_version: str
    title: str
    section: str | None
    page: int | None
    chunk_id: str | None
    relevance_score: float
    source_uri: str
    content_hash: str
    snippet: str  # 精确检索片段
```

### 6.9 溯源边补充创建逻辑

```
LineageWriterService 在关键事件中创建溯源边：

1. 证据快照冻结时（EvidenceSnapshotService.freeze_snapshot）：
   - 对每个 source_ref 创建边:
     {source_namespace} → research:evidence_snapshot
     (如 core:fact:<id> → research:evidence_snapshot:<id>)
     (如 research:published_derived:<id> → research:evidence_snapshot:<id>)

2. Analysis Run 启动时：
   - 创建边: research:evidence_snapshot:<id> → research:analysis_run:<id>

3. Analysis Step 完成时：
   - 创建边: research:analysis_run:<id> → research:analysis_step:<id>

4. 产物确认时（ProductService.create_derived_dataset 等）：
   - 创建边: research:analysis_run:<id> → research:derived_dataset:<id>
   - 创建边: research:analysis_run:<id> → research:view:<id>
   - 创建边: research:analysis_run:<id> → research:insight:<id>

5. 知识引用保存时：
   - 创建边: research:knowledge_reference:<id> → research:insight:<id>
     (edge_type: knowledge_ref_to_insight)

6. 成果发布时（阶段 4 已实现）：
   - 创建边: research:workspace → research:result_version
   - 创建边: research:derived_dataset_version → research:result_version
   - 创建边: research:view_version → research:result_version
   - 创建边: research:insight_version → research:result_version

所有边仅追加（append-only），创建后不可修改或删除。
```

### 6.10 权限裁剪逻辑

```
权限裁剪在图拼接完成后统一执行：

1. 对 ProvenanceGraph 中每个节点：
   a. 根据 namespace 路由到对应 Adapter
   b. Adapter.check_permission(namespace, node_id, principal)
   c. 若无权限 → 替换为 RestrictedNode

2. RestrictedNode 生成规则：
   - 临时 ID: "restricted_{index}"（每次查询重新生成，不可枚举）
   - display_label: "受限来源"
   - node_type: "restricted"
   - attributes: {} (空)
   - 不保留原节点的任何信息

3. 截断分支（truncate_branch）：
   - 若权限策略配置为截断（而非占位）：
     a. 移除无权节点
     b. 递归移除该节点的全部上游分支
     c. 移除涉及被截断节点的边
   - 截断后的图可能比占位模式更小，但不会暴露任何存在信息

4. 权限校验的来源：
   - 核心节点: CoreProvenanceAdapter.check_permission（复用核心权限系统）
   - 研究节点: ResearchLineageAdapter.check_permission（复用阶段 1-4 权限逻辑）
   - 动态校验: 不依赖创建时的静态权限快照
```

### 6.11 与阶段 1-4 的集成点

| 阶段 1-4 组件 | 阶段 5 集成方式 |
|------------|---------------|
| ResearchLineageEdge（阶段 4 已创建表） | ResearchLineageAdapter 读取溯源边；LineageWriterService 补充创建缺失的溯源边 |
| EvidenceSnapshotService（阶段 1） | 冻结快照时通过 LineageWriterService 创建 fact → snapshot 跨边界边 |
| ResearchOrchestrator（阶段 2） | Run 启动、步骤完成时通过 LineageWriterService 创建溯源边 |
| ProductService（阶段 3） | 产物确认时通过 LineageWriterService 创建 run → product 溯源边 |
| PublicationService（阶段 4） | 发布时已创建溯源边（阶段 4 已实现），阶段 5 不修改 |
| ModelGateway（阶段 2） | 知识库检索步骤通过 KnowledgeProviderService 调用 KnowledgeProvider；检索结果保存为 KnowledgeReference |
| ContextRouter（阶段 2） | 知识库检索步骤标记为 "keyword_only" 模式（不发送 Fact 原始数据） |
| InsightVersion（阶段 3） | evidence_source_label = knowledge_base 的 Insight 关联 KnowledgeReference |
| 成果详情页"溯源"Tab（阶段 4 预留） | 阶段 5 激活为完整联邦溯源图（ResultProvenanceTab） |
| 成果详情页 P2-4 受限溯源节点预览（阶段 4 预留） | 阶段 5 完整实现受限占位节点 |
| ResearchMemoryService（阶段 2 已交付） | 知识引用事件更新研究记忆文档（新增 knowledge.referenced 事件类型） |
| CoreFactProvider（阶段 1） | CoreProvenanceAdapter 可复用 CoreFactProvider 的只读查询逻辑 |
| 权限模型（阶段 1-4） | 溯源图权限裁剪复用现有权限校验；`research:manage` 控制知识引用快照完整查看 |

### 6.12 审计事件命名

| 操作 | action 字符串 | resource_type |
|------|--------------|---------------|
| 溯源图查询 | `research.provenance.query` | `research_provenance_graph` |
| 溯源图导出 | `research.provenance.export` | `research_provenance_graph` |
| 知识库检索 | `research.knowledge.search` | `research_knowledge_reference` |
| 知识引用快照查看 | `research.knowledge.reference.view` | `research_knowledge_reference` |
| 溯源边创建 | `research.lineage.edge_created` | `research_lineage_edge` |
| KnowledgeProvider 降级 | `research.knowledge.provider_degraded` | `research_knowledge_provider` |

### 6.13 节点命名空间与类型标签映射

| 命名空间 | 节点类型标签 | 图标 | 跳转目标 |
|---------|------------|------|---------|
| `core:fact` | 实验事实 | 🔬 | 原系统 Fact 详情页 |
| `core:derivation_run` | 核心推导 | ⚙️ | 原系统推导详情（如存在） |
| `core:evidence_set` | 证据集 | 📋 | 原系统证据集详情（如存在） |
| `research:evidence_snapshot` | 证据快照 | 📋 | 快照摘要弹窗（无独立详情页） |
| `research:analysis_run` | 分析运行 | ▶️ | Workspace Run 详情 |
| `research:analysis_step` | 分析步骤 | ▶️ | Run 详情中的步骤 |
| `research:derived_dataset` | 衍生数据 | 📊 | 产物详情 |
| `research:derived_dataset_version` | 衍生数据版本 | 📊 | 产物版本详情 |
| `research:view` | 图表 | 📈 | 产物详情 |
| `research:view_version` | 图表版本 | 📈 | 产物版本详情 |
| `research:insight` | Insight | 💡 | 产物详情 |
| `research:insight_version` | Insight 版本 | 💡 | 产物版本详情 |
| `research:result_version` | 成果版本 | 📦 | 成果详情页 |
| `research:workspace` | 研究空间 | 🏠 | Workspace 页面 |
| `research:knowledge_reference` | 知识库引用 | 📚 | 引用快照详情（需权限） |
| `restricted` | 受限来源 | 🔒 | 不可跳转 |
