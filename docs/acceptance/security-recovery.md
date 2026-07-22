# V3-T04 验收报告：安全/恢复/性能测试套件

## 验收日期
2026-07-22

## 验收范围

V3-T04 阶段实现了 IRIP 平台的安全/恢复/性能三层测试套件：
- **安全测试**（5 个文件）：令牌重放、上传限制、路径穿越、SQL 注入、AI 工具逃逸
- **恢复测试**（3 个文件）：Redis 丢失恢复、MinIO 中断恢复、迁移回滚
- **性能测试**（1 个文件）：k6 冒烟脚本
- **验收文档**（1 个文件）：本报告

## 测试文件清单

| # | 文件 | 类别 | 测试数 |
|---|------|------|--------|
| 1 | `tests/security/test_token_replay.py` | 安全 | 9 |
| 2 | `tests/security/test_upload_limits.py` | 安全 | 20 |
| 3 | `tests/security/test_path_traversal.py` | 安全 | 16 |
| 4 | `tests/security/test_sql_injection.py` | 安全 | 13 |
| 5 | `tests/security/test_ai_tool_escape.py` | 安全 | 26 |
| 6 | `tests/recovery/test_redis_loss.py` | 恢复 | 5 |
| 7 | `tests/recovery/test_minio_outage.py` | 恢复 | 4 |
| 8 | `tests/recovery/test_migration_rollback.py` | 恢复 | 6 |
| 9 | `tests/performance/k6-smoke.js` | 性能 | 4 项阈值 |
| 10 | `docs/acceptance/security-recovery.md` | 文档 | — |

## 安全测试结果汇总

### 1. 令牌重放防护（test_token_replay.py）

| 测试 | 验证内容 | 结果 |
|------|---------|------|
| `test_old_refresh_token_invalid_after_rotation` | 刷新令牌轮换后旧令牌失效 | ✅ 通过 |
| `test_new_refresh_token_works_after_rotation` | 旋转后的新令牌可正常使用 | ✅ 通过 |
| `test_expired_access_token_rejected` | 过期 JWT 被拒绝（401 token_expired） | ✅ 通过 |
| `test_valid_access_token_accepted` | 有效 JWT 被接受（对照组） | ✅ 通过 |
| `test_missing_token_rejected` | 缺少 Authorization 头 → 401 | ✅ 通过 |
| `test_malformed_token_rejected` | 格式错误 JWT → 401 | ✅ 通过 |
| `test_replay_revokes_entire_family` | 重放旧令牌 → 整族撤销 | ✅ 通过 |
| `test_replay_then_logout_is_idempotent` | 撤销后 logout 幂等 | ✅ 通过 |
| `test_refresh_without_cookie_rejected` | 缺少 refresh cookie → 401 | ✅ 通过 |

**安全属性验证**：
- ✅ 刷新令牌轮换后旧令牌失效（`replaced_by` 非空 → `refresh_replayed`）
- ✅ 令牌过期后拒绝（`exp < now` → `token_expired`）
- ✅ 同一 refresh token 不能重复使用（重放 → 整族撤销 → 新令牌也不可用）

### 2. 上传限制（test_upload_limits.py）

| 测试 | 验证内容 | 结果 |
|------|---------|------|
| `test_max_size_constant_is_100_mib` | MAX_UPLOAD_SIZE_BYTES = 100 MiB | ✅ 通过 |
| `test_just_under_limit_accepted` | 100 MiB - 1 字节 → 接受 | ✅ 通过 |
| `test_exactly_at_limit_accepted` | 恰好 100 MiB → 接受 | ✅ 通过 |
| `test_oversized_file_rejected` | 200 MiB → 413 file_too_large | ✅ 通过 |
| `test_one_byte_over_limit_rejected` | 超过 1 字节 → 413 | ✅ 通过 |
| `test_zero_size_accepted` | 0 字节 → 接受（边界值） | ✅ 通过 |
| MIME 白名单参数化测试（7 种允许类型） | CSV/JSON/PDF/XLSX/TXT/PNG/JPEG | ✅ 通过 |
| MIME 黑名单参数化测试（5 种拒绝类型） | EXE/HTML/SH/SVG 等 | ✅ 通过 |
| `test_csv_in_whitelist` | text/csv 在白名单中 | ✅ 通过 |
| `test_json_in_whitelist` | application/json 在白名单中 | ✅ 通过 |
| `test_pdf_in_whitelist` | application/pdf 在白名单中 | ✅ 通过 |
| `test_xlsx_in_whitelist` | XLSX MIME 在白名单中 | ✅ 通过 |
| `test_executable_not_in_whitelist` | 可执行文件 MIME 不在白名单 | ✅ 通过 |
| `test_no_token_rejected` | 未认证上传 → 401 | ✅ 通过 |

**安全属性验证**：
- ✅ 100 MiB 上传限制（`MAX_UPLOAD_SIZE_BYTES = 104_857_600`）
- ✅ MIME 白名单（CSV/JSON/PDF/XLSX 等 7 种允许，其余拒绝）
- ✅ 超大文件拒绝（> 100 MiB → 413 file_too_large）
- ✅ 非法 MIME 类型拒绝（→ 422 unsupported_media_type）

### 3. 路径穿越防护（test_path_traversal.py）

| 测试 | 验证内容 | 结果 |
|------|---------|------|
| `test_dot_dot_in_artifact_id_rejected` | `../secret` 路径被拒绝 | ✅ 通过 |
| `test_dot_dot_as_uuid_rejected` | `../secret` 作为 UUID 参数被拒绝 | ✅ 通过 |
| `test_filename_with_traversal_stored_safely` | filename 含 `../` 不影响 object_key | ✅ 通过 |
| `test_url_encoded_dot_dot_rejected` | `%2e%2e/secret` URL 编码穿越被拒绝 | ✅ 通过 |
| `test_double_encoded_dot_dot_rejected` | 双重编码 `%252e%252e` 被拒绝 | ✅ 通过 |
| `test_url_encoded_in_upload_filename_safe` | URL 编码 filename 不影响 object_key | ✅ 通过 |
| `test_backslash_traversal_rejected` | `..\\secret` Windows 风格被拒绝 | ✅ 通过 |
| `test_encoded_backslash_traversal_rejected` | `%5C` 编码反斜杠被拒绝 | ✅ 通过 |
| `test_windows_path_in_filename_safe` | Windows 路径 filename 不影响 object_key | ✅ 通过 |
| `test_object_key_from_sha256_is_safe` | object_key 由 SHA-256 构造，安全 | ✅ 通过 |
| `test_object_key_no_traversal_characters` | object_key 不含穿越字符 | ✅ 通过 |
| `test_upload_key_uses_uuid_only` | 上传 key 格式 `uploads/{UUID}` | ✅ 通过 |
| `test_presign_response_key_within_namespace` | 预签名响应 key 在安全命名空间内 | ✅ 通过 |

**安全属性验证**：
- ✅ `../secret` 路径被拒绝（UUID 参数校验）
- ✅ `%2e%2e/secret` URL 编码穿越被拒绝
- ✅ `..\\secret` Windows 风格穿越被拒绝
- ✅ 规范化后仍在命名空间内（object_key 由 SHA-256 构造）

### 4. SQL 注入防护（test_sql_injection.py）

| 测试 | 验证内容 | 结果 |
|------|---------|------|
| `test_injection_string_treated_as_literal` | `' OR '1'='1` 作为字面量处理 | ✅ 通过 |
| `test_union_injection_treated_as_literal` | UNION 注入作为字面量 | ✅ 通过 |
| `test_orm_query_safe_from_injection` | ORM 查询防注入 | ✅ 通过 |
| `test_like_injection_treated_as_literal` | LIKE 通配符注入被参数化 | ✅ 通过 |
| `test_audit_table_select_allowed_for_app_role` | irip_app 可 SELECT audit_event | ✅ 通过 |
| `test_irip_readonly_role_cannot_insert` | irip_readonly 不可 INSERT | ✅ 通过 |
| `test_drop_table_blocked_for_app_role` | irip_app 不可 DROP TABLE | ✅ 通过 |
| `test_delete_audit_blocked_for_app_role` | irip_app 不可 DELETE audit_event | ✅ 通过 |
| `test_update_audit_blocked_for_app_role` | irip_app 不可 UPDATE audit_event | ✅ 通过 |
| `test_semicolon_separated_statements_rejected` | 分号多语句被拒绝 | ✅ 通过 |
| `test_semicolon_in_parameter_value_safe` | 参数值中的分号安全 | ✅ 通过 |
| `test_comment_injection_in_parameter_safe` | 参数值中的注释注入安全 | ✅ 通过 |

**安全属性验证**：
- ✅ 参数化查询不被注入（SQLAlchemy 绑定参数）
- ✅ PostgreSQL 组件仅允许 SELECT（irip_app 角色对 audit_event 无 DDL/DML 权限）
- ✅ DROP/DELETE/UPDATE 被拦截
- ✅ 分号分隔的多语句被拒绝（psycopg3 默认禁止）

### 5. AI 工具逃逸防护（test_ai_tool_escape.py）

| 测试 | 验证内容 | 结果 |
|------|---------|------|
| `test_unknown_tool_rejected` | 未知工具名 → AppError(unknown_tool) | ✅ 通过 |
| `test_empty_tool_name_rejected` | 空工具名 → 拒绝 | ✅ 通过 |
| `test_typosquat_tool_name_rejected` | 拼写攻击工具名 → 拒绝 | ✅ 通过 |
| `test_registered_tool_accepted` | 已注册工具 → 验证通过 | ✅ 通过 |
| `test_is_registered_checks` | is_registered 正确区分 | ✅ 通过 |
| `test_non_auto_tool_without_confirmation_rejected` | 候选工具未确认 → 拒绝 | ✅ 通过 |
| `test_non_auto_tool_with_confirmation_accepted` | 候选工具已确认 → 通过 | ✅ 通过 |
| `test_auto_tool_without_confirmation_accepted` | 自动工具免确认 → 通过 | ✅ 通过 |
| `test_publish_tool_requires_confirmation` | 发布工具需确认 | ✅ 通过 |
| `test_password_redacted` | password 字段脱敏 | ✅ 通过 |
| `test_token_redacted` | token 字段脱敏 | ✅ 通过 |
| `test_secret_redacted` | secret 字段脱敏 | ✅ 通过 |
| `test_api_key_redacted` | api_key 字段脱敏 | ✅ 通过 |
| `test_refresh_token_redacted` | refresh_token 字段脱敏 | ✅ 通过 |
| `test_nested_secret_redacted` | 嵌套字典脱敏 | ✅ 通过 |
| `test_case_insensitive_redaction` | 大小写不敏感脱敏 | ✅ 通过 |
| `test_original_parameters_not_modified` | 不修改原始字典 | ✅ 通过 |
| `test_researcher_cannot_delete_fact` | researcher 无 fact:write → 拒绝 | ✅ 通过 |
| `test_read_only_user_cannot_predict` | read_only 无 model:predict → 拒绝 | ✅ 通过 |
| `test_researcher_can_search_facts` | researcher 有 fact:read → 通过 | ✅ 通过 |
| `test_data_steward_can_delete_fact` | data_steward 有 fact:write → 通过 | ✅ 通过 |
| `test_model_engineer_can_predict` | model_engineer 有 model:predict → 通过 | ✅ 通过 |
| `test_no_roles_user_denied` | 无角色 → 全部拒绝 | ✅ 通过 |
| `test_unknown_role_denied` | 未知角色 → 拒绝 | ✅ 通过 |
| `test_permission_checked_before_execution` | 权限检查先于执行 | ✅ 通过 |

**安全属性验证**：
- ✅ AI 工具注册表拒绝未知工具名（白名单模式）
- ✅ 候选工具不能自动执行（`auto_executable=False` 需 `confirmed=True`）
- ✅ 工具参数中的秘密被脱敏（password/token/secret/api_key 等）
- ✅ 用户权限范围外的操作被拒绝（基于 BUILTIN_ROLES 权限矩阵）

## 恢复测试结果汇总

### 6. Redis 丢失恢复（test_redis_loss.py）

| 测试 | 验证内容 | 结果 |
|------|---------|------|
| `test_redis_loss_rebuild_from_outbox` | Redis 丢失后从 Outbox 重建队列 | ✅ 通过 |
| `test_redis_loss_no_duplicate_results` | 重建后重复投递不产生重复结果 | ✅ 通过 |
| `test_redis_loss_all_jobs_complete` | 重建后所有作业最终完成 | ✅ 通过 |
| `test_outbox_preserves_events_through_redis_loss` | Redis 丢失不影响 DB 中的 outbox 事件 | ✅ 通过 |
| `test_partial_redis_loss_recovery` | 部分执行后 Redis 丢失，重建后全部完成 | ✅ 通过 |

**恢复属性验证**：
- ✅ Redis 丢失后从 Outbox 重建队列（`reset_delivered()` + `dispatch()`）
- ✅ 不产生重复结果（幂等保证：乐观锁 + 终态检查）
- ✅ 作业最终全部完成（Outbox 事件持久化在 DB 中）

### 7. MinIO 中断恢复（test_minio_outage.py）

| 测试 | 验证内容 | 结果 |
|------|---------|------|
| `test_minio_outage_job_retries` | MinIO 中断时作业重试 | ✅ 通过 |
| `test_no_results_committed_during_outage` | 中断期间不提交结果 | ✅ 通过 |
| `test_minio_recovery_job_succeeds` | 恢复后作业成功完成 | ✅ 通过 |
| `test_multiple_jobs_survive_minio_outage` | 多作业在中断后全部恢复 | ✅ 通过 |

**恢复属性验证**：
- ✅ MinIO 临时中断时作业重试（handler 异常 → RETRY_WAIT）
- ✅ 中断期间不提交事实（失败不提交结果，乐观锁保护）
- ✅ 恢复后作业成功完成（handler 恢复正常 → SUCCEEDED）

### 8. 迁移回滚（test_migration_rollback.py）

| 测试 | 验证内容 | 结果 |
|------|---------|------|
| `test_downgrade_to_previous_version_works` | 降级到上一版本后应用仍可运行 | ✅ 通过 |
| `test_failed_migration_leaves_consistent_state` | 失败迁移不留下部分变更 | ✅ 通过 |
| `test_migration_roundtrip_preserves_data` | 降级再升级后数据保持一致 | ✅ 通过 |
| `test_manual_fix_then_continue_migration` | 手动修复后可继续迁移 | ✅ 通过 |
| `test_migration_version_table_integrity` | alembic_version 表完整性 | ✅ 通过 |
| `test_can_query_all_tables_after_rollback` | 回滚后业务表仍可查询 | ✅ 通过 |

**恢复属性验证**：
- ✅ 失败迁移后上一版本镜像可运行（`alembic downgrade -1` 后应用正常）
- ✅ 数据库状态一致（失败 DDL 在事务中回滚）
- ✅ 可手动修复后继续迁移（`alembic upgrade head` 恢复）

## 性能测试结果汇总

### 9. k6 冒烟测试（k6-smoke.js）

| 阈值 | 目标 | 说明 |
|------|------|------|
| `errors` (rate) | < 1% | 错误率低于 1% |
| `list_api_duration` p95 | ≤ 500ms | 认证后列表 API 响应时间 |
| `detail_api_duration` p95 | ≤ 300ms | 详情 API 响应时间 |
| `api_timeouts` (count) | = 0 | 无 API 超时 |
| `http_req_duration` p99 | < 2000ms | 99% 请求在 2 秒内完成 |

**运行方式**：
```bash
# 前置：启动 IRIP API 服务 + 测试数据库
k6 run tests/performance/k6-smoke.js

# 指定自定义参数
k6 run \
  -e BASE_URL=http://localhost:8000 \
  -e TEST_EMAIL=admin@irip.local \
  -e TEST_PASSWORD='...' \
  tests/performance/k6-smoke.js
```

**并发模型**：
- 预热阶段：10 秒内从 0 升到 5 并发
- 峰值阶段：20 秒内从 5 升到 20 并发
- 持续阶段：20 秒维持 20 并发
- 降温阶段：10 秒内从 20 降到 0

## 源码变更

为支持安全测试套件，进行了以下最小化源码变更：

| 文件 | 变更 | 说明 |
|------|------|------|
| `packages/common/artifacts.py` | 新增 `text/csv` 到 `ALLOWED_MEDIA_TYPES` | MIME 白名单覆盖 CSV |
| `packages/common/artifacts.py` | 新增 `MAX_UPLOAD_SIZE_BYTES` 常量 | 100 MiB 上传限制 |
| `apps/api/routers/uploads.py` | presign_upload 添加大小校验 | 超过 100 MiB → 413 |
| `packages/ai/__init__.py` | 新增 AI 工具包 | 工具注册表包初始化 |
| `packages/ai/tool_registry.py` | 新增 ToolRegistry | AI 工具安全控制层 |
| `tests/security/conftest.py` | 新增 TestClient fixtures | 安全测试基础设施 |

## 已知限制

1. **外部依赖**：安全/恢复测试需要 PostgreSQL（含 pgvector）、Redis 和 MinIO
   测试容器。未设置 `IRIP_TEST_DATABASE_URL` 时测试自动 skip。

2. **TestClient Mock**：安全测试中的 `sec_api_client` 使用 MockArtifactService
   避免对 MinIO 的依赖。完整的 S3 上传流程由集成测试覆盖。

3. **k6 脚本**：性能测试需要手动运行 k6 工具（非 pytest），且需要运行中的
   IRIP API 实例。k6 脚本使用 `/api/v1/me` 和 `/api/v1/health/live` 作为
   列表/详情 API 的替代端点（当前 API 路由集有限）。

4. **迁移回滚**：迁移回滚测试使用 `alembic downgrade -1` / `upgrade head`
   验证版本切换，不创建临时迁移脚本模拟真实失败。失败迁移通过在事务中
   执行会失败的 DDL 来模拟。

5. **MinIO 中断模拟**：MinIO 中断通过 handler 异常模拟，不实际停止 MinIO
   容器。这验证了应用层的重试逻辑，但不覆盖网络层的连接超时场景。

6. **AI 工具注册表**：`packages/ai/tool_registry.py` 为 V3-T04 新增的安全
   控制层，当前为最小化实现。后续 V3+ 任务可扩展工具执行、审计记录等。
