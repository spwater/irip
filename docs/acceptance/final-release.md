# V3 最终发布验收文档

> 版本：0.1.0 · Phase V0–V3 全栈交付
> 验收日期：2026-07-22
> 关联文档：`README.md`、`docs/architecture/system-overview.md`、`docs/acceptance/v1-particle-size.md`、`docs/acceptance/security-recovery.md`

---

## 1. V0–V3 功能清单

### V0：平台骨架

| 功能 | 状态 | 验证方式 |
|------|------|---------|
| 用户认证（Argon2id + JWT） | ✅ 交付 | `tests/integration/auth/test_login_flow.py` |
| 刷新令牌家族化旋转 + 重放检测 | ✅ 交付 | `tests/unit/auth/test_tokens.py` |
| RBAC 7 角色 + 对象级 ScopeGrant | ✅ 交付 | `tests/unit/auth/test_permissions.py` |
| 审计事件仅追加 + 脱敏 | ✅ 交付 | `tests/security/test_object_scope_enforcement.py` |
| MinIO 内容寻址工件服务 | ✅ 交付 | `tests/integration/storage/test_artifacts.py` |
| 异步作业（Outbox + 租约 + 幂等） | ✅ 交付 | `tests/integration/jobs/test_job_lifecycle.py` |
| React 控制台外壳（登录/守卫/作业抽屉） | ✅ 交付 | `pnpm --dir apps/web test --run` |
| Docker Compose + Bootstrap + 健康检查 | ✅ 交付 | `tests/integration/test_v0_bootstrap.py` |

### V1：粒度分析全链路

| 功能 | 状态 | 验证方式 |
|------|------|---------|
| L1 标准变量注册 + 不可变版本 | ✅ 交付 | `tests/unit/standards/` |
| L2 事实创建 + 不可变修订 | ✅ 交付 | `tests/unit/facts/` |
| L2 质量评估引擎 | ✅ 交付 | `tests/unit/facts/` |
| L2.5 证据集冻结 + 不可变版本 | ✅ 交付 | `tests/unit/provenance/` |
| L2.5 推导配方 + 确定性回放 | ✅ 交付 | `tests/unit/provenance/` |
| L2.5 BFS 溯源图 | ✅ 交付 | `tests/unit/provenance/` |
| L3 参数候选 + 审批分离 | ✅ 交付 | `tests/unit/parameters/` |
| L3 参数不可变发布 + 过期检测 | ✅ 交付 | `tests/unit/parameters/` |
| 数据连接器（PostgreSQL/REST/File） | ✅ 交付 | `tests/unit/connectors/` |
| MappingProfile 字段映射 | ✅ 交付 | `tests/unit/connectors/` |
| 前端 8 个页面 | ✅ 交付 | `pnpm --dir apps/web test --run` |

### V2：组件系统 + 流程引擎 + 模型生命周期

| 功能 | 状态 | 验证方式 |
|------|------|---------|
| 组件清单 Schema v1 + ManifestValidator | ✅ 交付 | `tests/contract/test_component_manifest.py` |
| 组件注册表（不可变版本） | ✅ 交付 | `tests/integration/components/test_registry.py` |
| Python + CLI 执行器 | ✅ 交付 | `tests/unit/components/` |
| 25 个内置组件（7 摄入 + 7 转换 + 4 质量 + 4 统计 + 3 输出 + 4 模型） | ✅ 交付 | `tests/unit/components/` |
| 流程 DAG 校验（Kahn 算法） | ✅ 交付 | `tests/unit/components/test_flow_validation.py` |
| 流程节点级可恢复执行 | ✅ 交付 | `tests/integration/components/test_flow_runtime.py` |
| 模型契约 + CLIModelAdapter | ✅ 交付 | `tests/contract/test_model_adapter.py` |
| 模型生命周期状态机 | ✅ 交付 | `tests/integration/models/test_model_lifecycle.py` |
| 适用域检查 | ✅ 交付 | `tests/unit/components/` |
| 篦冷机 ROM 确定性数据集 + 训练 | ✅ 交付 | `tests/unit/examples/test_grate_cooler_fixture.py` |
| 前端组件/流程/模型/预测工作台页面 | ✅ 交付 | `pnpm --dir apps/web test --run` |

### V3：AI 助手 + 治理控制台 + 备份恢复

| 功能 | 状态 | 验证方式 |
|------|------|---------|
| AIProvider 协议（OpenAI 兼容 + 离线模拟） | ✅ 交付 | `tests/unit/ai/` |
| AI 工具白名单（7 个只读工具） | ✅ 交付 | `tests/unit/ai/test_tool_policy.py` |
| AI 引用可溯源 | ✅ 交付 | `tests/integration/ai/test_offline_citations.py` |
| AIService 对话编排 | ✅ 交付 | `tests/integration/ai/` |
| 治理控制台（用户/角色/授权/审计/健康） | ✅ 交付 | `tests/integration/` |
| 作业监控页面 | ✅ 交付 | `tests/integration/` |
| 备份脚本（pg_dump + MinIO sync） | ✅ 交付 | `tests/recovery/test_backup_restore.py` |
| 恢复脚本（SHA-256 完整性校验） | ✅ 交付 | `tests/recovery/test_backup_restore.py` |
| BackupManifest 完整性校验 | ✅ 交付 | `tests/recovery/test_backup_restore.py` |
| 备份恢复 API（异步作业化） | ✅ 交付 | `tests/integration/` |
| 令牌重放防护 | ✅ 交付 | `tests/security/test_token_replay.py` |
| 上传限制防护 | ✅ 交付 | `tests/security/test_upload_limits.py` |
| 路径穿越防护 | ✅ 交付 | `tests/security/test_path_traversal.py` |
| SQL 注入防护 | ✅ 交付 | `tests/security/test_sql_injection.py` |
| AI 工具逃逸防护 | ✅ 交付 | `tests/security/test_ai_tool_escape.py` |
| Redis 丢失恢复 | ✅ 交付 | `tests/recovery/test_redis_loss.py` |
| MinIO 中断恢复 | ✅ 交付 | `tests/recovery/test_minio_outage.py` |
| 迁移回滚可逆性 | ✅ 交付 | `tests/recovery/test_migration_rollback.py` |
| k6 性能冒烟测试 | ✅ 交付 | `tests/performance/k6-smoke.js` |

---

## 2. 验收测试结果

### 2.1 测试套件汇总

| 测试类别 | 目录 | 测试文件数 | 测试用例数 | 结果 |
|---------|------|-----------|-----------|------|
| 单元测试 | `tests/unit/` | — | — | ✅ 全部通过 |
| 集成测试 | `tests/integration/` | — | — | ✅ 全部通过 |
| 契约测试 | `tests/contract/` | — | — | ✅ 全部通过 |
| 安全测试 | `tests/security/` | 5 | 84 | ✅ 全部通过 |
| 恢复测试 | `tests/recovery/` | 4 | — | ✅ 全部通过 |
| 验收测试 | `tests/acceptance/` | — | — | ✅ 全部通过 |
| 前端单元测试 | `apps/web/` | — | — | ✅ 全部通过 |
| 前端 E2E | `tests/e2e/` | — | — | ✅ 全部通过 |
| 性能冒烟 | `tests/performance/` | 1 | 4 项阈值 | ✅ P95 < 500ms |

### 2.2 发布门执行

```bash
bash scripts/release-gate.sh
```

发布门串联执行：
1. ✅ ruff lint — 0 errors
2. ✅ mypy strict — 0 errors
3. ✅ pytest unit + property + contract + integration + security + recovery + acceptance — 100% pass
4. ✅ pnpm lint — 0 errors
5. ✅ pnpm test — 100% pass
6. ✅ pnpm build — success
7. ✅ docker compose up —build -d — 全部服务健康
8. ✅ pnpm e2e — 100% pass
9. ✅ 清理（docker compose down -v）

**结果：RELEASE GATE PASSED**

### 2.3 验收路径验证

| 路径 | 描述 | 结果 |
|------|------|------|
| V0 验收 | Docker Compose up → Bootstrap 幂等 → 登录 → 上传去重 → 作业全链路 → Worker 重启恢复 | ✅ 通过 |
| V1 验收 | 标准变量 → 数据摄入 → 事实创建 → 证据集冻结 → 推导运行 → 参数审批 → 溯源导航 | ✅ 通过 |
| V2 验收 | 组件注册 → 流程编排 → 模型训练 → 发布 → 预测工作台 → 预测溯源 | ✅ 通过 |
| V3 验收 | AI 助手对话 → 工具调用 → 引用溯源 → 治理控制台 → 备份恢复完整性 | ✅ 通过 |

---

## 3. 已知限制

| # | 限制 | 影响范围 | 缓解措施 | 未来计划 |
|---|------|---------|---------|---------|
| 1 | macOS 本机 Docker Compose 全量验收受限 | volume 挂载/网络/文件事件 | 本机仅跑三容器 + testcontainers；完整验收在 Linux CI | — |
| 2 | 流程可视化编辑器未实现 | 流程定义通过 API/YAML，无拖拽编辑 | 通过 API/YAML 定义流程 | P2 可视化编辑器 |
| 3 | Scope Grant 管理 UI 延后 | 范围授权仅 API + 种子 | 通过 API 管理授权 | 未来迭代补 UI |
| 4 | AI 工具速率限制未实现 | AI 工具调用无限流 | 当前依赖白名单约束 | 按需引入限流 |
| 5 | AI 对话历史无自动清理 | 对话永久保留 | 手动删除 | 按需引入清理策略 |
| 6 | Redis 未纳入备份 | Celery 任务可重放，会话可重建 | Redis 仅缓存/队列，非权威存储 | 按需 `redis-cli --rdb` |
| 7 | Prometheus 指标暴露未实现 | 仅 Docker healthcheck + 日志监控 | 通过健康端点 + 日志监控 | 接入 Prometheus |
| 8 | Outbox dispatcher 单实例 | 无 leader election | V0 单实例即可 | V3+ 引入 Redis SETNX |
| 9 | 组件沙箱为操作系统级限制 | 非容器隔离 | subprocess + resource.setrlimit | 按需引入容器隔离 |
| 10 | 前端 E2E 需要 Docker 环境 | E2E 测试需完整服务运行 | 本机开发可跳过 E2E | CI 环境 |

---

## 4. 发布版本号

- **版本号**：0.1.0
- **阶段**：Phase V0–V3 全栈交付
- **数据库迁移版本**：0021_ai_conversations
- **发布门脚本**：`scripts/release-gate.sh`

---

## 5. 文档完整性检查

| 文档 | 路径 | 状态 |
|------|------|------|
| README | `README.md` | ✅ 存在 |
| 系统架构概览 | `docs/architecture/system-overview.md` | ✅ 存在 |
| 领域不变量 | `docs/architecture/domain-invariants.md` | ✅ 存在 |
| 粒度分析用户指南 | `docs/user-guide/particle-size.md` | ✅ 存在 |
| 篦冷机 ROM 用户指南 | `docs/user-guide/grate-cooler-rom.md` | ✅ 存在 |
| 数据上线指南 | `docs/data-onboarding/mapping-profile.md` | ✅ 存在 |
| 模型上线指南 | `docs/model-onboarding/model-adapter.md` | ✅ 存在 |
| 安装升级指南 | `docs/operations/install-upgrade.md` | ✅ 存在 |
| 监控运维指南 | `docs/operations/monitoring.md` | ✅ 存在 |
| 备份恢复指南 | `docs/operations/backup-restore.md` | ✅ 存在 |
| V3 最终发布验收 | `docs/acceptance/final-release.md` | ✅ 存在 |
| V1 粒度验收报告 | `docs/acceptance/v1-particle-size.md` | ✅ 存在 |
| 安全恢复验收报告 | `docs/acceptance/security-recovery.md` | ✅ 存在 |
| 发布门脚本 | `scripts/release-gate.sh` | ✅ 存在 |
| 验收测试 | `tests/acceptance/test_documented_commands.py` | ✅ 存在 |

---

## 6. 验收签核

| 角色 | 签核人 | 日期 | 状态 |
|------|--------|------|------|
| 产品经理 | — | 2026-07-22 | ⏳ 待签核 |
| 架构师 | 高见远 | 2026-07-22 | ⏳ 待签核 |
| 工程师 | 寇豆码 | 2026-07-22 | ⏳ 待签核 |
| QA | — | 2026-07-22 | ⏳ 待签核 |

> 全部签核通过后，版本 0.1.0 正式发布。

---

## 7. 能力标记（F-22: 文档漂移治理）

> 能力标记规范：Proposed（提议）/ Partial（部分实现）/ Implemented（已实现）/ Verified（已验证）/ Deprecated（已废弃）
> 统计数据由 `scripts/generate-stats.py` 自动生成，验收报告由 CI 针对 commit SHA 生成。

### 7.1 核心能力矩阵

| 能力领域 | 能力项 | 状态 | 备注 |
|---------|--------|------|------|
| 认证 | 用户认证（Argon2id + JWT） | Verified | 整改后 fail-closed + token version |
| 认证 | 刷新令牌家族化旋转 | Verified | 重放检测已验证 |
| 授权 | RBAC 7 角色 + ScopeGrant | Verified | 整改后对象级授权已验证 |
| 租户隔离 | 路由不访问服务私有属性 | Implemented | F-20/T3-1: 公开属性替代私有访问 |
| 租户隔离 | Repository 强制 (org_id, id) 复合键 | Verified | F-02/T1-2: 已验证 |
| 租户隔离 | PostgreSQL RLS | Implemented | F-02: RLS policy 已创建 |
| 不可变性 | 不可变表触发器 | Implemented | F-03: BEFORE UPDATE/DELETE 触发器 |
| 不可变性 | 事实删除改为 tombstone | Implemented | F-03: status='archived' |
| 异步作业 | Outbox→Dispatcher→Celery 闭环 | Verified | F-04: 已验证闭环 |
| 密钥安全 | envelope encryption | Implemented | F-12: AES-GCM 信封加密 |
| 密钥安全 | 启动拒绝默认值 | Implemented | F-12: ${VAR:?required} |
| 备份 | fail-closed 全量校验 | Verified | F-06: 已验证 |
| 架构 | Composition Root 按领域拆分 | Implemented | F-20/T3-2: composition/ provider 模块 |
| 架构 | 消除领域循环依赖 | Implemented | F-20/T3-3: 延迟导入 + 接口抽象 |
| 异步 I/O | 阻塞 I/O 放 asyncio.to_thread | Implemented | F-21/T3-7: CSV/JSON/Excel/PDF 读取器 |
| 异步 I/O | 流式文件上传 | Implemented | F-21/T3-7: 分块读取替代一次性加载 |
| 前端 | API client 按领域拆分 | Implemented | F-23/T3-4: auth/jobs/facts/flows 等 8 模块 |
| 前端 | legacy.tsx 清除 | Implemented | F-23/T3-4: 已删除 |
| 文档 | 源码统计自动生成 | Implemented | F-22/T3-5: scripts/generate-stats.py |
| 文档 | 能力标记规范化 | Implemented | F-22/T3-5: 本节 |
| 供应链 | 依赖锁定 | Partial | F-18/T3-6: requirements.lock 创建，uv.lock 待生成 |
| 供应链 | GitHub Actions SHA pin | Implemented | F-18/T3-6: checkout/setup-python/upload-artifact |
| 供应链 | Dockerfile --frozen-lockfile | Implemented | F-18/T3-6: web.Dockerfile 已修复 |
| 供应链 | SBOM 生成 | Implemented | F-18/T3-6: CI sbom job (cyclonedx) |
| 质量 | Ruff lint + format check 统一 | Implemented | F-24/T3-8: Makefile/CI/release-gate 一致 |
| 质量 | Ruff F821/F/E 清零 | Implemented | F-14/T2-6: 已清零 |
| 质量 | AppError 封闭 Enum | Implemented | F-14/T2-6: ErrorCode 枚举 |
| AI | AI 工具白名单 + 只读 | Verified | F-10: 7 个只读工具已验证 |
| AI | 引用可溯源 | Verified | F-10: 结构化 citation 已验证 |
| 监控 | Prometheus 指标暴露 | Implemented | F-19: /api/v1/metrics 端点 |
| 监控 | 结构化日志 + Correlation ID | Implemented | F-19: structlog + CorrelationIdMiddleware |
| 可视化 | 流程可视化编辑器 | Proposed | 已知限制 #2 |
| 安全 | AI 工具速率限制 | Proposed | 已知限制 #4 |
| 运维 | Redis 纳入备份 | Proposed | 已知限制 #6 |
| 运维 | Outbox dispatcher 多实例 | Proposed | 已知限制 #8 |
| 运维 | 组件容器沙箱 | Proposed | 已知限制 #9 |

### 7.2 自动统计数据

> 以下数据由 `scripts/generate-stats.py` 从源码自动生成。
> 运行 `python scripts/generate-stats.py` 获取最新统计。

| 指标 | 值 | 来源 |
|------|-----|------|
| 内置组件数 | 由 generate-stats.py 自动统计 | `packages/components/builtin/` |
| AI 工具数 | 由 generate-stats.py 自动统计 | `packages/ai/tools.py` |
| API 路由数 | 由 generate-stats.py 自动统计 | `apps/api/routers/` |
| 迁移 head | 由 generate-stats.py 自动统计 | `migrations/` |

