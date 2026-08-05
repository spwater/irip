# IRIP 系统架构概览

> 版本：0.8.0 · 覆盖 Phase V0–V3
> 关联文档：`docs/arch-v0.md`、`docs/arch/v2-architecture.md`、`docs/arch/v3-architecture.md`
> 关联图表：`docs/class-diagram.mermaid`、`docs/sequence-diagram.mermaid`

---

## 1. 架构模式：模块化单体 + Worker

IRIP 采用 **FastAPI 模块化单体 + Celery Worker + React 控制台** 的经典三段式架构。

- **单体 API**（`apps/api`）：所有领域路由集中在同一 FastAPI 进程，通过 `packages/*` 领域包实现逻辑隔离。单体降低了分布式事务复杂度，同时包级隔离为未来拆分微服务保留了边界。
- **Worker**（`apps/worker`）：Celery Worker 处理耗时异步作业（推导运行、流程执行、模型训练、备份恢复），通过 PostgreSQL Outbox + 租约 + 幂等提交保证可恢复性。
- **Web 控制台**（`apps/web`）：React + Ant Design 前端，TanStack Router/Query 实现路由与数据获取。

```
┌──────────────────────────────────────────────────────────────┐
│                    apps/web (React 控制台)                     │
│  研发 | 标准 | 事实 | 参数 | 组件 | 流程 | 模型 | AI助手         │
│  治理 | 用户 | 授权 | 审计 | 健康 | 作业中心                    │
├──────────────────────────────────────────────────────────────┤
│                    apps/api (FastAPI 单体)                     │
│  auth · standards · facts · provenance · parameters           │
│  components · flows · models · assistant · governance        │
│  audit · backups · health                                    │
├──────────────────────────────────────────────────────────────┤
│                    apps/worker (Celery)                        │
│  derivation · flows · models · backups                       │
├──────────────────────────────────────────────────────────────┤
│  packages/common  auth  audit  jobs  connectors               │
│  standards  facts  provenance  parameters                     │
│  components  models  ai                                      │
├──────────────────────────────────────────────────────────────┤
│  deployments/compose (Dockerfile · bootstrap · backup · restore)│
└──────────────────────────────────────────────────────────────┘
```

---

## 2. 基础设施

### 2.1 PostgreSQL 16（权威存储）

- **角色**：唯一权威存储。所有业务数据（标准/事实/参数/组件/流程/模型/AI 对话/审计/作业）均持久化于此。
- **扩展**：pgvector（向量检索，为 AI 语义搜索预留）。
- **事务**：所有写操作走 `session_scope(factory)`，事务级自动 commit/rollback。
- **Outbox 模式**：业务写 + 事件投递同事务插入 `outbox_event`，保证消息不丢。

### 2.2 Redis 7（缓存 / 队列）

- **角色**：Celery broker + 结果后端。非权威存储——可丢失、可重建。
- **不纳入备份**：任务可重放，会话可重建（详见 `docs/operations/backup-restore.md`）。
- **韧性**：Redis 宕机时 API 降级运行（Outbox 事件持久化在 PostgreSQL，Redis 恢复后自动重投）。

### 2.3 MinIO（对象存储）

- **角色**：S3 兼容内容寻址存储。存储原始工件（Excel/CSV/PDF）、模型文件、备份归档。
- **内容寻址**：对象键为 `sha256/<前2位>/<digest>`，相同内容自动去重。
- **去重设计**：`artifact_blob`（SHA-256 主键）+ `artifact`（业务引用），多 artifact 共享同一 blob。

---

## 3. V0–V3 各层职责

### V0：平台骨架

| 模块 | 职责 |
|------|------|
| `packages/common` | ID 生成（UUIDv7）、时钟注入、错误契约、哈希、分页游标、数据库会话、工件服务、S3 仓库 |
| `packages/auth` | 用户认证（Argon2id）、JWT 签发、刷新令牌家族化旋转 + 重放检测、RBAC 7 角色、对象级 ScopeGrant |
| `packages/audit` | 审计事件仅追加写入 + 敏感字段脱敏（REVOKE UPDATE/DELETE） |
| `packages/jobs` | 异步作业（Outbox + 租约 + 幂等提交）、Worker 租约获取/心跳/续期 |
| `apps/api` | FastAPI 应用工厂、CORS、异常→AppError 映射、认证/上传/作业/健康路由 |
| `apps/web` | React 登录页、路由守卫、作业抽屉、API 客户端（自动 401→refresh→retry） |

### V1：粒度分析全链路

| 模块 | 职责 |
|------|------|
| `packages/standards` | L1 标准层：变量注册、单位仿射转换、状态机（draft→published→deprecated）、不可变版本 |
| `packages/facts` | L2 事实层：事实创建、不可变修订、观察值标准化、质量评估引擎 |
| `packages/provenance` | L2.5 溯源层：证据集冻结、推导配方版本化、确定性回放、BFS 溯源图 |
| `packages/parameters` | L3 参数层：条件引擎、候选审批分离（self_approval_forbidden）、不可变发布、过期检测 |
| `packages/connectors` | 数据连接器（PostgreSQL/REST/File）+ MappingProfile 字段映射 |

### V2：组件系统 + 流程引擎 + 模型生命周期

| 模块 | 职责 |
|------|------|
| `packages/components` | 组件 SDK（Context/Result/Protocol）、清单验证、注册表（不可变版本）、Python/CLI 执行器、流程引擎（DAG 校验 + 节点级执行） |
| `packages/models` | 模型契约（JSON Schema）、CLIModelAdapter、模型状态机（draft→published→deprecated）、适用域检查、预测写 model_execution 事实 |
| 25 个内置组件 | 7 摄入 + 7 映射转换 + 4 质量 + 4 统计 + 3 输出 + 4 模型组件 |

### V3：AI 助手 + 治理控制台 + 备份恢复

| 模块 | 职责 |
|------|------|
| `packages/ai` | AIProvider 协议（OpenAI 兼容 + 离线确定性模拟）、7 个只读工具白名单、引用可溯源、AIService 对话编排 |
| 治理 API | 用户管理、角色分配、范围授权管理、审计事件只读查询、作业监控、系统健康仪表盘 |
| `deployments/compose` | 备份脚本（pg_dump + MinIO sync）、恢复脚本（SHA-256 完整性校验）、BackupManifest |

---

## 4. 数据流图

### 4.1 粒度分析数据流（V1 核心链路）

```
原始数据文件 (Excel/CSV/PDF)
    │
    ▼
[数据摄入组件] ──文件读取──▶ ObservationTable
    │
    ▼
[字段映射组件] ──MappingProfile──▶ 标准化字段（映射到 L1 标准变量）
    │
    ▼
[质量检查组件] ──Schema/Range/Order──▶ DiagnosticReport
    │
    ▼
[FactService] ──create_fact()──▶ Fact + FactRevision (不可变)
    │                                   │
    │                                   ├─▶ QualityAssessment
    │                                   └─▶ FactArtifact (→ MinIO)
    │
    ▼
[证据集冻结] ──EvidenceSet.freeze()──▶ EvidenceSetVersion (不可变)
    │
    ▼
[推导配方] ──TransformationRecipe──▶ DerivationRun (确定性回放)
    │                                       │
    │                                       ▼
    │                              ParameterCandidate
    │                                       │
    ▼                                       ▼
[参数审批] ──审批分离──▶ ParameterVersion (不可变, status=published)
    │
    ▼
溯源图 (BFS: Parameter → DerivationRun → Fact → RawArtifact)
```

### 4.2 模型预测数据流（V2 核心链路）

```
研究员 → 预测工作台
    │
    ▼
选择模型 (published 状态) → GET /api/v1/models/{id} → ModelContract.input_schema
    │
    ▼
输入参数 → POST /api/v1/models/{version_id}/predict
    │
    ▼
ModelService.predict()
    ├─▶ ArtifactService.presign_download → 下载模型文件
    ├─▶ ModelAdapter.load(model_path, contract)
    ├─▶ ModelAdapter.validate_input(inputs)
    ├─▶ ApplicabilityChecker.check(inputs, domain) → ApplicabilityResult
    ├─▶ ModelAdapter.predict(inputs) → outputs
    └─▶ FactService.create_fact(fact_type=model_execution, derivation_ref=model_version_id)
         │
         ▼
    PredictionRecord (inputs + outputs + applicability + fact_id)
         │
         ▼
    溯源链接 (→ model_execution 事实 → 溯源图)
```

### 4.3 AI 助手数据流（V3 核心链路）

```
研究员 → POST /api/v1/assistant/conversations/{id}/messages (content)
    │
    ▼
AIService.send_message()
    ├─▶ INSERT ai_message (role=user)
    ├─▶ 加载对话历史 (ai_message WHERE conversation_id ORDER BY created_at)
    ├─▶ ToolRegistry.get_tool_schemas_for_llm() → 7 个工具 JSON Schema
    ├─▶ AIProvider.complete(AIRequest) → AIResponse
    │       ├─▶ [OpenAI 模式] httpx → POST {base_url}/v1/chat/completions
    │       └─▶ [离线模式] 关键词匹配 → 确定性响应 (无网络依赖)
    │
    ├─▶ IF tool_calls 非空:
    │       FOR each tool_call:
    │           ├─▶ ToolRegistry.is_allowed(tool_name) — 白名单强制校验
    │           ├─▶ ToolRegistry.execute_tool(name, args, org_id, user_id)
    │           │       └─▶ 调用 V0-V2 查询服务 (FactService/ParameterService/...)
    │           └─▶ CitationCollector.add_from_tool_result(result)
    │       AIProvider.complete(updated_request) → final_response
    │
    └─▶ INSERT ai_message (role=assistant, content, tool_calls, citations)
         │
         ▼
    200 OK (assistant message + citations + tool_traces)
         │
         ▼
    前端 CitationList → 点击引用 → 跳转溯源 (fact→/facts/{id}, model→/models/{id})
```

---

## 5. 技术选型决策记录

| 决策点 | 选型 | 理由 |
|--------|------|------|
| Web 框架 | FastAPI 0.115+ | 异步原生、OpenAPI 自动生成、生态成熟 |
| ORM | SQLAlchemy 2.0 async + psycopg 3 | 异步 ORM 标杆、PostgreSQL 原生驱动 |
| 任务队列 | Celery 5.4 + Redis | 工业标准异步队列，Redis 仅作 broker（非权威） |
| 前端 UI | React 18 + Ant Design 5 | 中文优先组件库，企业级控制台首选 |
| 前端路由/数据 | TanStack Router/Query | 类型安全路由 + 自动缓存/轮询 |
| 对象存储 | MinIO（S3 兼容） | 自托管、内容寻址、boto3 客户端复用 |
| 认证 | Argon2id + JWT + 家族化刷新 | 抗 GPU 破解 + 重放检测 + 无状态验证 |
| 组件沙箱 | subprocess + resource.setrlimit | 无需 Docker 容器隔离，降低部署复杂度 |
| AI 调用 | httpx 直连 OpenAI 兼容 REST API | 不引入 openai SDK，与 RestConnector 模式一致 |
| 备份 | subprocess pg_dump + aws s3 sync | 无需额外 Python SDK，复用 CLI 工具 |
| Python 版本 | >=3.12（放开上限） | 兼容本机 Python 3.13，不锁死上限 |
| pip 源 | 清华镜像 | 国内网络加速 |
| npm 源 | npmmirror | 国内网络加速 |

---

## 6. 安全设计要点

1. **RBAC + ScopeGrant**：7 个内置角色 + 对象级子树授权（`scope_grant` 表），授权在服务层强制。
2. **审计仅追加**：`audit_event` 表 `REVOKE UPDATE, DELETE`，payload 写入前脱敏。
3. **刷新令牌安全**：仅持久化 SHA-256 摘要 + 家族 ID + 单用途旋转，重放即整族撤销。
4. **AI 工具白名单**：7 个只读查询工具（search_facts/get_fact/list_parameters/get_parameter/list_models/get_model_detail/get_provenance），候选工具默认禁用，每次 tool_call 经 `is_allowed()` 校验。
5. **组件安全**：凭据以 secret_id 引用不内联明文；PostgreSQL 组件仅允许 SELECT；REST 组件 SSRF 防护（内网/环回地址拦截）。
6. **备份完整性**：每个备份组件附带 SHA-256 校验和，恢复前 `BackupManifestValidator` 验证，不匹配则中止。

---

## 7. 版本演进路线

| 阶段 | 交付内容 | 验收门 |
|------|---------|--------|
| V0 | 平台骨架（认证/授权/工件/作业/Outbox/前端外壳） | `tests/integration/test_v0_bootstrap.py` + E2E |
| V1 | 粒度分析全链路（L1→L2→L2.5→L3 证据链） | `docs/acceptance/v1-particle-size.md` |
| V2 | 组件系统 + 流程引擎 + 模型生命周期 + 篦冷机 ROM | `tests/acceptance/test_v2_model_execution.py` |
| V3 | AI 助手 + 治理控制台 + 备份恢复 + 安全/恢复/性能测试 | `docs/acceptance/final-release.md` + `scripts/release-gate.sh` |
