# IRIP 全项目代码评审报告

> **评审日期**: 2026-08-08  
> **评审范围**: 后端 ~91K 行 Python + 前端 ~44K 行 TS/TSX  
> **评审维度**: 正确性 · 安全 · 性能 · 可维护性  
> **项目版本**: main @ 874dcbb (v0.8.0)

---

## 总览

| 维度 | Critical | High/Important | Medium/Suggestion | 合计 |
|------|----------|----------------|-------------------|------|
| 正确性 | 3 | 9 | 8 | 20 |
| 安全 | 2 | 5 | 7 | 14 |
| 性能 | 7 | 10 | 6 | 23 |
| 可维护性 | 3 | 9 | 10 | 22 |
| **合计** | **15** | **33** | **31** | **79** |

---

## P0 — 必须立即修复（8 项）

> 这些问题会导致功能崩溃、数据泄露或安全漏洞，应阻塞发布。

### P0-1. `stream_ask` 重复调用 `_prepare_ask` — 双重副作用 + 资源泄漏

**维度**: 正确性 + 可维护性  
**文件**: `packages/ai/ask_service.py:682-701`  
**严重程度**: Critical

`stream_ask` 方法连续两次调用 `_prepare_ask`，第二次覆盖了第一次的 `ctx`。`_prepare_ask` 内部会创建对话、注册取消事件、重载数据库工具声明。

**影响**:
- `conversation_id` 为 None 时创建**两个对话**，第一个成为孤儿
- `CancellationRegistry` 注册两个取消事件，`finally` 只清理一个，造成**资源泄漏**
- 重复的计时日志输出

**修复**: 删除第二个 `_prepare_ask` 调用（line 693-701），保留第一个。

---

### P0-2. `insert_run` 调用参数与方法签名不匹配 — 运行时 TypeError

**维度**: 正确性  
**文件**: `packages/research/planning/plan_analyzer.py:366-372, 568-577`  
**严重程度**: Critical

调用方传递 `plan_id`（应为 `plan_version_id`）和 `status`（方法不接受此参数），同时缺少 `snapshot_id`、`run_number`、`image_digest`、`created_by` 四个必填参数。

**影响**: `analyze_data` 和 `extract_insight` 整个流程在运行时直接抛出 `TypeError`，功能完全不可用。

**修复**: 使用正确的参数名和完整参数列表调用 `insert_run`。

---

### P0-3. `ParameterService.approve` 缺少部门过滤 — IDOR 跨部门审批

**维度**: 正确性 + 安全  
**文件**: `packages/parameters/service.py:382-522`  
**严重程度**: Critical

`approve` 方法直接按 `candidate_id` 查询，**没有 JOIN `Parameter` 表做部门过滤**。而同文件的 `reject` 方法正确地做了 JOIN 过滤。

**影响**: 任何部门用户只要知道 `candidate_id`，就能审批不属于本部门的参数候选，绕过租户隔离。

**修复**:
```python
# approve 方法中添加 JOIN 过滤
candidate = await session.scalar(
    sa.select(ParameterCandidate)
    .join(Parameter, ParameterCandidate.parameter_id == Parameter.id)
    .where(ParameterCandidate.id == candidate_id)
)
```

---

### P0-4. `pickle.loads` 反序列化用户上传的模型 — 远程代码执行 (RCE)

**维度**: 安全  
**文件**: `packages/models/adapters.py:426-428`  
**严重程度**: Critical

`PythonModelAdapter.load()` 使用 `pickle.loads()` 反序列化用户上传的模型工件。任何拥有 `model:manage` 权限的用户可上传恶意 pickle 文件，在 Worker 进程中执行任意代码。

**利用路径**: 上传恶意 pickle → 触发模型预测 → Worker 进程 RCE → 访问数据库/MinIO/Docker

**修复**: 使用 `RestrictedUnpickler` 限制可反序列化的类，或迁移到 ONNX/TF SavedModel 等安全格式，或将 pickle 反序列化放入沙箱容器。

---

### P0-5. `_generate_fallback_script` 未转义用户输入 — 沙箱代码注入

**维度**: 正确性 + 安全  
**文件**: `packages/research/execution/step_executor.py:731-762`  
**严重程度**: Critical

`question` 来自用户输入，直接通过 f-string 嵌入生成的 Python 脚本。包含单引号会破坏脚本语法，包含 `', os.system('rm -rf /'), '` 构成代码注入。

**修复**: 使用 `json.dumps(question)` 安全嵌入，或对 `question` 进行转义。

---

### P0-6. Celery 任务缺少超时配置 — 队列永久阻塞

**维度**: 性能  
**文件**: `apps/worker/tasks/flows.py:150,223` / `derivation.py:146` / `models.py:217,238,259`  
**严重程度**: Critical

所有 Celery 任务未设置 `time_limit` / `soft_time_limit`。配合 `worker_prefetch_multiplier=1`，一个卡死的任务会永久阻塞整个队列。

**修复**:
```python
@celery_app.task(name="irip.flow.execute", soft_time_limit=600, time_limit=660)
def execute_flow_job(...): ...
```

---

### P0-7. 每节点/每步骤多次独立 session 获取 — 性能瓶颈

**维度**: 性能  
**文件**: `packages/components/flow/execution_engine.py:655-727` / `packages/research/execution/step_executor.py:78-283`  
**严重程度**: Critical

执行引擎每个节点 3 次 session 获取（含 GUC 设置 ~5-10ms/次），`retry_node` 更达 6 次。研究步骤执行器同样每步骤 5-10 次 session 获取。50 个节点 = 150+ 次 session = ~750ms-1.5s 纯开销。

**修复**: 将状态更新合并到单一 session 上下文中，或使用批量 UPDATE。

---

### P0-8. `list_facts_detail` 顺序 MinIO 下载 — 1-2 秒延迟

**维度**: 性能  
**文件**: `packages/facts/query_service.py:157-166`  
**严重程度**: Critical

page_size=20 时，循环内逐条调用 `_build_data_summary`（每次 2 次 DB 查询 + 1 次 MinIO 下载），全部顺序执行。总延迟 1-2 秒。

**修复**: 用 `asyncio.gather` 并行执行，将 `find_json_artifact` 改为批量 IN 查询。

---

## P1 — 应在当前迭代修复（15 项）

### 正确性

| # | 文件 | 问题 | 影响 |
|---|------|------|------|
| P1-1 | `facts/repository.py:196-393` | `get_fact`/`search_facts`/`list_facts` 接受 `org_id` 但从未使用 | RLS 配置错误时可跨部门访问 |
| P1-2 | `research/execution/repository_trusted.py:366-385` | `get_next_run_number` 使用 SELECT MAX+1 模式，TOCTOU 竞态 | 并发时重复编号 |
| P1-3 | `research/execution/repository_trusted.py:874-905` | `upsert_memory` read-then-write 竞态 | 并发插入产生多条记录 |
| P1-4 | `research/execution/step_executor.py:587-628` | 混合步骤 Python+LLM 使用同一 `step_id`，状态来回切换 | 状态历史混乱 |
| P1-5 | `research/execution/step_executor.py:587-628` | LLM 未接收 Python 步骤输出，看的是原始快照数据 | "混合"步骤实际是两个独立步骤 |
| P1-6 | `research/execution/orchestrator_core.py:273` | 审计记录用 `workspace_id` 作为 `department_id` | 审计记录部门归属错误 |
| P1-7 | `facts/query_service.py:540-562` | `_resolve_task_info` 每次创建新 DB engine 从不释放 | 连接池泄漏 |
| P1-8 | `research/publication/publisher.py:707-714` | 内容哈希计算不包含 Insight 内容 | 内容篡改不可检测 |
| P1-9 | `components/flow/execution_engine.py:447-627` | `retry_node` 成功后不恢复下游节点执行 | Run 永久卡在 running 状态 |

### 安全

| # | 文件 | 问题 | 影响 |
|---|------|------|------|
| P1-10 | `compose.yaml:325` | 恢复容器挂载 Docker socket | 容器逃逸到宿主机 |
| P1-11 | `research/execution/sandbox.py:574` | 沙箱 User 字段与文档不一致 | 可能以非预期权限运行 |
| P1-12 | `research/execution/step_executor.py:258-270` | AI 生成代码直接在沙箱执行，提示注入风险 | 通过提示注入获取沙箱内数据 |
| P1-13 | `apps/api/routers/files.py:114-124` | 文件浏览端点未拒绝符号链接 | 可通过符号链接泄露宿主文件 |
| P1-14 | `apps/api/routers/ai_config.py:211-223` | API key 掩码作用于加密值而非明文 | 暴露加密格式信息 |

### 性能

| # | 文件 | 问题 | 影响 |
|---|------|------|------|
| P1-15 | `apps/api/routers/flows.py:619-637` | `list_runs` 循环内逐条查询 `get_latest_node_execution` | N+1 查询，100 runs = 100 额外查询 |
| P1-16 | `facts/query_service.py:493-712` | `_resolve_task_info` 26 次顺序 DB 查询 | 500ms+ 延迟 |
| P1-17 | `research/products/candidates.py:110-117` | `identify_candidates` 循环内 2N 次查询 | 10 runs = 20 额外查询 |
| P1-18 | `research/execution/repository_trusted.py:105-280` | `list_plans`/`list_runs` 无 LIMIT | 无界查询 |
| P1-19 | `facts/query_service.py:386-454` | `get_fact_data` 无 Redis 缓存 | 每次完整 DB+MinIO 查询链路 |
| P1-20 | `apps/worker/tasks/*.py` | 每次任务重建 session factory | 50-100ms/次连接池初始化开销 |
| P1-21 | `apps/web/src/features/assistant/...` | 消息列表 3 秒轮询 | 10 用户 = 200 请求/分钟 |
| P1-22 | `apps/web/src/features/components/...` | 重复组件查询（相同 API 调用两次） | 双倍网络请求 |
| P1-23 | `apps/worker/tasks/*.py` | 异常被吞没返回 dict，Celery 认为成功 | 无法触发自动重试 |

---

## P2 — 中期优化（25 项）

### 可维护性

| # | 问题 | 涉及文件 |
|---|------|---------|
| P2-1 | `_rls_dept_id` 私有属性跨模块直接赋值（30+ 处） | `apps/api/composition/` 下 20+ 文件 |
| P2-2 | 8 个纯 re-export 的 shim 文件未清理 | `packages/components/`, `packages/standards/`, `packages/research/` |
| P2-3 | `research/models.py` 实为 DTO 但命名为 models | `packages/research/models.py` (1255 行) |
| P2-4 | `citation.py` 与 `citations.py` 命名易混淆 | `packages/ai/citation.py`, `packages/ai/citations.py` |
| P2-5 | 10 个路由文件超过 500 行（最大 955 行） | `apps/api/routers/` |
| P2-6 | 路由间跨模块导入响应模型 | `routers/flows.py:66` 导入 `routers/facts.py` |
| P2-7 | "not_found" 错误处理模式重复 30+ 次 | `packages/` 目录多个 service 文件 |
| P2-8 | 7 个文件超过 750 行需拆分 | 见详细报告 |
| P2-9 | `_thinking_enabled` 私有属性直接访问 | `packages/ai/ask_service.py:247-249` |
| P2-10 | 测试覆盖率阈值仅 30% | `pyproject.toml:86` |

### 正确性

| # | 问题 | 文件 |
|---|------|------|
| P2-11 | 10+ 处裸 `except Exception` 吞没错误 | `research/planning/plan_analyzer.py` |
| P2-12 | 版本号自动递增竞态 + ValueError 未处理 | `components/registry/registry.py:335-366` |
| P2-13 | 独立 session 可能绕过 RLS GUC | `research/snapshots.py:348-376` |
| P2-14 | detached ORM 对象访问 | `execution_engine.py:477-561` |
| P2-15 | 多个独立 session 缺乏事务原子性 | `orchestrator_core.py` |
| P2-16 | 原始 SQL + 内联 S3 客户端构建 | `plan_analyzer.py` |

### 安全

| # | 问题 | 文件 |
|---|------|------|
| P2-17 | 缺少 API 安全响应头 | `apps/api/main.py` |
| P2-18 | LLM 错误响应记入日志（可能含敏感信息） | `packages/ai/openai_compatible.py:147-148` |
| P2-19 | `_safe_literal` 使用简单转义而非 `quote_literal` | `packages/common/tenant_guc.py:25-39` |
| P2-20 | CORS `allow_credentials=True` 无通配符校验 | `apps/api/main.py:181-189` |

### 性能

| # | 问题 | 文件 |
|---|------|------|
| P2-21 | `evaluate_expression` 顺序解析变量 | `packages/ai/numeric/service.py:119-121` |
| P2-22 | 快照数据顺序加载 + 循环内创建 S3 repo | `context_builder.py:152-184` |
| P2-23 | 大文本未截断存入 JSONB | `plan_analyzer.py:194,336` |
| P2-24 | Beat 调度过密（5 秒一次空查询） | `apps/worker/celery_app.py:101-105` |
| P2-25 | `node_exec_summaries` O(N²) 线性扫描 | `execution_engine.py:371-388` |

---

## 正面发现 — 项目做得好的方面

### 安全控制
1. **密码哈希**: Argon2id + 恒定时间校验防用户枚举
2. **JWT 撤销**: 通过 `token_version` claim 实现令牌撤销
3. **刷新令牌旋转**: 实现了重放检测和整族撤销
4. **信封加密**: AES-256-GCM 加密敏感数据
5. **SSRF 防护**: DNS 解析后 IP 校验 + DNS rebinding 防护 + 重定向重检
6. **RLS 多租户隔离**: PostgreSQL 行级安全 + GUC + fail-closed 语义
7. **SQL 注入防护**: 连接器使用 `sqlparse` 校验 + READ ONLY 事务
8. **文件上传限制**: 媒体类型白名单 + 大小限制
9. **沙箱安全配置**: 断网、只读 FS、非 root、cap_drop ALL、no-new-privileges
10. **RLS superuser 断言**: 启动时检查运行时角色非 superuser/bypassrls

### 可维护性
1. **一致的架构分层**: packages 按领域模块组织，内部遵循 entities→repository→service→routers
2. **不可变值对象**: 大量使用 `@dataclass(frozen=True)`
3. **完善的文档字符串**: 几乎所有模块/类/方法都有中文文档字符串
4. **依赖注入模式**: Service 通过构造函数注入，路由层通过 FastAPI DI
5. **ScopedSessionMixin**: 统一数据库会话管理 + RLS GUC
6. **ErrorCode 封闭枚举**: 错误码集中管理
7. **Feature flags 机制**: 环境变量控制模块启停
8. **代码质量工具链**: ruff + mypy(strict) + pytest(coverage)

---

## 修复优先级路线图

### 第 1 周 — P0 紧急修复
1. 删除 `stream_ask` 中重复的 `_prepare_ask` 调用
2. 修复 `insert_run` 参数名和缺失参数
3. `approve` 方法添加部门过滤 JOIN
4. `pickle.loads` 替换为安全反序列化
5. `_generate_fallback_script` 添加输入转义
6. 为所有 Celery 任务添加 `soft_time_limit`/`time_limit`
7. 合并 `execution_engine` 和 `step_executor` 的 session 获取
8. 并行化 `list_facts_detail` 的 `_build_data_summary`

### 第 2 周 — P1 短期修复
9. `get_fact`/`search_facts`/`list_facts` 添加 `org_id` 过滤
10. `get_next_run_number` 改用数据库序列
11. 批量化 N+1 查询（`list_runs`、`identify_candidates`、`_resolve_task_info`）
12. `get_fact_data` 添加 Redis 缓存
13. Celery session factory 单例化
14. `retry_node` 成功后恢复下游节点
15. 混合步骤传递 Python 输出给 LLM
16. 审计记录使用正确的 `department_id`
17. `_resolve_task_info` 引擎释放/单例化

### 第 3-4 周 — P2 中期优化
18. `_rls_dept_id` 封装泄漏修复
19. 清理 shim 文件
20. 拆分大文件（>750 行）
21. 提取重复的 not_found 错误处理
22. 移除 `print()` 调试语句
23. 提高测试覆盖率阈值到 60%
24. 添加安全响应头中间件
25. 修正命名不一致

---

*本报告由 4 个并行评审 agent 生成，覆盖 79 个发现项。*
