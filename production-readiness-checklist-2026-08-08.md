# IRIP 生产就绪完整清单

> **日期**: 2026-08-08  
> **版本**: main @ 874dcbb (v0.8.0)  
> **目标**: 以生产应用投入使用所需完成的所有工作

---

## 总览

| 类别 | P0 阻断 | P1 重要 | P2 改进 | 合计 |
|------|---------|---------|---------|------|
| 代码修复 | 8 | 15 | 25 | 48 |
| 基础设施部署 | 8 | 10 | 12 | 30 |
| **合计** | **16** | **25** | **37** | **78** |

---

## 一、代码修复项（来自四维评审）

### P0 — 阻断发布（8 项）

| # | 问题 | 文件 | 维度 |
|---|------|------|------|
| C-1 | `stream_ask` 重复调用 `_prepare_ask` → 双重对话 + 取消事件泄漏 | `packages/ai/ask_service.py:682-701` | 正确性 |
| C-2 | `insert_run` 参数名不匹配 + 缺 4 个必填参数 → 运行时 TypeError | `packages/research/planning/plan_analyzer.py:366-372` | 正确性 |
| C-3 | `ParameterService.approve` 缺部门过滤 → IDOR 跨部门审批 | `packages/parameters/service.py:382-522` | 安全 |
| C-4 | `pickle.loads` 反序列化用户上传模型 → RCE | `packages/models/adapters.py:426-428` | 安全 |
| C-5 | `_generate_fallback_script` 未转义用户输入 → 沙箱代码注入 | `packages/research/execution/step_executor.py:731-762` | 安全 |
| C-6 | Celery 任务无 `time_limit` → 一个卡死任务永久阻塞队列 | `apps/worker/tasks/*.py` | 性能 |
| C-7 | 每节点 3-6 次独立 session → 50 节点 ~1s 纯开销 | `execution_engine.py:655-727`, `step_executor.py:78-283` | 性能 |
| C-8 | `list_facts_detail` 顺序 MinIO 下载 → 20 条数据 1-2 秒 | `packages/facts/query_service.py:157-166` | 性能 |

### P1 — 当前迭代修复（15 项）

| # | 问题 | 文件 |
|---|------|------|
| C-9 | `get_fact`/`search_facts`/`list_facts` 接受 `org_id` 但从未使用 | `facts/repository.py:196-393` |
| C-10 | `get_next_run_number` SELECT MAX+1 竞态条件 | `research/execution/repository_trusted.py:366-385` |
| C-11 | `upsert_memory` read-then-write 竞态 | `research/execution/repository_trusted.py:874-905` |
| C-12 | 混合步骤 Python+LLM 使用同一 `step_id`，状态来回切换 | `research/execution/step_executor.py:587-628` |
| C-13 | LLM 未接收 Python 步骤输出，看的是原始快照 | `research/execution/step_executor.py:587-628` |
| C-14 | 审计记录用 `workspace_id` 作为 `department_id` | `research/execution/orchestrator_core.py:273` |
| C-15 | `_resolve_task_info` 每次创建新 DB engine 从不释放 → 连接泄漏 | `facts/query_service.py:540-562` |
| C-16 | 内容哈希计算不包含 Insight 内容 | `research/publication/publisher.py:707-714` |
| C-17 | `retry_node` 成功后不恢复下游节点 → Run 永久卡 running | `components/flow/execution_engine.py:447-627` |
| C-18 | 恢复容器挂载 Docker socket → 容器逃逸风险 | `compose.yaml:325` |
| C-19 | AI 生成代码直接在沙箱执行，提示注入风险 | `research/execution/step_executor.py:258-270` |
| C-20 | 文件浏览端点未拒绝符号链接 | `apps/api/routers/files.py:114-124` |
| C-21 | `list_runs` 循环内逐条查询 → N+1 | `apps/api/routers/flows.py:619-637` |
| C-22 | `identify_candidates` 循环内 2N 次查询 | `research/products/candidates.py:110-117` |
| C-23 | `get_fact_data` 无 Redis 缓存 → 每次完整 DB+MinIO 链路 | `facts/query_service.py:386-454` |

### P2 — 中期改进（25 项）

| # | 问题 | 文件 |
|---|------|------|
| C-24 | 生产代码残留 `print()` 调试语句 | `ask_service.py` 多处, converters/ |
| C-25 | `_rls_dept_id` 私有属性跨模块直接赋值（30+ 处） | `apps/api/composition/` 下 20+ 文件 |
| C-26 | 8 个纯 re-export shim 文件未清理 | `packages/components/`, `standards/`, `research/` |
| C-27 | `research/models.py` 实为 DTO 但命名误导 | `packages/research/models.py` |
| C-28 | 10 个路由文件超过 500 行（最大 955 行） | `apps/api/routers/` |
| C-29 | "not_found" 错误处理模式重复 30+ 次 | `packages/` 多个 service 文件 |
| C-30 | 7 个文件超过 750 行需拆分 | `research/models.py` 等 |
| C-31 | 测试覆盖率阈值仅 30% | `pyproject.toml:86` |
| C-32 | 10+ 处裸 `except Exception` 吞没错误 | `research/planning/plan_analyzer.py` |
| C-33 | 缺少 API 安全响应头 | `apps/api/main.py` |
| C-34 | LLM 错误响应记入日志 | `packages/ai/openai_compatible.py:147-148` |
| C-35 | `_safe_literal` 简单转义而非 `quote_literal` | `packages/common/tenant_guc.py:25-39` |
| C-36 | CORS `allow_credentials=True` 无通配符校验 | `apps/api/main.py:181-189` |
| C-37 | `evaluate_expression` 顺序解析变量 | `packages/ai/numeric/service.py:119-121` |
| C-38 | 快照数据顺序加载 + 循环内创建 S3 repo | `context_builder.py:152-184` |
| C-39 | 大文本未截断存入 JSONB | `plan_analyzer.py:194,336` |
| C-40 | 消息列表 3 秒轮询 → 应改 SSE/WebSocket | `apps/web/src/features/assistant/` |
| C-41 | 重复组件查询（相同 API 调用两次） | `apps/web/src/features/components/` |
| C-42 | Celery 异常被吞没返回 dict，无法触发自动重试 | `apps/worker/tasks/*.py` |
| C-43 | 每次任务重建 session factory | `apps/worker/tasks/*.py` |
| C-44 | `list_plans`/`list_runs` 无 LIMIT | `research/execution/repository_trusted.py:105-280` |
| C-45 | `node_exec_summaries` O(N²) 线性扫描 | `execution_engine.py:371-388` |
| C-46 | 前端缺少 `useMemo` 的 Map 构造 | `useFlowQueries.ts` |
| C-47 | Beat 调度过密（5 秒一次空查询） | `apps/worker/celery_app.py:101-105` |
| C-48 | 前端大型组件文件（>660 行） | `apps/web/src/features/` 多个 |

---

## 二、基础设施与部署缺口

### P0 — 上线前必须完成（8 项）

| # | 缺失项 | 说明 | 影响 |
|---|--------|------|------|
| I-1 | **无 TLS/HTTPS** | nginx 仅监听 80 端口，无 TLS 终止 | 所有传输明文，JWT/密码/数据可被中间人截获 |
| I-2 | **无 HSTS 安全头** | 缺少 HSTS、X-Content-Type-Options、X-Frame-Options | 浏览器安全防护缺失 |
| I-3 | **API 文档生产暴露** | `/docs`、`/openapi.json` 在所有环境均可访问 | 泄露 API 结构、参数、模型给攻击者 |
| I-4 | **无依赖漏洞扫描** | CI 中无 pip-audit/safety/trivy | 已知 CVE 漏洞可在生产中被利用 |
| I-5 | **限流仅单进程** | 内存滑动窗口，多 Worker 各自独立计数 | 多实例部署时限流不精确，可能被绕过 |
| I-6 | **无 Docker 日志轮转** | compose.yaml 未配置 logging driver | 长期运行磁盘被日志耗尽 |
| I-7 | **备份无异地存储** | 备份仅在本地 `./backups` 目录 | 主机故障导致备份和数据同时丢失 |
| I-8 | **生产环境密钥** | .env 中使用开发默认密钥 | JWT secret/master key 已知，任何人可伪造令牌 |

### P1 — 短期完成（10 项）

| # | 缺失项 | 说明 |
|---|--------|------|
| I-9 | **无 CD（持续部署）** | CI 仅做质量门检查，不构建/推送镜像、不自动部署 |
| I-10 | **无 Sentry/错误追踪** | 后端和前端均无 Sentry SDK 集成，生产错误不可见 |
| I-11 | **无日志聚合** | 无 ELK/Loki 实际部署配置，日志散落在各容器 stdout |
| I-12 | **无告警系统** | Prometheus 指标端点已存在，但无 Grafana 仪表盘 + Alertmanager 告警规则 |
| I-13 | **无 SAST** | CI 中无 bandit/semgrep 静态安全扫描 |
| I-14 | **无 Secret 扫描** | CI 中无 gitleaks/trufflehog 扫描代码中的密钥泄露 |
| I-15 | **无容器镜像扫描** | 无 Trivy/Grype 扫描 Docker 镜像漏洞 |
| I-16 | **Redis 无高可用** | 单点 Redis 故障导致异步作业投递中断（Outbox 降级但需人工介入） |
| I-17 | **数据库无高可用** | 单实例 PostgreSQL，无流复制/Patroni/RDS |
| I-18 | **无分布式追踪** | 无 OpenTelemetry/Jaeger，生产问题排查困难 |

### P2 — 中期改进（12 项）

| # | 缺失项 | 说明 |
|---|--------|------|
| I-19 | **无 K8s/IaC** | 无 Kubernetes 清单、Helm Charts 或 Terraform |
| I-20 | **无自动扩缩** | 无 HPA/Worker 自动扩缩配置 |
| I-21 | **无 PgBouncer** | 无外部连接池中间件 |
| I-22 | **无读副本** | 数据库无读写分离 |
| I-23 | **MinIO 单节点** | 无分布式/纠删码部署 |
| I-24 | **无 MinIO 生命周期策略** | 对象无 TTL/归档规则，无限增长 |
| I-25 | **无业务数据保留策略** | 审计日志/facts/artifacts 无 TTL/归档 |
| I-26 | **无 On-call Runbook** | 无值班应急手册、事故响应流程、复盘模板 |
| I-27 | **无 RTO/RPO 定义** | 文档中未定义恢复时间/恢复点目标 |
| I-28 | **负载测试不足** | 仅有 k6 冒烟测试，无负载/浸泡测试、未入 CI |
| I-29 | **前端无性能监控** | 无 Web Vitals/RUM、无 Lighthouse CI |
| I-30 | **无 GDPR/数据隐私** | 无数据导出/删除 API |

---

## 三、已有能力（不需要额外工作）

以下能力已完整实现，生产环境可直接使用：

### 安全
- Argon2id 密码哈希 + 恒定时间校验防用户枚举
- JWT 认证 + token_version 撤销机制
- 刷新令牌家族化旋转 + 重放检测
- AES-256-GCM 信封加密
- PostgreSQL RLS 多租户隔离 + fail-closed
- SSRF 防护（DNS 解析后 IP 校验 + rebinding 防护）
- 沙箱容器安全（断网、只读 FS、非 root、cap_drop ALL）
- SQL 注入防护（连接器 sqlparse 校验 + READ ONLY 事务）
- 容器安全基线（cap_drop、no-new-privileges、read_only）
- SBOM 生成（CycloneDX）

### 基础设施
- Docker Compose 全量编排（10 个服务 + 健康检查）
- CI 流水线 14 个 job（lint/typecheck/test-security/test-recovery/test-e2e 等）
- Prometheus 指标端点 + 健康探针（liveness + readiness）
- structlog JSON 结构化日志 + Correlation ID
- PITR 备份（pg_basebackup + WAL 归档 + mc mirror）
- 备份恢复脚本 + 恢复测试 + 文档
- 前端生产构建（Vite manualChunks + nginx 缓存）
- 功能开关机制（RESEARCH_MODULE_ENABLED）
- 连接池配置（pool_size=10, max_overflow=20, pool_pre_ping）
- Outbox 模式 + 租约机制（Worker 可水平扩展）

### 文档
- 系统架构概览 + 领域不变量
- 监控运维指南
- 备份恢复手册
- 安装升级指南
- 开发约定 + 用户引导

---

## 四、修复路线图

### 第 1 周 — P0 阻断项（16 项）

**代码修复（8 项）**:
1. 删除 `stream_ask` 重复 `_prepare_ask` 调用
2. 修复 `insert_run` 参数名 + 补全参数
3. `approve` 添加部门过滤 JOIN
4. `pickle.loads` 替换为安全反序列化（RestrictedUnpickler 或沙箱内执行）
5. `_generate_fallback_script` 添加输入转义
6. 为所有 Celery 任务添加 `soft_time_limit`/`time_limit`
7. 合并 `execution_engine`/`step_executor` 的 session 获取
8. 并行化 `list_facts_detail` 的 `_build_data_summary`

**基础设施（8 项）**:
9. 配置 nginx TLS/HTTPS（证书 + 443 端口监听 + HTTP→HTTPS 重定向）
10. 添加 HSTS + X-Content-Type-Options + X-Frame-Options 安全头
11. 生产环境禁用 `/docs`、`/redoc`、`/openapi.json`
12. CI 添加 `pip-audit` + `npm audit` + `trivy` 依赖扫描
13. 限流改用 Redis 实现分布式限流
14. compose.yaml 添加 Docker logging driver 配置（max-size/max-file）
15. 备份脚本添加异地存储（S3/OSS 远程同步）
16. 生成生产环境强随机密钥替换所有开发默认值

### 第 2 周 — P1 重要项（25 项）

**代码修复（15 项）**:
17. `get_fact`/`search_facts`/`list_facts` 添加 `org_id` 过滤
18. `get_next_run_number` 改用 DB 序列
19. 批量化 N+1 查询（`list_runs`、`identify_candidates`、`_resolve_task_info`）
20. `get_fact_data` 添加 Redis 缓存
21. Celery session factory 单例化
22. `retry_node` 成功后恢复下游节点
23. 混合步骤传递 Python 输出给 LLM
24. 审计记录使用正确的 `department_id`
25. `_resolve_task_info` 引擎释放/单例化
26. 移除 `compose.yaml` 中 restore 容器的 Docker socket 挂载
27. 文件浏览端点拒绝符号链接
28. `evaluate_expression` 并行解析变量
29. 快照数据并行加载 + 循环外创建 S3 repo
30. Celery 区分可重试/不可重试异常
31. `upsert_memory` 使用 `INSERT ON CONFLICT`

**基础设施（10 项）**:
32. CI 添加 `bandit` 或 `semgrep` SAST 扫描
33. CI 添加 `gitleaks` secret 扫描
34. CI 添加 `trivy` 容器镜像扫描
35. 集成 Sentry SDK（后端 + 前端）
36. 配置 Grafana 仪表盘 + Alertmanager 告警规则
37. 配置 Loki 或 ELK 日志聚合
38. 前端添加 ErrorBoundary + Sentry 错误追踪
39. 编写 On-call Runbook + 事故响应流程
40. 定义 RTO/RPO 目标并文档化
41. 消息列表从轮询改为 SSE 推送

### 第 3-4 周 — P2 改进项（37 项）

**代码修复（25 项）**:
42. 移除 `print()` 调试语句 → `logging.debug()`
43. `_rls_dept_id` 封装泄漏 → 公开 `set_rls_override()` 方法
44. 清理 8 个 shim 文件
45. 修正 `research/models.py` → `dtos.py` 命名
46. 拆分 10 个胖路由文件
47. 提取重复 not_found 错误处理为辅助函数
48. 拆分 7 个 >750 行大文件
49. 测试覆盖率阈值 30% → 60%
50. 收窄 10+ 处裸 `except Exception`
51. 添加 API 安全响应头中间件
52. LLM 错误日志脱敏
53. `_safe_literal` 改用 `quote_literal`
54. CORS 添加通配符拒绝校验
55. 大文本存入 JSONB 前截断
56. 合并重复前端组件查询
57. 前端 Map 构造添加 `useMemo`
58. Beat 调度频率 5s → 15-30s
59. 拆分前端大型组件文件
60-64. （其余 P2 见代码评审详细报告）

**基础设施（12 项）**:
65. 评估 K8s 部署方案（如需水平扩展）
66. 配置 PgBouncer 连接池代理
67. 评估 Redis Sentinel 高可用
68. 评估 PostgreSQL 流复制/Patroni
69. MinIO 生命周期策略配置
70. 业务数据保留策略 + 审计日志归档
71. k6 负载/浸泡测试入 CI
72. 前端 Lighthouse CI + Web Vitals
73. 分布式追踪（OpenTelemetry）
74. GDPR 数据导出/删除 API
75. CD 流水线（镜像构建 → 推送 → 部署）
76. 标准架构图（Mermaid/Draw.io）

---

## 五、生产环境部署检查清单

部署到生产环境前逐项确认：

### 密钥与配置
- [ ] JWT secret 使用强随机值（≥256 bit），非开发默认值
- [ ] Master key 使用强随机值
- [ ] 数据库密码使用强随机值
- [ ] Redis 密码使用强随机值
- [ ] MinIO secret key 使用强随机值
- [ ] AI API key 已加密存储
- [ ] IRIP_ALLOW_PRIVATE_NETWORK=false（生产环境关闭）
- [ ] IRIP_ENV=production
- [ ] 所有 `.env` 密钥通过密钥管理系统注入，不存储在代码仓库

### 网络
- [ ] TLS 证书已配置且有效
- [ ] HTTP 自动重定向到 HTTPS
- [ ] HSTS 头已设置
- [ ] CSP 头已配置
- [ ] X-Content-Type-Options: nosniff
- [ ] X-Frame-Options: DENY
- [ ] CORS origins 仅包含生产域名
- [ ] CORS 拒绝通配符

### 应用
- [ ] `/docs`、`/redoc`、`/openapi.json` 在生产环境禁用
- [ ] IRIP_LOG_LEVEL=INFO（非 DEBUG）
- [ ] IRIP_API_CORS_ORIGINS 设置为生产前端域名
- [ ] IRIP_MINIO_EXTERNAL_ENDPOINT 设置为外部可达地址

### 数据库
- [ ] 迁移已执行到最新版本
- [ ] RLS 策略已启用
- [ ] 运行时角色非 superuser/bypassrls（启动断言已通过）
- [ ] PITR 备份已配置并验证
- [ ] 首次备份已成功
- [ ] 备份异地存储已配置

### 容器
- [ ] Docker 日志驱动配置（max-size: 100m, max-file: 5）
- [ ] 所有容器 cap_drop: ALL + no-new-privileges
- [ ] restore 容器不挂载 Docker socket
- [ ] 沙箱容器使用非 root 用户

### 监控
- [ ] `/api/v1/health/live` 返回 200
- [ ] `/api/v1/health/ready` 所有检查通过
- [ ] Worker `/health` 端点返回 200
- [ ] Prometheus scrape 已配置
- [ ] Grafana 仪表盘已导入
- [ ] Alertmanager 告警规则已配置
- [ ] Sentry 错误追踪已集成

### 安全
- [ ] 依赖漏洞扫描已通过
- [ ] SAST 扫描已通过
- [ ] Secret 扫描已通过
- [ ] 容器镜像扫描已通过
- [ ] 限流中间件已启用（Redis 分布式限流）

### 功能验证
- [ ] 登录 + 令牌刷新正常
- [ ] 文件上传 + 下载正常
- [ ] AI 助手对话正常
- [ ] 流程执行正常
- [ ] 备份 + 恢复验证通过
- [ ] k6 冒烟测试通过

---

*本清单基于代码四维评审（79 项）+ 基础设施生产就绪审计（20 个领域）综合生成。*
