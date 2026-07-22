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
