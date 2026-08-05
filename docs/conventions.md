# IRIP 编码约定

> 本文件是项目所有编码约定的**唯一权威来源**。新代码必须遵守，旧代码逐步迁移。
> 最后更新：2026-08-04

---

## 1. 语言与命名

| 层 | 规范 | 示例 |
|---|---|---|
| 数据库表/列 | snake_case 单数 | `app_user`、`flow_run`、`department_id` |
| 数据库索引 | `ix_<表>_<列>` | `ix_fact_created_at` |
| 数据库唯一约束 | `uq_<表>_<列>` | `uq_showcase_conv_msg_block` |
| 数据库外键 | `fk_<表>_<被引表>_<列>` | `fk_fact_object_industrial_object` |
| Python 模块 | snake_case | `packages/facts/service.py` |
| Python 类 | PascalCase | `FactService`、`ComponentRegistryService` |
| Python 常量 | UPPER_SNAKE | `PROTECTED_PARAMS`、`MAX_PAGE_SIZE` |
| Python 私有 | `_` 前缀 | `self._dept_id`、`_handle_search_facts()` |
| TypeScript 文件 | kebab-case 或 PascalCase（组件 PascalCase） | `fact-detail.tsx`、`FlowDetail.tsx` |
| TypeScript 变量 | camelCase | `selectedFlowId`、`componentOptions` |
| TypeScript 类型 | PascalCase | `FlowSummary`、`ComponentDetail` |
| API 字段 | snake_case（与 OpenAPI 一致） | `created_at`、`department_id` |
| API URL 路径 | kebab-case | `/api/v1/lab-ops` |

**语言规则：** 代码/API/字段/错误码/事件类型用英文；UI 显示文本用中文。

**前缀约定：** `dept_`（部门相关）、`task_`（任务相关，ORM 类名 `Task*`）。

---

## 2. 数据库

### 2.1 通用规则

- 主键统一 `id UUID PK DEFAULT gen_random_uuid()`
- 时间戳统一 `timestamptz NOT NULL DEFAULT now()`，应用层只允许 `datetime.now(UTC)` 或 `Clock.now()`
- 乐观锁 `lock_version INT NOT NULL DEFAULT 0`（仅可变实体）
- ID 使用 UUIDv7（`packages/common/ids.new_id()`）
- API 输出 RFC 3339（`2026-07-15T08:30:00Z`），Pydantic 序列化器自动处理
- 前端用 `dayjs` 本地化为 `YYYY-MM-DD HH:mm:ss` 显示

### 2.2 不可变表

以下表禁止 UPDATE/DELETE，由 DB 触发器 `raise_immutable_violation` 兜底：
- `fact`（实验数据写入后不可编辑，但 `status` 可变如 archive）
- `audit_event`（仅追加，应用角色 `REVOKE UPDATE, DELETE`）
- `component_version`（发布即不可变，需 GUC 开关才可清理）

### 2.3 多租户列

所有业务表（A/B 类）必须有：
- `department_id UUID NOT NULL` — 归属部门（稳定 FK，不随树调整变化）
- A 类表额外有 `visible_departments JSONB DEFAULT '[]'` — 横向白名单
- A 类面向用户发布的表额外有 `visibility_scope TEXT DEFAULT 'tree'` + `owner_user_id UUID NOT NULL`

### 2.4 迁移

- Alembic 管理，当前 revision 链：`0001_squashed → 0062 → ... → 0073`
- 新迁移按编号递增，`down_revision` 指向上一个
- 大批量删除旧迁移时做 squash（基线用 raw SQL 保留 RLS/角色/触发器）

---

## 3. 后端分层

```
entities.py (ORM) → repository.py (数据访问) → service.py (业务编排) → routers/*.py (API)
```

- 值对象（`@dataclass(frozen=True)`）在服务层与 API 层之间传递，如 `FactRef`、`ShowcaseItemRef`
- Composition Root 依赖注入（`ApplicationContainer`）
- 每模块独立 `packages/<domain>/`，遵循 entities → repository → service 模式

### 3.1 数据访问

- 所有数据库操作走 `session_scope()`（自动 commit / rollback）
- 查询必须通过 `compute_visible_dept_ids()` 做可见性过滤，不得硬编码 `== self._dept_id`
- 写操作强制 `department_id`，解析不到时按敏感度挂 root（公共）或 system（敏感）

### 3.2 异步与事务

- 任何"写业务表 + 触发异步事件"必须同事务插入 `outbox_event`
- Celery 任务命名：`<domain>.<verb>`（如 `jobs.execute`、`outbox.dispatch`、`worker.heartbeat`）
- Worker 心跳间隔 10s，租约 TTL 30s，到期由 `reaper` 重新入队
- Worker 需手动重启（无 `--reload`）；Beat 必须运行否则 job 卡 accepted

### 3.3 鉴权与审计

- 所有 `/api/v1/*`（除 `/auth/login` 与 `/health/*`）必须经过 `CurrentUser` 依赖
- 写操作必须通过 `AuthorizationService.require(user, action, resource)`
- 管理权模型：所有者 + 上级向下（`dept_scope.check_management_permission()`）
- 关键事件必须写 `audit_event`，payload 经 `redact()` 脱敏

### 3.4 错误格式

统一格式：`{error: {code, message, retryable, fields}}`

| HTTP | code | 触发 |
|---|---|---|
| 400 | `invalid_request` / `invalid_cursor` | 参数错、游标错 |
| 401 | `invalid_credentials` / `token_expired` / `refresh_replayed` | 认证失败 |
| 403 | `forbidden` | 授权拒绝 |
| 404 | `not_found` | 资源不存在 |
| 409 | `conflict` / `idempotency_conflict` | 乐观锁/幂等冲突 |
| 422 | `validation_failed` | Pydantic 校验失败 |
| 500 | `internal_error` | 未捕获异常 |
| 503 | `dependency_unavailable` | DB/Redis/MinIO 不可达 |

### 3.5 分页

- 游标格式：base64url JSON `{"v": <稳定排序值>, "id": "<UUID>"}`
- 默认页大小 20，最大 100
- 校验失败抛 `AppError(code="invalid_cursor")`

---

## 4. 前端

### 4.1 技术栈不变

不更换 React、Ant Design、TanStack、Vite。UI 升级允许重组页面内部布局，但不改变路由、API、权限、业务字段或核心操作流程。

### 4.2 状态管理

- access token 仅存 React state（`AuthProvider`），刷新页面 → `/auth/refresh`（HttpOnly Cookie）→ 重新拉 `/me`
- API 客户端 401 时自动 refresh 并重试一次；重试仍 401 → 跳登录页
- 服务端状态用 TanStack Query，本地 UI 状态用 Zustand / React state
- 作业 ID 列表存 `localStorage`

### 4.3 视觉

- 设计语言：潮线 Tideline 水光版（流动/深邃/克制）
- 三条审美红线：不要斜切硬几何、不要大面积实色撞色块、一种签名装饰只用一次
- 深潮蓝（`#0B4A6F`）只用于文字/线条/渐变，不做大面积底色
- 共用视觉组件不直接调用业务 API，不保存服务端实体，不判断业务权限

### 4.4 数据真实性

- 没有时间序列时不绘制趋势线
- 没有全量总数时不把当前页 items 数标成"总数"
- 空值、零值、未知和请求失败必须使用不同表达
- 错误消息优先用 `extractApiError`，不暴露堆栈/token/内部路径

---

## 5. Converter 插件

- 每插件一个文件 `converters/<name>/converter.py` + `__init__.py`
- 输入 `file_path`，输出 `{metadata, points, series}` 三类固定结构
- 新增插件三步：写 converter.py → registry.py 注册 → ai_tool 表插 `category=ingestion`；主系统零改动
- 公共模块：`common/text_extractor.py`（图片走 PaddleOCR）+ `common/llm_utils.py`
- 路由：`packages/plugins/router.py`（后缀映射 + fallback 到 llm_converter）

---

## 6. 测试

- 后端：pytest / pytest-asyncio / testcontainers
- 前端：Vitest + Testing Library / Playwright
- 新增功能必须附带测试，不得用大面积 DOM snapshot 代替行为断言
- 迁移测试：新增迁移需更新 `test_migration_files.py` 中的不可变表列表

---

## 7. Git 与发布

- 提交消息格式：`<type>: <中文描述>`（type = feat / fix / refactor / docs / chore / test / style）
- 版本号在 `pyproject.toml` 和 `apps/web/package.json` 同步更新
- 发布时创建 annotated tag（`git tag -a v0.x -m "..."`）
- release 文档放 `docs/release-YYYY-MM-DD-vX.X.md`
