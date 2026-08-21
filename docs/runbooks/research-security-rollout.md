# 研究模块安全上线与凭据轮换运行手册

> **适用范围**：P0 数据隔离 Task 1–7 完成后的生产上线与凭据轮换。
> **前置条件**：高风险入口开关（`RESEARCH_ANALYSIS_ENABLED`、`LEGACY_MODEL_EXECUTION_ENABLED`）默认关闭；RLS 迁移 `0088_research_domain_rls` 已编写；Workspace 所有权 Guard、身份感知会话、Router 无库访问、主进程不可信模型代码禁止均已合入。
> **执行角色**：运维 SRE + 安全工程师共同执行，每步需双人在岗确认。

---

## 0. 角色与凭据速查

| 维度 | 说明 |
|------|------|
| 受限数据库角色 | `irip_app`（非 superuser，RLS 对其强制生效） |
| 研究分析入口开关 | `RESEARCH_ANALYSIS_ENABLED`（默认 `false`，fail-closed） |
| 遗留模型执行入口开关 | `LEGACY_MODEL_EXECUTION_ENABLED`（默认 `false`，fail-closed） |
| RLS 迁移 | Alembic `0088` — 对全部 `research_%` 表 ENABLE + FORCE RLS |
| 须轮换的凭据 | AI API 密钥、数据库应用密码、MinIO 凭据、JWT 签名密钥（`IRIP_JWT_SECRET`） |
| 须清除的敏感副本 | `/tmp/irip-insight-debug.log` 授权副本、含正文内容的集中化日志 |

---

## 步骤 1：设置入口开关默认关闭

在部署前，将高风险入口开关显式设为关闭，确保 fail-closed 安全默认。

1. 确认环境变量配置（编排清单 / `.env` / 密钥管理系统）中：
   ```
   RESEARCH_ANALYSIS_ENABLED=false
   LEGACY_MODEL_EXECUTION_ENABLED=false
   ```
2. 验证 `packages/common/feature_flags.py` 中两个开关的默认值均为 `false`（代码层面已 fail-closed）。
3. 确认路由守卫：`apps/api/routers/research_timeline.py`（分析端点）与 `apps/api/routers/models.py`（模型执行端点）均调用 `require_feature_enabled()`，关闭时返回 HTTP 503 `feature_disabled`。

**验收**：`/me` 响应中 `feature_flags.research_analysis=false`、`feature_flags.legacy_model_execution=false`。

---

## 步骤 2：快照数据库并清点敏感副本

在代码部署前对数据与残留敏感副本做完整清点，作为回滚基线与清除依据。

1. **数据库快照**：对 `irip` 数据库执行一致性快照（`pg_dump` 或云快照），记录 backup ID 与时间戳。
2. **清点 stdout 副本**：检查容器/进程 stdout 中是否存在历史输出的研究正文（应已被 Task 2 脱敏处理）。记录保留位置。
3. **清点文件副本**：检索文件系统中的授权副本，重点：
   - `/tmp/irip-insight-debug.log`（含正文内容的调试日志，须在步骤 7 清除）
4. **清点日志平台副本**：在集中化日志平台中检索含研究正文的日志条目，记录索引与保留窗口。

**输出**：一份《敏感副本清点表》，含位置、保留窗口、处置方式（清除/降级为事件元数据）。

---

## 步骤 3：部署代码但不启用分析

以"分析关闭"状态上线新代码，确保只读链路正常后再分阶段开启高风险功能。

1. 构建并部署 API、Worker、Web 镜像（含 P0 Task 1–7 全部改动）。
2. 以 `RESEARCH_ANALYSIS_ENABLED=false`、`LEGACY_MODEL_EXECUTION_ENABLED=false` 启动。
3. 健康检查：`/health` 返回 200，`/me` 响应中两个高风险开关均为 `false`。
4. 验证只读链路：研究历史页面（timeline、turn detail）可正常访问；分析端点返回 503。

**验收**：应用以 fail-closed 状态正常运行，无高风险端点可被调用。

---

## 步骤 4：运行 Alembic 0088 并清点 RLS 状态

执行 RLS 迁移并验证全部 `research_%` 表已 ENABLE + FORCE 行级安全。

1. **执行迁移**：
   ```bash
   uv run alembic upgrade head
   ```
   确认 `0088_research_domain_rls` 成功应用（`research_lineage_edge.workspace_id` 列已添加并回填、NOT NULL、FK 已建立）。
2. **清点 RLS 状态**：对全部 32 张 `research_%` 表执行：
   ```sql
   SELECT relname, relrowsecurity, relforcerowsecurity
   FROM pg_class
   WHERE relname LIKE 'research_%' AND relkind = 'r'
   ORDER BY relname;
   ```
   预期：`relrowsecurity=true` 且 `relforcerowsecurity=true`（所有表 ENABLE + FORCE）。
3. **清点策略**：
   ```sql
   SELECT tablename, policyname, cmd, qual IS NOT NULL AS has_using, with_check IS NOT NULL AS has_check
   FROM pg_policies
   WHERE tablename LIKE 'research_%'
   ORDER BY tablename;
   ```
   预期：每张表有 `research_workspace_isolation` 策略，USING 与 WITH CHECK 均非空。
4. **降级验证**：确认 `0088.downgrade()` 可安全回退（DROP POLICY + DISABLE RLS + DROP lineage workspace_id 列）。

**验收**：32 张研究表全部 ENABLE + FORCE RLS，策略谓词均含 `owner_user_id` 与 `current_visible_dept_ids()`。

---

## 步骤 5：用 irip_app 角色执行双用户双部门 API 测试

以受限数据库角色（`irip_app`，非 superuser）验证 RLS 在真实访问路径下生效。

1. 确认 `irip_app` 角色非 superuser（`SELECT rolsuper FROM pg_roles WHERE rolname='irip_app'` 应为 `false`），RLS 对其强制生效。
2. 准备双用户双部门测试数据：
   - 用户 A（部门 D1）、用户 B（部门 D2），各自创建研究 Workspace。
   - 设置 GUC `app.current_user_id` 与 `app.current_dept_id`，`SET ROLE irip_app`。
3. 执行 API 级隔离测试（对应 `tests/security/test_research_timeline_rls.py`）：
   - 用户 A 无法读取用户 B 的 Workspace 及其下属表（turn、insight、conclusion、result 等）。
   - 跨部门访问被拦截。
   - GUC 缺失时 fail-closed（返回空集，非报错泄露）。
4. 执行单元 RLS 行为测试（对应 `tests/unit/test_rls_multitenant.py`）：
   ```bash
   IRIP_ENV=test \
   IRIP_TEST_DATABASE_URL="postgresql+psycopg://irip:irip_dev_password@localhost:5432/irip" \
   uv run pytest tests/unit/test_rls_multitenant.py tests/security/test_research_timeline_rls.py -q
   ```

**验收**：双用户双部门场景下零跨租户泄露；fail-closed 在 GUC 缺失时生效。

---

## 步骤 6：轮换 AI / 数据库 / MinIO / JWT 凭据

上线后轮换全部凭据，使旧密钥失效，杜绝凭据泄露窗口。

按以下顺序轮换，每项轮换后立即验证对应链路：

1. **AI API 密钥**
   - 在 AI 供应商控制台重新生成 API key，吊销旧 key。
   - 更新数据库 `ai_model_config.api_key`（经 `IRIP_MASTER_KEY` 信封加密写入），或更新对应密钥管理配置。
   - 验证：分析链路（仅在步骤 8 启用后）使用新 key 可正常调用。
2. **数据库应用密码**（`IRIP_DATABASE_PASSWORD`）
   - 在 PostgreSQL 中 `ALTER ROLE irip_app PASSWORD '<new>'`。
   - 更新编排系统中的 `IRIP_DATABASE_PASSWORD`（不可使用 `irip_dev_password` 等开发默认值，见 `security_check.py` WEAK_SECRETS）。
   - 滚动重启 API/Worker，确认连接池使用新密码成功建连。
3. **MinIO 凭据**（`IRIP_MINIO_ACCESS_KEY` / `IRIP_MINIO_SECRET_KEY`）
   - 在 MinIO 中创建新 access key，吊销旧 key。
   - 更新 `IRIP_MINIO_ACCESS_KEY`、`IRIP_MINIO_SECRET_KEY`（不可使用 `irip_dev_password`）。
   - 验证：研究 artifact 上传/下载链路使用新凭据正常。
4. **JWT 签名材料**（`IRIP_JWT_SECRET`）
   - 生成 >= 32 字节的高熵随机密钥，替换 `IRIP_JWT_SECRET`。
   - 不可使用 `dev_only_insecure_jwt_secret_change_me_0123456789abcdef` 等弱密钥。
   - 滚动重启服务；轮换后所有已签发 JWT 立即失效，用户需重新登录。
   - 验证：`assert_production_keys()`（`packages/common/security_check.py`）在生产环境通过。

**验收**：`assert_production_keys()` 无错误；四类凭据全部更新；旧凭据已吊销。

---

## 步骤 7：按保留策略清除敏感日志副本

清除含研究正文的授权副本与集中化日志，保留事件元数据以满足审计与保留策略。

1. **清除 `/tmp/irip-insight-debug.log` 授权副本**
   - 定位所有保留的授权副本（步骤 2 清点表中记录的位置）。
   - 安全删除文件内容（`shred -u` 或等价安全擦除）。
   - 确认源码中不再写入该路径（P0 Task 2 已移除敏感正文日志写入）。
2. **降级集中化日志**
   - 对集中化日志平台中含研究正文的条目：删除正文 `message`/payload 字段，仅保留事件元数据（时间戳、级别、事件类型、trace_id、用户 ID 哈希等）。
   - 保留策略：事件元数据按既有日志保留窗口留存，正文内容立即清除。
3. **验证无残留**：
   ```bash
   grep -rn "irip-insight-debug" apps packages 2>/dev/null | grep -v __pycache__
   ```
   预期：无引用该路径的写入逻辑。

**验收**：敏感正文副本已清除；事件元数据按保留策略留存；源码无正文日志写入残留。

---

## 步骤 8：Program Gate 1 通过后启用研究分析

在所有前置安全验证通过后，分阶段启用研究分析入口。

**Program Gate 1 准入条件**（全部满足才可启用）：
- 步骤 4：32 张研究表全部 ENABLE + FORCE RLS，策略齐全。
- 步骤 5：双用户双部门 API 隔离测试零跨租户泄露。
- 步骤 6：四类凭据全部轮换，`assert_production_keys()` 通过。
- 步骤 7：敏感日志副本已清除，仅保留事件元数据。

**启用流程**：
1. 在编排系统中设置 `RESEARCH_ANALYSIS_ENABLED=true`。
2. 滚动重启 API（环境变量在进程启动时读取一次，需重启生效）。
3. 验证 `/me` 响应中 `feature_flags.research_analysis=true`。
4. 执行分析端点冒烟测试：`POST /workspaces/{id}/turns/{turn_id}/analyze` 返回正常分析结果。
5. 监控首小时：关注 RLS 拦截告警、分析失败率、凭据连接错误。

**验收**：研究分析链路在 RLS 保护下正常运行，无跨租户访问。

---

## 步骤 9：永久保持遗留模型执行关闭

`LEGACY_MODEL_EXECUTION_ENABLED` 永久保持 `false`，除非隔离运行时获得单独安全审批。

1. **默认策略**：`LEGACY_MODEL_EXECUTION_ENABLED=false` 作为生产常态，不得在编排模板中以 `true` 为默认值。
2. **路由守卫**：`apps/api/routers/models.py` 中 `POST /api/v1/models/{model_id}/predict` 始终受 `require_feature_enabled(LEGACY_MODEL_EXECUTION_ENABLED, "legacy_model_execution")` 守卫，关闭时返回 503。
3. **隔离运行时例外**：如需执行不可信模型代码，必须：
   - 在独立隔离运行时（非主进程）中执行（P0 Task 7 已禁止主进程执行）。
   - 获得单独安全审批（书面记录，含风险评估、隔离边界、回退方案）。
   - 审批通过后方可临时设置 `LEGACY_MODEL_EXECUTION_ENABLED=true`，并在使用后立即恢复 `false`。
4. **审计**：对 `LEGACY_MODEL_EXECUTION_ENABLED` 的任何变更须记录到 `audit_event`，含审批编号与操作人。

**验收**：生产环境 `LEGACY_MODEL_EXECUTION_ENABLED=false`；启用需隔离运行时 + 单独审批，且全程可审计。

---

## 附录 A：回滚

- **代码回滚**：回退应用镜像至上一版本，`RESEARCH_ANALYSIS_ENABLED` 与 `LEGACY_MODEL_EXECUTION_ENABLED` 保持 `false`。
- **迁移回滚**：`uv run alembic downgrade 0087` 执行 `0088.downgrade()`（DROP POLICY + DISABLE RLS + DROP lineage workspace_id 列）。注意：回滚后 RLS 不再生效，须在评估安全影响后方可执行。
- **数据库恢复**：必要时使用步骤 2 的快照恢复（影响同时段其他域，需重大变更审批）。
- **凭据**：轮换后不回退至旧凭据；如轮换导致故障，重新生成新凭据而非复用旧值。

## 附录 B：安全回归测试命令

```bash
# Lint
uv run ruff check apps packages tests

# 类型检查
uv run mypy packages apps/api apps/worker

# P0 完整测试集（unit + contract + security + research integration）
IRIP_ENV=test \
IRIP_TEST_DATABASE_URL="postgresql+psycopg://irip:irip_dev_password@localhost:5432/irip" \
uv run pytest tests/unit tests/contract tests/security tests/integration/research -q
```

## 附录 C：危险模式验证

确认以下危险模式已从 `apps`、`packages` 源码中消除（无匹配）：

```bash
grep -rn "pickle.loads\|joblib.load\|admin@irip.local\|IRIP_ALEMBIC_DATABASE_URL\|irip-insight-debug\|PAYLOAD msg" apps packages 2>/dev/null \
  | grep -v __pycache__ | grep -v node_modules
```

预期：无输出（全部危险模式已消除）。
