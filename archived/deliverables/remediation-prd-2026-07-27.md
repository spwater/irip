# IRIP 整改产品需求文档（PRD）

> **文档版本**：v1.0  
> **创建日期**：2026-07-27  
> **产品经理**：许清楚  
> **审阅基线**：`main` / `a35dd9559ad8a188300f57ca82b6e9ef9e999ac8`  
> **审阅报告**：`/Users/shuipei/Desktop/snowSP/2026-07-27-irip-comprehensive-code-review.md`  
> **当前成熟度判定**：内部 Alpha / 功能原型  
> **生产发布建议**：No-Go（P0 关闭前禁止接入生产数据）

---

## 1. 项目信息

| 项目 | 内容 |
|---|---|
| **Language** | 中文 |
| **Programming Language** | Python 3.12+ / TypeScript（现有技术栈不变） |
| **Project Name** | irip_remediation |
| **原始需求** | 基于外部专家综合代码审阅报告，对 IRIP 平台 24 项问题（F-01 至 F-24）进行结构化整改，将项目从"内部 Alpha / 功能原型"提升到"生产可用" |

### 1.1 原始需求复述

外部专家对 IRIP 平台进行了全面静态代码审阅，发现 **9 项 P0、10 项 P1、5 项 P2** 共 24 项问题。核心结论：项目"设计承诺与生产运行链之间存在明显断层"，横切约束（租户隔离、授权、不可变性、审计）未形成不可绕过的运行机制，文档完成度描述显著领先于真实代码。

本 PRD 的任务是：将 24 项审阅发现转化为结构化、可验收的整改需求，按 4 个阶段规划执行路线，并明确待决策问题。

---

## 2. 产品目标

| 编号 | 目标 | 衡量标准 |
|---|---|---|
| **G-1** | 消除专家审阅发现的所有 P0 问题（9 项） | 9 项 P0 需求全部通过验收标准；无已知跨租户数据破坏入口 |
| **G-2** | 将项目从"内部 Alpha / 功能原型"提升到"生产可用" | P0 + P1 全部关闭；发布准入标准（安全/数据正确性/可靠性/工程质量/运维）全部满足 |
| **G-3** | 建立不可绕过的租户、授权、不可变、审计控制面 | 数据库层 + 应用层双重强制；跨组织 API 交叉测试 100% 通过；不可变表数据库级拒绝改写 |

---

## 3. 用户故事

| 编号 | 角色 | 需求 | 价值 |
|---|---|---|---|
| **US-1** | 平台运维工程师 | 我希望默认部署不会自动启动恢复服务，这样我不会在部署时意外覆盖在线数据 | 消除最高破坏性风险 |
| **US-2** | 多组织管理员 | 我希望每个组织的数据严格隔离，这样 A 组织用户绝不能读写 B 组织数据 | 数据安全合规 |
| **US-3** | 合规审计员 | 我希望审计日志、事实修订、版本记录在数据库层不可篡改，这样监管审计时能信任历史记录 | 证据链可信 |
| **US-4** | 研究员 | 我希望异步作业（备份、恢复、模型训练）能可靠执行并可追踪状态，这样提交后能确认结果 | 业务可用性 |
| **US-5** | 安全工程师 | 我希望生产环境拒绝默认密钥并以最小权限运行，这样系统不会被已知凭据接管 | 系统安全 |

---

## 4. 需求池（按优先级分层）

### 4.1 P0 需求（Must Have — 立即处理，9 项）

#### F-01 [P0] 默认生产编排会自动启动恢复服务

| 项目 | 内容 |
|---|---|
| **编号** | F-01 |
| **优先级** | P0 |
| **标题** | 默认生产 Compose 会自动启动 restore，存在误恢复和覆盖在线数据风险 |
| **问题描述** | `compose.yaml` 中 `restore` 和 `backup` 服务没有 profile，`docker compose up` 会自动启动恢复容器，若挂载目录存在旧备份则覆盖在线数据库和对象存储 |
| **整改要求** | 1. 将 `restore` 从默认 Compose 服务集合移除；2. 使用独立 `compose.restore.yaml` 或加 `profiles: ["dangerous-ops"]`；3. 恢复前要求显式确认令牌、维护模式、目标环境校验和备份 ID；4. 默认拒绝对非空目标恢复，需双人审批和审计；5. `backup` 改为显式任务或真实调度任务 |
| **验收标准** | • 默认 `docker compose config --services` 不包含 `restore` • 不提供确认令牌时恢复命令非零退出 • 对非空目标恢复必须显式 override • E2E 演练证明普通部署不触发任何恢复写操作 |
| **涉及文件** | `compose.yaml`、`deployments/compose/restore.py`、`deployments/compose/backup.py` |
| **建议角色** | 平台/后端负责人 |

---

#### F-02 [P0] 跨组织更新与删除路径

| 项目 | 内容 |
|---|---|
| **编号** | F-02 |
| **优先级** | P0 |
| **标题** | 事实、流程、作业等实体的更新/删除路径缺少组织条件，存在跨组织数据破坏 |
| **问题描述** | 事实删除按 `fact_id`/`task_code` 直接删除无组织条件；流程更新按全局 ID 查询；Job 取消/详情按 job ID 不检查组织；组织解析失败 fail-open 回退到演示组织甚至生成随机 UUID |
| **整改要求** | 1. 立即禁用事实硬删除和流程硬删除入口；2. 所有 Repository 方法强制使用 `(organization_id, id)` 或 `TenantId` 值对象；3. 组织解析失败必须 fail closed，返回 401/403，不得回退或生成随机值；4. 为 Job、Fact、Flow、Audit、Backup、ScopeGrant 增加统一组织谓词；5. 评估 PostgreSQL RLS 作为第二道防线 |
| **验收标准** | • 每个按 ID 读写端点执行 A/B 组织交叉测试，必须返回 404 或 403 • SQL 捕获测试证明所有租户表查询包含组织条件 • 不存在任何生成临时组织 ID 的生产路径 |
| **涉及文件** | `apps/api/routers/facts.py`、`apps/api/routers/flows.py`、`packages/components/flow_runtime.py`、`packages/jobs/service.py`、`apps/api/main.py` |
| **建议角色** | 安全 + 后端 |

---

#### F-03 [P0] 证据链和版本不可变性未被强制

| 项目 | 内容 |
|---|---|
| **编号** | F-03 |
| **优先级** | P0 |
| **标题** | FactRevision、组件版本、流程版本等"不可变"数据仍可被修改或物理删除 |
| **问题描述** | 事实删除端点物理删除工件链接、观察值、修订和事实；对象图删除级联物理删除事实和修订；组件回滚修改旧版本 `created_at`；组件版本可全部删除；迁移仍向应用角色授予事实表 UPDATE/DELETE |
| **整改要求** | 1. 稳定聚合只允许 tombstone/archived，不删除版本和证据行；2. `current_version_id` 指针指向当前版本，不修改版本时间；3. 运行角色对 Revision、Version、Evidence、Audit 表只授予 SELECT/INSERT；4. 加数据库触发器拒绝对不可变行 UPDATE/DELETE；5. 设计法务/保留期清理流程，必须审批并生成不可变清理记录 |
| **验收标准** | • 通过真实 API 和运行账号尝试 UPDATE/DELETE，数据库层拒绝 • 回滚版本只改变指针，不改变历史行内容和时间戳 • 删除业务对象后历史版本、运行和证据仍可查询并验证哈希 |
| **涉及文件** | `apps/api/routers/facts.py`、`packages/standards/object_graph.py`、`packages/components/registry.py`、`packages/components/flow_runtime.py`、`migrations/versions/0012_facts.py` |
| **建议角色** | DBA + 后端 |

---

#### F-04 [P0] Outbox、Celery 与 JobExecutor 没有形成生产闭环

| 项目 | 内容 |
|---|---|
| **编号** | F-04 |
| **优先级** | P0 |
| **标题** | Outbox 未接入 Celery 投递链，普通作业、备份、恢复可能永远不执行 |
| **问题描述** | 无生产代码实例化并调用 `OutboxDispatcher.dispatch()`；Outbox 把消息 LPUSH 到 Redis list 而非 Celery 协议，且无消费者；JobExecutor 未注册通用/备份/恢复/审计导出 handler；未知 kind 使用 echo fallback 产生假成功；流程又直接 send_task 形成双通道 |
| **整改要求** | 1. 只保留 Outbox→Dispatcher→Celery 一条通道；2. Dispatcher 使用 Celery producer，建立 `event_type/job_kind → task` 显式映射；3. 使用 `FOR UPDATE SKIP LOCKED` 或 claim/lease 支持多 Dispatcher；4. Beat 注册 dispatch、lease heartbeat、reaper、retry 和死信检查；5. JobExecutor 未知 kind 必须失败，禁止 echo fallback；6. 为 flow、ingestion、model、backup、restore、audit_export 注册明确 handler |
| **验收标准** | • 端到端测试覆盖 accepted→queued→running→succeeded/failed • 事务回滚时不产生任务 • 重复投递只产生一次业务结果 • Worker 崩溃后租约到期可恢复 • 未知 kind 进入 failed/dead-letter，不返回 succeeded |
| **涉及文件** | `packages/jobs/service.py`、`packages/jobs/outbox.py`、`packages/jobs/worker.py`、`apps/worker/tasks/__init__.py`、`packages/components/flow_runtime.py`、`apps/api/routers/flows.py` |
| **建议角色** | 后端/平台 |

---

#### F-05 [P0] 审计的 append-only 和组织隔离实际无效

| 项目 | 内容 |
|---|---|
| **编号** | F-05 |
| **优先级** | P0 |
| **标题** | 审计表的数据库权限保护未作用于实际 API/Worker 连接账号，且查询和记录存在组织 ID 错误 |
| **问题描述** | API/Worker 使用数据库 owner `irip` 而非受限 `irip_app`，REVOKE 不约束实际连接；审计查询未按组织过滤；创建导出 Job 和记录审计时把 `user_id` 当作 `organization_id`；关键动作审计覆盖面极低 |
| **整改要求** | 1. 分离 migration owner、API runtime、Worker runtime 三类账号；2. API/Worker 使用最小权限 LOGIN role，审计表通过触发器或专用函数写入；3. `CurrentUser`/`Principal` 必须包含可信 organization ID；4. 审计列表、导出和详情强制组织谓词；5. 通过命令总线/中间件统一记录关键命令结果 |
| **验收标准** | • 应用实际连接角色无法 UPDATE/DELETE audit_event • 跨组织审计读取全部被拒绝 • 关键动作审计覆盖率清单达到 100%，包含 actor、organization、request/correlation ID、结果和对象标识 |
| **涉及文件** | `migrations/versions/0003_authorization_audit.py`、`compose.yaml`、`apps/api/routers/audit.py`、`apps/api/routers/governance.py` |
| **建议角色** | DBA + 后端 + 安全 |

---

#### F-06 [P0] 备份与恢复完整性 fail-open

| 项目 | 内容 |
|---|---|
| **编号** | F-06 |
| **优先级** | P0 |
| **标题** | 备份和恢复会跳过缺失/损坏对象并继续报告成功，无法保证恢复完整性 |
| **问题描述** | MinIO 列表/下载失败只记录 warning 后继续；manifest 只对成功对象计数和哈希，无法发现漏备；恢复时缺失/SHA 不匹配对象被跳过；PG dump 与 MinIO 无一致性快照；manifest 无签名/MAC |
| **整改要求** | 1. 列表、下载、上传、哈希任何一步失败都使任务失败；2. manifest 记录期望对象数、完成数、总字节数和失败清单，失败清单非空不得发布备份；3. 恢复前先完整验证所有文件再对空白/隔离目标恢复；4. 定义 PG 与 MinIO 一致性边界（维护窗口/写入冻结/内容版本高水位）；5. 对 manifest 做签名，备份复制到异地不可变存储 |
| **验收标准** | • 随机删除/篡改任一对象后，恢复必须在写入目标前失败 • 恢复后 DB 引用对象集合与 MinIO 对象集合完全一致 • 定期恢复演练产生可审计的 RPO/RTO 报告 |
| **涉及文件** | `deployments/compose/backup.py`、`deployments/compose/restore.py` |
| **建议角色** | SRE/DBA |

---

#### F-07 [P0] readiness 与当前迁移 head 永久不一致

| 项目 | 内容 |
|---|---|
| **编号** | F-07 |
| **优先级** | P0 |
| **标题** | readiness 固定检查迁移 `0024`，而当前 head 为 `0031`，最新数据库会被判定未就绪 |
| **问题描述** | `health.py` 硬编码 `EXPECTED_MIGRATION_HEAD = "0024"`，使用严格相等判断，数据库升级到最新版本后 readiness 必然返回 503 |
| **整改要求** | 1. 从 Alembic revision graph 动态读取所有 heads，不手写版本号；2. readiness 比较数据库 head 集合与代码 head 集合；3. CI 添加"迁移升级后 readiness 为 200"的强制测试 |
| **验收标准** | • `alembic upgrade head` 后 readiness 返回 200 • 新增迁移无需修改健康检查常量 • 支持多 head 或明确在 CI 拒绝多 head |
| **涉及文件** | `apps/api/routers/health.py` |
| **建议角色** | 平台/SRE |

---

#### F-12 [P0] 密钥明文持久化且生产默认凭据可导致系统接管

| 项目 | 内容 |
|---|---|
| **编号** | F-12 |
| **优先级** | P0 |
| **标题** | API key 明文存储，生产编排含已知管理员口令和 JWT/DB/MinIO 默认密钥 |
| **问题描述** | AI 配置 API key 声称加密但实际明文存储；连接器密钥有明文 TODO；compose.yaml 提供公开默认值；bootstrap 自动创建管理员；JWT 有静态 fallback 且直接信任 token roles 不查询用户状态；API/Worker 以 root 运行且 DB 账号是 owner |
| **整改要求** | 1. 首选外部 Secret Manager，否则 envelope encryption，数据库只存密文和 key version；2. 生产配置使用 `${VAR:?required}`，启动时拒绝弱密钥和默认值；3. 管理员引导使用一次性外部 Secret 并强制首次轮换；JWT 验证后查询用户状态和当前角色；4. 建立 JWT key rotation、token version 和全局/按用户失效机制；5. DB/MinIO 使用最小权限 service account；6. API、Worker、Nginx 以非 root 运行 |
| **验收标准** | • 数据库中不存在明文 API key/连接器密钥 • 生产启动拒绝默认/弱密钥 • JWT 不存在静态 fallback，验证时查询用户当前状态和角色 • 容器以非 root 用户运行 • DB 运行账号非 owner |
| **涉及文件** | `apps/api/routers/ai_config.py`、`packages/connectors/mapping.py`、`compose.yaml`、`deployments/compose/bootstrap.py`、`apps/api/dependencies/auth.py` |
| **建议角色** | 安全 + 平台/SRE |

---

#### F-13 [P0] 流程可读取 Worker 任意本地文件，且 SSRF、上传和组件执行边界不足

| 项目 | 内容 |
|---|---|
| **编号** | F-13 |
| **优先级** | P0 |
| **标题** | 流程读取组件接受任意本地路径可读取 Worker 环境和挂载文件，SSRF/上传/组件执行边界不足 |
| **问题描述** | AI 配置允许任意 base URL 存在 SSRF；REST Connector 无私网阻断和响应大小限制；本地上传用 `startswith` 判断路径可绕过；摄入组件接受任意路径；运行 inputs 可覆盖节点参数；CLI 组件在主 Worker 容器以 root 执行无 OS 级隔离；预签名上传无 content-length 条件 |
| **整改要求** | 1. 建立统一 SSRF-safe HTTP Client（DNS 解析后二次校验、禁止私网/链路本地、allowlist、重定向重检、大小和超时上限）；2. 生产禁用仓库根浏览，只允许专用导入目录或 artifact ID；3. 使用 `Path.resolve().is_relative_to(root)`，流式上传并硬限制大小；4. 所有摄入组件只接受 ArtifactRef；5. 安全敏感节点参数不得被运行 inputs 覆盖；预签名策略绑定 content-length-range，complete 先 HEAD 再流式校验；6. CLI 组件在独立非 root 容器/沙箱运行（无网络、只读 FS、cap drop、资源限额、seccomp） |
| **验收标准** | • 流程不能读取 `/proc/self/environ`、源码或挂载 secret • SSRF 测试证明私网和链路本地地址被阻断 • 路径穿越测试证明 `startswith` 绕过已修复 • CLI 组件在独立沙箱运行，无网络访问 • 上传大小限制在流式读取中强制执行 |
| **涉及文件** | `apps/api/routers/ai_config.py`、`packages/connectors/rest_connector.py`、`apps/api/routers/files.py`、`packages/components/builtin/ingestion/csv_reader.py`、`packages/components/flow_runtime.py`、`apps/api/routers/flows.py`、`packages/components/runner.py` |
| **建议角色** | 安全 + 后端 |

---

### 4.2 P1 需求（Should Have — 生产前必须完成，10 项）

#### F-08 [P1] ScopeGrant 是未接入的"死功能"

| 项目 | 内容 |
|---|---|
| **编号** | F-08 |
| **优先级** | P1 |
| **标题** | ScopeGrant 对象级授权服务已实现但未接入业务 API/查询 |
| **问题描述** | `require_permission` 只检查 JWT 静态角色；`main.py` 未覆盖 `get_authorization_service`；生产路由不调用 `AuthorizationService.require()`；安全测试测独立服务而非真实 API |
| **整改要求** | 1. 将 ScopeGrant 转成统一 `QueryScope`，由所有列表和单对象命令使用；2. 所有服务接收 Principal，不允许只传裸 user ID；3. RBAC 只负责粗粒度入口，资源授权由 Scope Policy 决定；4. 授权默认拒绝，禁止先查全量再在 Python 中过滤 |
| **验收标准** | • ScopeGrant 配置后，无权限用户无法访问受限对象 • 列表查询自动应用 scope 过滤 • 安全测试通过真实 API 验证 • 默认拒绝行为通过测试 |
| **涉及文件** | `packages/auth/scope_grants.py`、`apps/api/dependencies/authorization.py`、`apps/api/main.py` |
| **建议角色** | 安全 + 后端 |

---

#### F-09 [P1] Job 与 Artifact 存在跨租户 IDOR，且部分端点只有认证

| 项目 | 内容 |
|---|---|
| **编号** | F-09 |
| **优先级** | P1 |
| **标题** | Job 和 Artifact 端点仅认证无权限检查，按 UUID 可跨租户访问 |
| **问题描述** | `jobs.py` 全部端点仅 `get_current_user` 无 `require_permission`；Job 详情返回 payload/result/last_error；上传/下载只有认证；Artifact 点查/下载只按 ID；本地上传复用 `flow:read` 无写权限 |
| **整改要求** | 1. 定义 `job:read/submit/cancel/retry`、`artifact:read/write` 等权限；2. 对创建者、组织、对象关联和 ScopeGrant 做联合检查；3. 敏感 payload/result 根据权限脱敏 |
| **验收标准** | • 跨租户 Job/Artifact UUID 访问返回 403/404 • 无权限角色无法执行 cancel/retry • 敏感字段按权限脱敏 • 权限矩阵测试通过 |
| **涉及文件** | `apps/api/routers/jobs.py`、`apps/api/routers/uploads.py`、`packages/common/artifacts.py`、`apps/api/routers/files.py` |
| **建议角色** | 安全 + 后端 |

---

#### F-10 [P1] AI 工具调用与真实引用未实现

| 项目 | 内容 |
|---|---|
| **编号** | F-10 |
| **优先级** | P1 |
| **标题** | AI 工具调用未发送 tools schema、未执行 handler、未二次调用；引用不可验证 |
| **问题描述** | 请求构造未发送 `tools`/`tool_choice`；工具调用仅标记 "executed" 不执行 handler；无 tool message 回传和第二轮 completion；引用可取自供应商内容；存在两套不一致注册表 |
| **整改要求** | 1. 合并为唯一 ToolDefinition/Registry/Handler 模型；2. Provider 发送完整 JSON Schema；3. Handler 必须绑定当前 Principal、组织和 Scope Policy；4. 服务端执行工具并生成不可伪造的结构化 citation；5. 将工具结果回传模型完成第二轮回答；6. 为写工具建立风险等级、显式确认和审计 |
| **验收标准** | • AI 助手能真实调用注册工具并返回结果 • 引用可追溯到服务端真实查询记录 • 两套注册表合并为一套 • 写工具有显式确认和审计记录 |
| **涉及文件** | `packages/ai/openai_compatible.py`、`packages/ai/service.py`、`packages/ai/tools.py`、`packages/ai/tool_registry.py` |
| **建议角色** | AI/领域后端 |

---

#### F-11 [P1] 模型执行结果未进入事实和溯源链

| 项目 | 内容 |
|---|---|
| **编号** | F-11 |
| **优先级** | P1 |
| **标题** | 模型预测游离于证据链之外，无法证明输入/模型版本/输出关系 |
| **问题描述** | ModelService 构造时未注入 FactService；Worker 显式传入 `fact_service=None`；无服务时直接返回，异常被吞掉；写入使用不明确的对象关联 |
| **整改要求** | 1. 明确定义 ModelExecution 聚合及其与工业对象、模型版本、输入事实的关系；2. API 和 Worker 统一注入事实/溯源端口；3. 事实写入失败必须使执行失败或进入可观察补偿队列；4. 引用模型版本哈希、输入快照和输出工件哈希 |
| **验收标准** | • 模型预测产生可追溯的 ModelExecution 事实 • 事实包含模型版本哈希、输入快照、输出工件哈希 • 事实写入失败使执行失败 • 端到端验证输入→模型→输出→证据链完整 |
| **涉及文件** | `apps/api/main.py`、`apps/worker/tasks/models.py`、`packages/models/service.py` |
| **建议角色** | AI/领域后端 |

---

#### F-14 [P1] 静态检查已暴露确定性运行错误和错误响应失真

| 项目 | 内容 |
|---|---|
| **编号** | F-14 |
| **优先级** | P1 |
| **标题** | 8 个未定义名称（F821）会导致 NameError；AppError 错误码映射不完整导致 4xx 被伪装为 500 |
| **问题描述** | Ruff 发现 8 个未定义名称（facts.py 的 `func`/`AppError`/`ArtifactService`，flows.py 的 `AppError`/`S3Repository`）；`_STATUS_MAP` 缺少 `file_too_large`/`ssrf_blocked`/`component_timeout`/`ai_provider_error` 等码 |
| **整改要求** | 1. 将 Ruff F/E、Mypy 和导入测试设为阻断质量门；2. AppError 使用封闭 Enum 并直接携带 HTTP status 或由单一穷尽映射生成；3. CI 检查"所有被抛出的错误码都有协议映射"；4. 为上述分支增加 API/Worker 测试 |
| **验收标准** | • Ruff F821 为 0 • 所有 AppError 错误码有正确的 HTTP 状态映射 • 对应分支有 API/Worker 测试覆盖 • CI 中 Ruff F/E 为阻断门 |
| **涉及文件** | `apps/api/routers/facts.py`、`apps/api/routers/flows.py`、`apps/worker/tasks/flows.py`、`apps/api/main.py` |
| **建议角色** | 后端 + 工程效能 |

---

#### F-15 [P1] 恢复流程会吞掉失败，并存在归档提取风险

| 项目 | 内容 |
|---|---|
| **编号** | F-15 |
| **优先级** | P1 |
| **标题** | 恢复流程 Alembic/pg_restore 非零退出被忽略；归档提取无路径穿越防护 |
| **问题描述** | Alembic 非零退出只记录 warning；无备份时退出 0；pg_restore 通过 stderr 字符串判断是否忽略；归档在验证 manifest 前使用 `tar.extractall`；镜像未安装 `age` |
| **整改要求** | 1. 任一外部命令非零必须默认失败，仅对结构化、可证明安全的特定状态放行；2. 先验证归档成员路径、manifest 和签名再提取；3. Python 3.12 使用安全 extraction filter，额外拒绝绝对路径、`..`、符号链接逃逸；4. 为备份/恢复提供专用非 root 镜像并安装固定版本 `age` |
| **验收标准** | • Alembic/pg_restore 失败时恢复任务失败 • 归档提取拒绝路径穿越成员 • 安全 extraction filter 通过测试 • 备份/恢复镜像以非 root 运行且含 age |
| **涉及文件** | `deployments/compose/restore.py` |
| **建议角色** | SRE/DBA |

---

#### F-16 [P1] 发布门脚本按当前仓库无法成功运行

| 项目 | 内容 |
|---|---|
| **编号** | F-16 |
| **优先级** | P1 |
| **标题** | release-gate.sh 引用不存在的测试目录，E2E 路径/URL/fixture 错误，迁移版本过期 |
| **问题描述** | 引用不存在的 `tests/property`；集成测试在 Docker 启动前执行；Playwright 路径指向错误目录；默认 localhost:5173 与 Compose 80 端口不匹配；fixture 不存在；迁移版本写 `0021` |
| **整改要求** | 1. 统一一个 CI 和本地共用的质量入口；2. 先启动隔离测试基础设施再迁移和执行测试；3. 修复 E2E 路径、URL、浏览器安装、数据播种和清理；4. 发布证据由 CI 自动生成（commit SHA、依赖锁摘要、迁移 head、测试报告、签核） |
| **验收标准** | • release-gate.sh 在干净环境可成功运行 • E2E 路径和 URL 正确 • 发布报告由 CI 自动生成含 commit SHA • 迁移版本与实际 head 一致 |
| **涉及文件** | `scripts/release-gate.sh`、`apps/web/playwright.config.ts`、`.github/workflows/ci.yml` |
| **建议角色** | 工程效能 |

---

#### F-17 [P1] 测试分类和覆盖门存在盲区

| 项目 | 内容 |
|---|---|
| **编号** | F-17 |
| **优先级** | P1 |
| **标题** | 大量"unit"实为 DB 集成测试且 CI 不运行；无覆盖率门；前端测试严重不足 |
| **问题描述** | conftest 未配置 DB 时 skip；CI unit job 不提供 DB，integration job 不运行 unit；164 passed/114 skipped 说明大量 unit 是 DB 集成；CI 不运行 contract/acceptance/E2E/performance；无覆盖率 fail-under；前端 57 个源文件仅 5 个组件测试 |
| **整改要求** | 1. 将 DB 测试重分类为 integration，或用 fake repository 改成真正 unit；2. CI 对非预期 skip 设置上限并保存 skip 报告；3. 为 contract、acceptance、E2E、performance 建立独立 job；4. 设置核心领域 line/branch 覆盖率和 PR diff coverage 门 |
| **验收标准** | • CI 中非预期 skip 为 0 • contract/acceptance/E2E/performance 在 CI 独立运行 • 核心领域有 line/branch 覆盖率门 • PR diff coverage 门启用 • 前端组件测试覆盖率提升 |
| **涉及文件** | `tests/conftest.py`、`.github/workflows/ci.yml`、`apps/web/` |
| **建议角色** | 工程效能 |

---

#### F-18 [P1] CI、依赖与制品不可重现

| 项目 | 内容 |
|---|---|
| **编号** | F-18 |
| **优先级** | P1 |
| **标题** | Python 无 lock 文件，CI Actions/镜像用 tag 不 pin digest，Web Dockerfile 绕过锁文件 |
| **问题描述** | Python 依赖只有宽约束无 lock/hash；CI lint 用未固定 `pip install ruff`；Actions 用 tag 不 pin SHA；API 镜像每次重新解析依赖；Web Dockerfile 用 `--no-frozen-lockfile \|\| true` 绕过锁并吞失败；基础镜像用 tag 不用 digest；CI MinIO 缺 `server /data` |
| **整改要求** | 1. 使用 uv/pip-tools 生成带哈希的 Python lock；2. 生产强制 frozen lock，禁止 `\|\| true`；3. Pin Actions 和基础镜像 digest；4. 生成 SBOM、签名和 provenance；5. 修复 CI MinIO 启动方式并保存构建/测试工件 |
| **验收标准** | • 同一源码产生相同制品 • 依赖安装失败不被隐藏 • Actions 和镜像 pin 到 digest/SHA • SBOM 和签名制品生成 • CI MinIO 正常启动 |
| **涉及文件** | `pyproject.toml`、`.github/workflows/ci.yml`、`apps/web/Dockerfile`、`deployments/compose/*.Dockerfile` |
| **建议角色** | 工程效能 |

---

#### F-19 [P1] 可观测性与文档承诺不一致

| 项目 | 内容 |
|---|---|
| **编号** | F-19 |
| **优先级** | P1 |
| **标题** | structlog 仅声明未使用，Prometheus 指标未实现，Worker/Beat 无 healthcheck，readiness 无真实 heartbeat |
| **问题描述** | 生产代码用标准 logging/print 而非 structlog；Prometheus 指标仍为未来规划；Worker 和 scheduler 无 healthcheck；readiness 只通过 Outbox 积压推测无真实 heartbeat；恢复/备份异常被 warning 或 broad catch 吞掉 |
| **整改要求** | 1. 统一 JSON 日志和 correlation ID；2. 输出 API、队列、Job、Worker、Outbox、DB、MinIO、备份 RPO/RTO 指标；3. Worker/Beat 提供真实 heartbeat；4. 对静默失败改为结构化失败事件和告警 |
| **验收标准** | • 生产日志为 JSON 格式且含 correlation ID • Prometheus 指标可被抓取 • Worker/Beat 有真实 healthcheck • 静默失败产生结构化告警事件 |
| **涉及文件** | `apps/api/`、`apps/worker/`、`packages/jobs/`、`deployments/` |
| **建议角色** | 平台/SRE |

---

### 4.3 P2 需求（Nice to Have — 中期治理，5 项）

#### F-20 [P2] 应用层与领域层耦合过重

| 项目 | 内容 |
|---|---|
| **编号** | F-20 |
| **优先级** | P2 |
| **标题** | 路由直接访问服务私有属性和 ORM，main.py 超大 Composition Root，领域间存在循环依赖 |
| **问题描述** | 路由频繁访问 `service._factory`、`service._org_id`；`main.py` 735 行集中组装；facts 与 standards、departments 与 equipment 存在 ORM 循环依赖；`ez_scan_extractor` 反向导入 API Router |
| **整改要求** | 1. API 只依赖应用命令/查询接口；2. 引入明确 Ports/Protocols 和按领域拆分的 provider 模块；3. 跨域查询进入 read-model/query 包；4. API 和 Worker 共享显式 Composition Root，不依赖模块全局状态 |
| **验收标准** | • 路由不访问服务私有属性 • 无领域循环依赖 • 无 API 反向依赖 • Composition Root 可按领域拆分 |
| **涉及文件** | `apps/api/main.py`、`apps/api/routers/`、`packages/` |
| **建议角色** | 架构/后端 |

---

#### F-21 [P2] 异步接口中存在阻塞 I/O 和大内存读取

| 项目 | 内容 |
|---|---|
| **编号** | F-21 |
| **优先级** | P2 |
| **标题** | async 方法中执行同步文件 I/O，大文件一次性读入内存，REST Connector 一次性加载超大 JSON |
| **问题描述** | Ruff 报告 10 个 ASYNC240、2 个 ASYNC230；CSV/JSON/Excel/PDF 读取在 async 中直接同步 I/O；CSV 全部行一次性读入；本地上传先完整读入再检查大小；REST Connector 超大响应一次性加载 |
| **整改要求** | 1. CPU/同步 I/O 放线程池或独立 Worker；2. 文件和网络响应使用流式处理、背压和硬上限；3. 为组件设置内存、CPU、时间和输出大小预算 |
| **验收标准** | • ASYNC240/ASYNC230 为 0 • 大文件处理为流式 • 组件有资源预算上限 • 压力测试无事件循环阻塞 |
| **涉及文件** | `packages/components/builtin/`、`apps/api/routers/files.py`、`packages/connectors/rest_connector.py` |
| **建议角色** | 后端 |

---

#### F-22 [P2] 文档、代码、验收和版本统计严重漂移

| 项目 | 内容 |
|---|---|
| **编号** | F-22 |
| **优先级** | P2 |
| **标题** | 验收文档宣称全部通过但实际有 279 个 Ruff/283 个 Mypy 错误；迁移版本/组件数/路由数不一致；文档引用不存在文件 |
| **问题描述** | `final-release.md` 宣称测试/lint/Mypy/E2E 全通过与实际不符；迁移写 `0021` 实际 `0031`；组件数多处不一致；文档引用不存在的测试/示例；签核 pending 但用"已交付/已发布"措辞 |
| **整改要求** | 1. 文档能力标记为 Proposed/Partial/Implemented/Verified/Deprecated；2. 组件数、AI 工具数、路由数、迁移 head 由源码自动生成；3. 验收报告由 CI 针对 commit SHA 生成，禁止手工维护"全部通过"；4. 文档中命令和路径加入 contract test |
| **验收标准** | • 文档能力标记准确 • 统计数据由源码自动生成 • 验收报告由 CI 生成含 commit SHA • 文档命令经 contract test 验证 • "已交付/已发布"措辞改为"开发中/内部Alpha" |
| **涉及文件** | `docs/acceptance/final-release.md`、`docs/`、`README.md`、`scripts/release-gate.sh` |
| **建议角色** | 架构/工程效能 |

---

#### F-23 [P2] 前端存在 legacy/重复实现和超大客户端

| 项目 | 内容 |
|---|---|
| **编号** | F-23 |
| **优先级** | P2 |
| **标题** | API client 2683 行过大，多个页面超 1000 行，legacy 组件未清除，占位页与实际页并存 |
| **问题描述** | `client.ts` 2683 行承载过多领域类型；`FlowDetail.tsx`/`ComponentsPage.tsx` 超 1000 行；`flow/legacy.tsx` 1132 行；占位页与实际页并存；旧路由重定向但文档仍描述旧信息架构 |
| **整改要求** | 1. API client 按领域拆分并由 OpenAPI 生成基础类型；2. 页面拆分为 query/container、presentational component 和 domain hook；3. 建立删除 legacy 的清单和可验证迁移路径；4. 明确信息架构并同步路由、菜单、文档和 E2E |
| **验收标准** | • API client 按领域拆分 • 无 legacy 组件残留 • 页面行数合理 • 信息架构文档与路由/菜单一致 |
| **涉及文件** | `apps/web/src/api/client.ts`、`apps/web/src/components/FlowDetail.tsx`、`apps/web/src/components/ComponentsPage.tsx`、`apps/web/src/components/flow/legacy.tsx` |
| **建议角色** | 前端 |

---

#### F-24 [P2] 质量基线未真正落地

| 项目 | 内容 |
|---|---|
| **编号** | F-24 |
| **优先级** | P2 |
| **标题** | Ruff 279 项问题、Mypy 283 个错误未作为阻断门执行；Makefile/CI/release-gate 检查范围不一致 |
| **问题描述** | Ruff 含 80 超长行、62 未用导入、60 导入排序、24 未用变量、12 async 阻塞、8 未定义名称；Mypy 283 错误集中在缺泛型/类型不匹配/无效 ignore；Makefile 只查 `packages/common`；前端 lint 只是 `tsc --noEmit` |
| **整改要求** | 1. 先冻结基线并清零 F821/F/E 运行错误，再分批清理类型债；2. 统一 Makefile、CI、release gate 的检查范围；3. 增加 ESLint、Prettier check、Ruff format check；4. 新代码不得增加 baseline，逐模块收紧到零 |
| **验收标准** | • Ruff F/E 为 0 • Makefile/CI/release-gate 检查范围一致 • ESLint/Prettier/Ruff format check 启用 • 新代码不增加 baseline • Mypy 按模块逐个清零 |
| **涉及文件** | `Makefile`、`.github/workflows/ci.yml`、`scripts/release-gate.sh`、`pyproject.toml` |
| **建议角色** | 工程效能 |

---

## 5. 整改路线（4 个阶段）

### 5.1 阶段 0：立即止血（7 项）

**目标**：消除最可能造成数据破坏和错误发布的入口。

| 序号 | 整改项 | 对应需求 | 预期工时 | 退出标准 |
|---|---|---|---|---|
| 0-1 | 默认 Compose 移除 restore/backup | F-01 | 0.5d | `docker compose config --services` 不含 restore |
| 0-2 | 临时禁用事实、组件、流程物理删除端点 | F-02, F-03 | 1d | 硬删除端点返回 405/禁用 |
| 0-3 | 修复 readiness `0024`/`0031` 不一致 | F-07 | 0.5d | `alembic upgrade head` 后 readiness 返回 200 |
| 0-4 | 修复 8 个 F821 未定义名称 | F-14 | 0.5d | Ruff F821 为 0 |
| 0-5 | 生产配置拒绝默认密钥，轮换现有凭据 | F-12 | 1d | 默认凭据无法启动或登录 |
| 0-6 | 禁止流程运行 inputs 覆盖本地 path，停用任意路径 reader | F-13 | 1d | 流程不能读取任意本地路径 |
| 0-7 | 明确标记当前版本"不可生产发布" | F-22 | 0.5d | 文档/README 标记"内部Alpha" |

**阶段 0 退出条件**：
- ✅ 普通部署不触发恢复
- ✅ 不存在已知跨租户硬删除入口
- ✅ 默认凭据无法启动或登录
- ✅ 流程不能读取任意本地路径
- ✅ 静态检查无 F821
- ✅ readiness 与迁移 head 一致
- ✅ 版本标记为"不可生产发布"

**预计总工时**：约 5 人日

---

### 5.2 阶段 1：安全与可靠性闭环

**目标**：建立不可绕过的租户、授权、审计和异步作业控制面。

| 序号 | 整改项 | 对应需求 | 预期工时 | 退出标准 |
|---|---|---|---|---|
| 1-1 | 引入可信 Principal 和统一 Tenant/Scope Policy | F-02, F-08 | 5d | 所有服务接收 Principal，组织条件强制 |
| 1-2 | 所有 Repository 强制组织条件，增加跨组织 API 测试 | F-02, F-09 | 5d | A/B 组织交叉测试 100% 通过 |
| 1-3 | 分离 migration/runtime 数据库账号，启用最小权限 | F-05, F-12 | 3d | 运行账号非 owner，不可改不可变表 |
| 1-4 | 版本、证据、审计表实现数据库 INSERT-only | F-03, F-05 | 4d | 数据库层拒绝 UPDATE/DELETE |
| 1-5 | 完成 Outbox→Celery→Handler 唯一链路 | F-04 | 5d | 端到端可靠性测试通过 |
| 1-6 | 删除 echo fallback，补齐 backup/restore/audit_export handler | F-04 | 2d | 未知 kind 进入 failed/dead-letter |
| 1-7 | 修复备份恢复 fail-open 和安全归档提取 | F-06, F-15 | 4d | 隔离恢复演练通过 |
| 1-8 | 建立 SSRF-safe HTTP Client 和文件路径安全 | F-13 | 3d | SSRF/路径穿越测试通过 |
| 1-9 | CLI 组件沙箱化 | F-13 | 3d | CLI 在独立非 root 容器运行 |

**阶段 1 退出条件**：
- ✅ P0 全部关闭
- ✅ 多组织安全测试通过
- ✅ Job 端到端和灾难恢复演练通过
- ✅ 不可变表数据库级强制
- ✅ 数据库账号最小权限

**预计总工时**：约 34 人日

---

### 5.3 阶段 2：功能真实性与质量门

**目标**：让文档声称的能力与真实运行一致。

| 序号 | 整改项 | 对应需求 | 预期工时 | 退出标准 |
|---|---|---|---|---|
| 2-1 | 完成 AI tools schema、handler、二次调用和真实 citation | F-10 | 5d | AI 工具端到端可验证 |
| 2-2 | 模型执行写入事实/溯源链 | F-11 | 3d | 输入→模型→输出→证据链完整 |
| 2-3 | Secret Manager/envelope encryption | F-12 | 3d | 数据库中无明文密钥 |
| 2-4 | 修复 release gate、CI MinIO、E2E 路径和 fixture | F-16 | 3d | 唯一发布门在干净环境可运行 |
| 2-5 | 重分类 DB 测试，禁止意外 skip | F-17 | 2d | CI 中非预期 skip 为 0 |
| 2-6 | 清零 Ruff F/E，Mypy 按模块清零，建立覆盖率门 | F-14, F-24 | 4d | Ruff F/E=0，覆盖率门启用 |
| 2-7 | 增加结构化日志、Worker heartbeat 和核心指标 | F-19 | 3d | JSON 日志 + Prometheus 指标 + Worker healthcheck |
| 2-8 | 修复 AppError 错误码映射 | F-14 | 1d | 所有错误码有正确 HTTP 映射 |

**阶段 2 退出条件**：
- ✅ 唯一发布门可在干净环境运行
- ✅ 当前 commit 有可验证报告
- ✅ 关键能力（AI 工具、模型溯源）有端到端证据
- ✅ Ruff F/E=0，覆盖率门启用
- ✅ 可观测性指标可被抓取

**预计总工时**：约 24 人日

---

### 5.4 阶段 3：架构收敛与可维护性

**目标**：降低下一阶段功能开发成本。

| 序号 | 整改项 | 对应需求 | 预期工时 | 退出标准 |
|---|---|---|---|---|
| 3-1 | 路由不再访问服务私有属性或 ORM | F-20 | 4d | 依赖方向检查通过 |
| 3-2 | 拆分 Composition Root 和超大领域文件 | F-20 | 4d | main.py 可按领域拆分 |
| 3-3 | 消除领域循环依赖和 API 反向依赖 | F-20 | 3d | 无循环依赖 |
| 3-4 | 前端按领域拆分 API client 和超大页面，清除 legacy | F-23 | 5d | API client 按领域拆分，无 legacy |
| 3-5 | 生成式维护迁移 head、组件、工具、路由和验收文档 | F-22 | 3d | 文档由代码自动生成 |
| 3-6 | 锁定依赖、镜像 digest，生成 SBOM、签名制品 | F-18 | 3d | 制品可重现，含 SBOM |
| 3-7 | 异步接口阻塞 I/O 修复 | F-21 | 3d | ASYNC240/230 为 0 |
| 3-8 | 统一 Makefile/CI/release-gate 检查范围 | F-24 | 1d | 检查范围一致 |

**阶段 3 退出条件**：
- ✅ 依赖方向检查通过
- ✅ 文档可由代码验证
- ✅ 发布使用不可变、可追溯制品
- ✅ 前端无 legacy，API client 按领域拆分
- ✅ 异步无阻塞 I/O

**预计总工时**：约 26 人日

---

### 5.5 整改路线总览

```mermaid
gantt
    title IRIP 整改路线总览
    dateFormat YYYY-MM-DD
    axisFormat %m-%d

    section 阶段0 立即止血
    F-01 移除默认restore      :a01, 2026-07-28, 1d
    F-02/03 禁用硬删除        :a02, 2026-07-28, 1d
    F-07 修复readiness        :a03, 2026-07-28, 1d
    F-14 修复F821             :a04, 2026-07-28, 1d
    F-12 拒绝默认密钥         :a05, 2026-07-28, 1d
    F-13 禁止任意路径         :a06, 2026-07-28, 1d
    F-22 标记不可发布         :a07, 2026-07-28, 1d

    section 阶段1 安全闭环
    F-02/08 Principal+Scope   :b01, 2026-07-29, 5d
    F-02/09 组织条件+测试     :b02, 2026-07-29, 5d
    F-05/12 DB账号分离        :b03, 2026-07-29, 3d
    F-03/05 INSERT-only       :b04, 2026-08-01, 4d
    F-04 Outbox闭环           :b05, 2026-07-29, 5d
    F-04 补齐handler          :b06, 2026-08-03, 2d
    F-06/15 备份恢复          :b07, 2026-07-29, 4d
    F-13 SSRF+路径安全        :b08, 2026-07-29, 3d
    F-13 CLI沙箱              :b09, 2026-08-01, 3d

    section 阶段2 质量门
    F-10 AI工具               :c01, 2026-08-11, 5d
    F-11 模型溯源             :c02, 2026-08-11, 3d
    F-12 Secret管理           :c03, 2026-08-11, 3d
    F-16 发布门               :c04, 2026-08-11, 3d
    F-17 测试分类             :c05, 2026-08-11, 2d
    F-14/24 质量基线          :c06, 2026-08-11, 4d
    F-19 可观测性             :c07, 2026-08-11, 3d

    section 阶段3 架构收敛
    F-20 解耦                 :d01, 2026-08-25, 4d
    F-20 拆分CR              :d02, 2026-08-25, 4d
    F-20 消除循环依赖         :d03, 2026-08-25, 3d
    F-23 前端拆分             :d04, 2026-08-25, 5d
    F-22 文档自动生成         :d05, 2026-08-25, 3d
    F-18 制品可重现           :d06, 2026-08-25, 3d
    F-21 异步修复             :d07, 2026-08-25, 3d
    F-24 统一检查范围         :d08, 2026-08-25, 1d
```

---

## 6. 需求优先级总览

| 优先级 | 数量 | 需求编号 | 阶段 |
|---|---|---|---|
| **P0** | 9 | F-01, F-02, F-03, F-04, F-05, F-06, F-07, F-12, F-13 | 阶段 0 + 阶段 1 |
| **P1** | 10 | F-08, F-09, F-10, F-11, F-14, F-15, F-16, F-17, F-18, F-19 | 阶段 1 + 阶段 2 |
| **P2** | 5 | F-20, F-21, F-22, F-23, F-24 | 阶段 2 + 阶段 3 |

---

## 7. 待确认问题

以下问题需要用户/管理层决策后才能确定具体实施方案：

### 7.1 安全架构决策

| 编号 | 问题 | 选项 | 影响 | 建议 |
|---|---|---|---|---|
| **Q-1** | 是否引入 PostgreSQL RLS 作为第二道防线？ | A) 引入 RLS / B) 仅应用层组织条件 | A 增加数据库层防护但增加运维复杂度；B 实现简单但有绕过风险 | 建议 A，作为深度防御 |
| **Q-2** | 密钥管理方案选择 | A) 外部 Secret Manager（AWS SM/HashiCorp Vault）/ B) envelope encryption（数据库存密文+key version） | A 安全性最高但引入外部依赖；B 自主可控但需自行管理 key rotation | 生产建议 A，过渡期可用 B |
| **Q-3** | CLI 组件是否运行在独立沙箱容器？ | A) 独立沙箱容器（无网络、只读FS、cap drop）/ B) 主 Worker 容器内限制 | A 安全性最高但增加调度复杂度；B 简单但爆炸半径大 | 建议 A，安全敏感场景必须 |

### 7.2 文档与发布决策

| 编号 | 问题 | 选项 | 影响 | 建议 |
|---|---|---|---|---|
| **Q-4** | 文档中"已交付/已发布"措辞是否全部改为"开发中/内部Alpha"？ | A) 全部改为"开发中/内部Alpha" / B) 保留但加标记 | A 准确反映现状；B 保留原有措辞但可能误导 | 建议 A，阶段 0 立即执行 |
| **Q-5** | 验收报告生成方式 | A) CI 针对 commit SHA 自动生成 / B) 人工维护 | A 可验证不可篡改；B 灵活但易漂移 | 建议 A |

### 7.3 架构决策

| 编号 | 问题 | 选项 | 影响 | 建议 |
|---|---|---|---|---|
| **Q-6** | 是否立即拆分微服务？ | A) 保持模块化单体并收敛边界 / B) 拆分微服务 | A 符合当前阶段，修复成本低；B 增加分布式复杂度 | 建议 A，专家明确不建议立即拆分 |
| **Q-7** | 数据库账号分离方案 | A) migration owner + API runtime + Worker runtime 三类 / B) 两类（owner + runtime） | A 权限最细粒度；B 简化运维 | 建议 A，审计表需要独立写入路径 |

### 7.4 工程决策

| 编号 | 问题 | 选项 | 影响 | 建议 |
|---|---|---|---|---|
| **Q-8** | Python 依赖锁定工具选择 | A) uv / B) pip-tools | A 速度更快，现代工具；B 更成熟 | 建议 A |
| **Q-9** | 前端 API client 生成方式 | A) OpenAPI 自动生成 / B) 手动维护按领域拆分 | A 类型安全且自动同步；B 灵活但易漂移 | 建议 A |

---

## 8. 发布准入标准

在宣布生产可用前，必须满足以下全部标准（来自审阅报告第 8 节）：

### 8.1 安全
- [ ] 所有多租户资源通过 API 交叉组织测试
- [ ] ScopeGrant 在列表和单对象读写中都真实生效
- [ ] 应用数据库账号不是 owner，且不可修改不可变表
- [ ] 生产启动拒绝默认密钥
- [ ] AI、REST、文件和组件执行通过 SSRF/路径/沙箱安全测试

### 8.2 数据正确性
- [ ] FactRevision、Evidence、PublishedVersion、AuditEvent 数据库级不可变
- [ ] 所有删除都是 tombstone/归档或受控保留期清理
- [ ] 模型与流程结果能追溯到输入、版本、工件和操作者

### 8.3 可靠性
- [ ] Outbox 端到端、重复投递、Worker 崩溃、Redis 短时故障测试通过
- [ ] 备份对象缺失或篡改时必须失败
- [ ] 在隔离环境完成全量恢复，验证迁移 head、外键、对象引用和业务不变量
- [ ] 明确并实测 RPO/RTO

### 8.4 工程质量
- [ ] Ruff F/E=0，Mypy 目标范围=0，TypeScript/ESLint=0
- [ ] unit、contract、integration、security、recovery、acceptance、E2E 全部在 CI 运行
- [ ] 非预期 skip=0；关键模块有 line/branch 和 diff coverage 门
- [ ] 依赖和镜像可重现，制品有 SBOM、签名和 commit SHA

### 8.5 运维
- [ ] readiness 与 Alembic head 自动一致
- [ ] API、Worker、Beat、Outbox、队列、备份均有指标和告警
- [ ] 发布、回滚、备份、恢复文档中的命令经过自动验证
- [ ] 发布报告由 CI 生成并完成业务、安全、运维签核

---

## 9. 风险处置顺序

| 顺序 | 风险 | 对应需求 | 建议责任角色 | 完成标志 |
|---|---|---|---|---|
| 1 | 默认恢复与硬删除 | F-01, F-02, F-03 | 平台/后端负责人 | 危险入口下线 |
| 2 | 跨组织与 ScopeGrant | F-02, F-08, F-09 | 安全 + 后端 | 全套 API 隔离测试通过 |
| 3 | DB 运行角色与不可变表 | F-03, F-05, F-12 | DBA + 后端 | 数据库级拒绝改写 |
| 4 | Outbox/Job 闭环 | F-04 | 后端/平台 | 端到端可靠性测试通过 |
| 5 | 备份恢复完整性 | F-06, F-15 | SRE/DBA | 隔离恢复演练通过 |
| 6 | readiness/配置/密钥 | F-07, F-12 | 平台/SRE | 生产 fail-closed |
| 7 | AI 与模型真实性 | F-10, F-11 | AI/领域后端 | 真实工具和溯源验收 |
| 8 | CI/发布门/覆盖率 | F-14, F-16, F-17, F-18, F-24 | 工程效能 | 单一发布门稳定通过 |
| 9 | 架构和前端收敛 | F-20, F-21, F-22, F-23 | 架构/前后端 | 边界和 ownership 清晰 |

---

## 10. 附录

### 10.1 P0 需求速查表

| 编号 | 标题（简） | 阶段 | 核心风险 |
|---|---|---|---|
| F-01 | 默认启动 restore | 0 | 误恢复覆盖在线数据 |
| F-02 | 跨组织更新删除 | 0+1 | 跨租户数据破坏 |
| F-03 | 不可变性未强制 | 0+1 | 证据链可篡改 |
| F-04 | Outbox 未闭环 | 1 | 作业永不执行 |
| F-05 | 审计无效 | 1 | 审计可篡改+泄露 |
| F-06 | 备份恢复 fail-open | 1 | 恢复不完整且报告成功 |
| F-07 | readiness 不一致 | 0 | 健康实例被摘除 |
| F-12 | 默认密钥+明文 | 0+2 | 系统被接管 |
| F-13 | 任意文件读取+SSRF | 0+1 | 凭据泄露+内网访问 |

### 10.2 P1 需求速查表

| 编号 | 标题（简） | 阶段 | 核心风险 |
|---|---|---|---|
| F-08 | ScopeGrant 死功能 | 1 | 对象级授权无效 |
| F-09 | Job/Artifact IDOR | 1 | 跨租户访问 |
| F-10 | AI 工具未实现 | 2 | 功能不真实 |
| F-11 | 模型未入溯源 | 2 | 预测不可追溯 |
| F-14 | F821+错误映射 | 0+2 | NameError+500 伪装 |
| F-15 | 恢复吞失败 | 1 | 部分恢复报告成功 |
| F-16 | 发布门不可运行 | 2 | 发布不可信 |
| F-17 | 测试盲区 | 2 | 关键测试被跳过 |
| F-18 | 制品不可重现 | 2+3 | 供应链风险 |
| F-19 | 可观测性缺失 | 2 | 故障不可发现 |

### 10.3 P2 需求速查表

| 编号 | 标题（简） | 阶段 | 核心风险 |
|---|---|---|---|
| F-20 | 耦合过重 | 3 | 维护成本高 |
| F-21 | 阻塞 I/O | 3 | 性能+DoS |
| F-22 | 文档漂移 | 0+3 | 交付可信度低 |
| F-23 | 前端 legacy | 3 | 维护困难 |
| F-24 | 质量基线未落地 | 2+3 | 虚假安全感 |

---

> **文档结束**  
> 本 PRD 基于 `2026-07-27-irip-comprehensive-code-review.md` 审阅报告编制，所有需求编号 F-01 至 F-24 与审阅报告一一对应。待确认问题（Q-1 至 Q-9）需用户决策后方可确定实施方案。
