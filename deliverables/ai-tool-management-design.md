# AI 工具管理 - 系统架构设计

> **文档版本**：v1.0
> **创建日期**：2026-07-28
> **架构师**：高见远（Gao）
> **项目**：IRIP 平台
> **模块**：AI 助手 - 工具管理
> **依据**：`deliverables/ai-tool-management-prd.md`（PRD v1.0，产品经理许清楚）
> **上游决策**：Q-1 / Q-2 / Q-3 已由用户确认（见 §1.2）

---

## 1. 实现方案 + 框架选型

### 1.1 核心思路

将 AI 工具的"声明层"（name / display_name / description / required_permission / candidate / parameters_schema）从 `packages/ai/tools.py` 的硬编码元组迁移到数据库 `ai_tool` 表，运行时由 `ToolRegistry` 从 DB 加载；执行逻辑（`AIService._execute_tool` 的 if-elif 分派）保持硬编码不变。

**三条已确认决策的落地方式**：

| 决策 | 落地方式 |
|---|---|
| **Q-1** 仅管理声明层 | 新建工具若 `_execute_tool` 无对应 handler，走 `else` 分支返回 `{"summary": "未实现的工具: xxx", "data": {"error": "Tool not implemented: xxx"}}`（现有行为，不改）。UI 抽屉显著提示"仅创建声明层"。 |
| **Q-2** 每次 ask 查 DB reload | `AIService.ask` 入口处调用 `await self._tool_registry.reload_from_db(session)`，无缓存、无 TTL。工具表预计 < 50 行，单行 SELECT 开销 < 1ms，可忽略。 |
| **Q-3** 保留硬编码元组作种子 | `WHITELIST_TOOLS` / `CANDIDATE_TOOLS` 元组**保留不动**，新增 `seed_tools_if_empty(session)` 函数，应用启动 lifespan 中调用：表空时写入 12 条种子数据，与 `ALL_TOOLS` 一致。 |

### 1.2 框架与复用

| 层 | 选型 | 复用现有 |
|---|---|---|
| 数据库 | PostgreSQL + SQLAlchemy 2.x async | `packages/common/database.py`（`Base` / `session_scope`）、`packages/common/db_types.py`（`GUID` / `UTCDateTime`） |
| 迁移 | Alembic | `migrations/versions/` 已有 0037，新增 0038 |
| 后端 API | FastAPI + Pydantic | `apps/api/routers/ai_config.py` 的内联 Table + Pydantic 模式 |
| 权限守卫 | `require_permission("system:manage")` | `apps/api/dependencies/authorization.py`；`system:manage` 仅授予 `platform_administrator`（见 `packages/auth/permissions.py:119,178`） |
| 审计 | `packages/audit/repository.py` 的 `AuditRecorder.record` | T-07 |
| 前端 | React + antd + @tanstack/react-query | `AIConfigPage.tsx` 的 useQuery/useMutation 模式 |
| 前端路由 | @tanstack/react-router | `PlatformPage.tsx` 的 `?tab=` 模式 |

### 1.3 关键设计决策

**D-1 权限**：PRD 写 `require_permission("platform:admin")`，但现有权限矩阵无此 action。`system:manage` 是唯一仅授予 `platform_administrator` 的权限，语义匹配（平台级运维管理）。v1 直接用 `system:manage`，不新增权限常量，避免改动 `BUILTIN_ROLES` 矩阵。

**D-2 乐观锁**：PRD §5.4 提及"保存冲突"提示，`ai_tool` 表加 `lock_version` 列（与 `equipment` / `department` 一致），`PATCH` 时校验 `WHERE lock_version = :expected`，冲突返回 409。

**D-3 禁用语义**：禁用的工具在 `ToolRegistry` 内部仍保留 `ToolSpec`（供管理 API 列出），但 `names()` / `list_enabled_tools()` / `validate()` / `_build_tool_schemas` 均过滤掉。`validate()` 对禁用工具抛 `unknown_tool`（与未知工具同处理），实现 T-03"禁用后 AI 不可见、不可调用"。

**D-4 热更新无显式刷新 API**：因每次 ask 都 reload，T-09 的"生效状态"由 `updated_at` 列 + toast 提示体现，不提供 `refresh-status` 端点（PRD 标注"可选"）。

**D-5 不支持删除**：PRD 未提工具删除需求，`ai_tool` 表无 delete 端点。禁用即可达到"下线"效果。删除会破坏历史对话中 `tool_calls_json` 的可追溯性。

**D-6 全局工具**：`ai_tool` 表不含 `organization_id`（Q-8 v1 全局），RLS 策略不隔离此表。

---

## 2. 文件列表及相对路径

### 2.1 新增文件

| 路径 | 说明 |
|---|---|
| `migrations/versions/0038_ai_tool.py` | 新建 `ai_tool` 表 + 索引 + 权限授予 |
| `packages/ai/tool_repository.py` | `ToolRepository`：`ai_tool` 表的 async CRUD |
| `packages/ai/tool_seeding.py` | `seed_tools_if_empty(session)`：表空时写入 12 条种子 |
| `apps/api/routers/ai_tools.py` | AI 工具管理 REST 端点（5 个） |
| `apps/web/src/ai_tools/AIToolsPage.tsx` | 工具列表页（筛选/搜索/启停开关） |
| `apps/web/src/ai_tools/ToolEditDrawer.tsx` | 编辑/新建抽屉（含 JSON Schema 编辑器） |
| `apps/web/src/ai_tools/types.ts` | 前端类型定义（ToolDTO 等） |

### 2.2 修改文件

| 路径 | 修改点 |
|---|---|
| `packages/ai/tools.py` | `ToolRegistry` 新增 `reload_from_db()` / `list_enabled_tools()` / `enabled_names()`；`validate()` 对禁用工具抛 `unknown_tool`；保留原元组作种子源 |
| `packages/ai/service.py` | `ask()` 入口加 `reload_from_db`；`_build_tool_schemas` 改用 `list_enabled_tools`；`names()` 改用 `enabled_names()`；`__init__` 的 `tool_registry` 注入不变 |
| `apps/api/main.py` | lifespan 中调用 `seed_tools_if_empty`；`include_router(ai_tools_router)` |
| `apps/web/src/api/client.ts` | 追加 AI 工具相关类型与 API 函数（`apiListAITools` / `apiUpdateAITool` / `apiToggleAITool` / `apiCreateAITool`） |
| `apps/web/src/pages/PlatformPage.tsx` | `VALID_TABS` 加 `'ai-tools'`；条件渲染该 Tab（仅 `platform_administrator`） |

### 2.3 不改动文件

- `packages/ai/tools.py` 中的 `WHITELIST_TOOLS` / `CANDIDATE_TOOLS` / `ALL_TOOLS` 元组（保留作种子源）
- `AIService._execute_tool` 的 if-elif 分派（Q-1 约束）
- `packages/auth/permissions.py`（不新增权限常量）

---

## 3. 数据结构和接口（类图）

### 3.1 数据库表 `ai_tool`

```sql
CREATE TABLE ai_tool (
    id                 UUID         PRIMARY KEY,
    name               TEXT         NOT NULL UNIQUE,          -- 工具唯一键，创建后不可改
    display_name       TEXT         NOT NULL,
    description        TEXT         NOT NULL,
    required_permission TEXT        NOT NULL,                 -- 如 "standard:read"
    candidate          BOOLEAN      NOT NULL DEFAULT false,    -- true=候选(需审批) false=只读
    parameters_schema  JSONB        NOT NULL DEFAULT '{}'::jsonb,
    enabled            BOOLEAN      NOT NULL DEFAULT true,
    lock_version       INTEGER      NOT NULL DEFAULT 0,       -- 乐观锁
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_by         UUID         NULL                       -- 最后修改人
);
CREATE INDEX ix_ai_tool_name ON ai_tool (name);                -- name 已有 UNIQUE 隐式索引，此处不重复
-- RLS：ai_tool 为全局表，不启用 tenant_isolation 策略
-- 权限：GRANT SELECT/INSERT/UPDATE ON ai_tool TO irip_app（不授予 DELETE）
```

### 3.2 类图

```
┌──────────────────────────────┐         ┌──────────────────────────────┐
│         ToolSpec (不变)       │         │       AIToolRow (新增)        │
│   packages/ai/tools.py       │         │   packages/ai/tool_repository │
├──────────────────────────────┤         ├──────────────────────────────┤
│ name: str                    │ ◀────── │ id: UUID                      │
│ display_name: str            │  映射   │ name: str                     │
│ description: str             │         │ display_name: str             │
│ required_permission: str     │         │ description: str              │
│ candidate: bool              │         │ required_permission: str      │
│ parameters_schema: dict       │         │ candidate: bool               │
│                              │         │ parameters_schema: dict        │
│ @dataclass(frozen=True)       │         │ enabled: bool                 │
└──────────────────────────────┘         │ lock_version: int             │
          ▲                               │ created_at / updated_at       │
          │ 组合                            │ updated_by: UUID | None       │
          │                                 └──────────────────────────────┘
┌──────────────────────────────┐                    ▲
│      ToolRegistry (改造)      │                    │ 持有
│   packages/ai/tools.py       │                    │
├──────────────────────────────┤   reload_from_db   │
│ _tools: dict[str, ToolSpec]  │ ◀─────────────────┤
│ _enabled: set[str]           │                    │
├──────────────────────────────┤                    │
│ register(spec)               │                    │
│ get(name) / validate(name)   │   ┌──────────────────────────────┐
│ is_candidate(name)           │   │    ToolRepository (新增)      │
│ list_tools()                 │   │  packages/ai/tool_repository │
│ list_enabled_tools() ★新增    │   ├──────────────────────────────┤
│ enabled_names() ★新增        │   │ list_all() -> list[AIToolRow] │
│ names()  (改为仅 enabled)    │   │ get_by_name(name)             │
│ reload_from_db(session) ★新增│   │ create(data) -> AIToolRow     │
│ to_definitions()             │   │ update(name, data, lock_ver)  │
│ validate_invocation(inv)     │   │ set_enabled(name, en, lock_v) │
└──────────────────────────────┘   └──────────────────────────────┘
          │                                   │
          │ 注入                                │ 使用
          ▼                                   ▼
┌──────────────────────────────┐   ┌──────────────────────────────┐
│       AIService (改造)        │   │   ai_tools_router (新增)      │
│   packages/ai/service.py     │   │  apps/api/routers/ai_tools.py│
├──────────────────────────────┤   ├──────────────────────────────┤
│ ask(...)                      │   │ GET    /api/v1/ai-tools       │
│  └ reload_from_db(session) ★  │   │ GET    /api/v1/ai-tools/{nm} │
│  └ tool_names = enabled_names│   │ POST   /api/v1/ai-tools       │
│  └ tool_schemas (仅 enabled) │   │ PATCH  /api/v1/ai-tools/{nm} │
│ _build_tool_schemas() (改)    │   │ PATCH  /ai-tools/{nm}/enabled│
│ _execute_tool() (不变)        │   └──────────────────────────────┘
└──────────────────────────────┘                │
                                                │ 调用
                                                ▼
                                   ┌──────────────────────────────┐
                                   │     前端 AIToolsPage          │
                                   │  apps/web/src/ai_tools/      │
                                   ├──────────────────────────────┤
                                   │ useQuery(['ai-tools'])       │
                                   │ useMutation(apiUpdateAITool) │
                                   │ useMutation(apiToggleAITool) │
                                   │  -> ToolEditDrawer           │
                                   └──────────────────────────────┘
```

### 3.3 后端 Pydantic 模型

```python
class AIToolDTO(BaseModel):
    """工具 DTO（列表 + 详情共用）。"""
    name: str
    display_name: str
    description: str
    required_permission: str
    candidate: bool
    parameters_schema: dict[str, Any]
    enabled: bool
    lock_version: int
    updated_at: str
    updated_by: str | None = None

class AIToolCreateRequest(BaseModel):
    name: str = Field(..., pattern=r"^[a-z][a-z0-9_]*$", max_length=64)
    display_name: str = Field(..., max_length=128)
    description: str = Field(..., max_length=2000)
    required_permission: str = Field(..., max_length=64)
    candidate: bool = False
    parameters_schema: dict[str, Any] = Field(default_factory=dict)

class AIToolUpdateRequest(BaseModel):
    display_name: str = Field(..., max_length=128)
    description: str = Field(..., max_length=2000)
    required_permission: str = Field(..., max_length=64)
    candidate: bool
    parameters_schema: dict[str, Any]
    lock_version: int

class AIToolToggleRequest(BaseModel):
    enabled: bool
    lock_version: int
```

---

## 4. 程序调用流程（时序图）

### 4.1 热更新生效流程（AI 问答触发 reload）

```
用户        前端        API路由        AIService.ask        ToolRegistry        ToolRepository        ai_tool表
 │           │            │                │                    │                    │                 │
 │─提问──────▶│            │                │                    │                    │                 │
 │           │─POST /ask──▶│                │                    │                    │                 │
 │           │            │─ask(user,q)────▶│                    │                    │                 │
 │           │            │                │─reload_from_db(s)──▶│                    │                 │
 │           │            │                │                    │─list_all()────────▶│                 │
 │           │            │                │                    │                    │─SELECT *──────▶│
 │           │            │                │                    │                    │◀──rows──────────│
 │           │            │                │                    │◀─[AIToolRow,...]───│                 │
 │           │            │                │                    │ 重建 _tools        │                 │
 │           │            │                │                    │ + _enabled 集合     │                 │
 │           │            │                │◀───────────────────│                    │                 │
 │           │            │                │ enabled_names()    │                    │                 │
 │           │            │                │─▶ [仅 enabled 工具名]│                    │                 │
 │           │            │                │ _build_tool_schemas│                    │                 │
 │           │            │                │─▶ [仅 enabled schema]                    │                 │
 │           │            │                │ provider.complete(req)                   │                 │
 │           │            │                │ ▼ tool_calls                              │                 │
 │           │            │                │ validate(tool_name)                      │                 │
 │           │            │                │─▶ 禁用工具抛 unknown_tool │              │                 │
 │           │            │                │   启用工具放行        │                    │                 │
 │           │            │                │ _execute_tool (if-elif, 不变)            │                 │
 │           │            │                │   else → "未实现"                         │                 │
 │           │            │◀─AIResponse───│                    │                    │                 │
 │           │◀─answer────│            │                    │                    │                 │
 │◀─answer──│            │                │                    │                    │                 │
```

### 4.2 工具编辑保存流程（含乐观锁 + 审计）

```
管理员       前端            ai_tools_router       ToolRepository       ai_tool表      AuditRecorder
 │           │                  │                      │                  │              │
 │─编辑保存──▶│                  │                      │                  │              │
 │           │─PATCH /tools/{nm}│                      │                  │              │
 │           │  body+lock_version│                     │                  │              │
 │           │                  │权限校验 system:manage│                  │              │
 │           │                  │─update(name,data,lock_ver)─────────────▶│              │
 │           │                  │                      │ UPDATE ... WHERE  │              │
 │           │                  │                      │  name=? AND       │              │
 │           │                  │                      │  lock_version=? ─▶│              │
 │           │                  │                      │◀──rowcount────────│              │
 │           │                  │                      │  rowcount=0 → 409  │              │
 │           │                  │                      │  rowcount=1 → ok  │              │
 │           │                  │◀─AIToolRow──────────│                  │              │
 │           │                  │─AuditRecorder.record(...)──────────────────────────────▶│
 │           │                  │  action="ai_tool.update"                              │
 │           │                  │  payload={before, after, diff}                       │
 │           │                  │◀────────────────────────────────────────────────────│
 │           │◀─200 + DTO──────│                      │                  │              │
 │           │  toast"已生效"   │                      │                  │              │
 │◀─提示────│                  │                      │                  │              │
```

### 4.3 启用/禁用流程（带二次确认）

```
管理员    前端                 API                 ToolRepository
 │        │                     │                     │
 │─点开关──▶│                     │                     │
 │        │ Modal.confirm        │                     │
 │─确认───▶│                     │                     │
 │        │─PATCH /tools/{nm}/enabled│                 │
 │        │  {enabled:false, lock_version}│           │
 │        │                     │权限校验             │
 │        │                     │─set_enabled(...)───▶│
 │        │                     │  UPDATE enabled    │
 │        │                     │  + lock_version    │
 │        │                     │◀─row──────────────│
 │        │                     │─audit record──────▶│
 │        │◀─200 + 新状态──────│                     │
 │        │ invalidateQueries   │                     │
 │        │ 列表刷新            │                     │
 │◀─toast─│                     │                     │

  下一次 ask() → reload_from_db → 该工具不在 enabled_names → AI 不可见
```

---

## 5. 任务列表（有序、含依赖关系、按实现顺序排列）

> 命名规则：`[PRD需求号].[阶段]` 阶段分为 **DB / Repo / Reg / Svc / API / Web**。
> 依赖标记：`← T-xx` 表示依赖该任务先完成。

| 序号 | 任务 | PRD | 阶段 | 依赖 | 工作量 | 说明 |
|---|---|---|---|---|---|---|
| **1** | 编写 migration `0038_ai_tool.py`：建表 + 索引 + GRANT | T-01 | DB | — | S | 含 `ai_tool` 表、`name` UNIQUE、不授予 DELETE |
| **2** | 实现 `packages/ai/tool_repository.py` 的 `ToolRepository` | T-01 | Repo | ← 1 | M | `list_all / get_by_name / create / update / set_enabled`，返回 `AIToolRow` dataclass |
| **3** | 实现 `packages/ai/tool_seeding.py` 的 `seed_tools_if_empty` | T-01 | Repo | ← 2 | S | 读 `ALL_TOOLS` 元组，表空时批量 INSERT，幂等 |
| **4** | 改造 `packages/ai/tools.py` 的 `ToolRegistry` | T-01/T-03 | Reg | ← 2 | M | 新增 `reload_from_db` / `list_enabled_tools` / `enabled_names`；`validate()` 对禁用工具抛 `unknown_tool`；`names()` 改为仅返回 enabled |
| **5** | 改造 `packages/ai/service.py` 的 `AIService.ask` | T-01 | Svc | ← 4 | S | `ask` 入口加 `await self._tool_registry.reload_from_db(session)`；`_build_tool_schemas` 改用 `list_enabled_tools` |
| **6** | 新建 `apps/api/routers/ai_tools.py`（5 端点） | T-02/T-04/T-05/T-06 | API | ← 2,4 | L | `GET 列表` / `GET 详情` / `POST 新建` / `PATCH 编辑` / `PATCH 启停`；全部 `require_permission("system:manage")`；PATCH/POST 含乐观锁校验和 `name` 格式校验 |
| **7** | 注册路由 + 启动种子：`apps/api/main.py` | T-01 | API | ← 3,6 | S | `include_router(ai_tools_router)`；lifespan 中 `await seed_tools_if_empty(session)` |
| **8** | 审计集成：在 `ai_tools.py` 的写端点调 `AuditRecorder.record` | T-07 | API | ← 6 | S | action=`ai_tool.create/update/toggle`，payload 含 before/after diff |
| **9** | 前端类型 + API 函数：`apps/web/src/api/client.ts` | T-02 | Web | ← 6 | S | 追加 `apiListAITools / apiGetAITool / apiCreateAITool / apiUpdateAITool / apiToggleAITool` + `AIToolDTO` 类型 |
| **10** | 新建 `apps/web/src/ai_tools/types.ts` | T-02 | Web | — | XS | `AIToolDTO` / `ToolFilter` / `ToolFormValues` 类型（也可并入 client.ts，按团队习惯） |
| **11** | 新建 `apps/web/src/ai_tools/AIToolsPage.tsx`（列表 + 筛选 + 搜索） | T-02/T-03 | Web | ← 9,10 | L | antd Table；筛选：类型/状态；搜索：name/display_name；启用开关列 + 二次确认 Modal；编辑按钮打开抽屉 |
| **12** | 新建 `apps/web/src/ai_tools/ToolEditDrawer.tsx`（编辑/新建抽屉） | T-04/T-06/T-08 | Web | ← 11 | L | antd Drawer 600px；Form；JSON 文本框（monospace + JSON.parse 实时校验）；新建模式 name 可填 + 黄色 Alert；编辑模式 name 只读；保存调对应 API + 成效 toast |
| **13** | `PlatformPage.tsx` 加 `'ai-tools'` Tab + 角色条件渲染 | T-05 | Web | ← 11 | S | `VALID_TABS` 加项；`items` 中根据 `currentUser.roles.includes('platform_administrator')` 决定是否渲染该 Tab |
| **14** | 验收测试（手测 + 现有测试不回归） | 全部 | QA | ← 1-13 | M | 跑迁移、启动 API、UI 列表/编辑/启停、AI 对话验证热更新生效 |

**关键依赖链**：`1 → 2 → 4 → 5`（后端热更新主线）；`2 → 3 → 7`（种子）；`6 → 9 → 11 → 12 → 13`（前端主线）；`6 → 8`（审计）。两条主线在任务 6 汇合（API 契约），可并行开发后端 1-5 与前端 9-13（前端先用 mock 契约）。

---

## 6. 依赖包列表

### 6.1 后端（Python）

| 包 | 用途 | 是否新增 |
|---|---|---|
| `sqlalchemy` 2.x | ORM / async session | 否（现有） |
| `alembic` | 迁移 | 否（现有） |
| `pydantic` | 请求/响应模型 | 否（现有） |
| `fastapi` | 路由 | 否（现有） |
| `packages.audit`（内部） | 审计记录 | 否（现有） |
| `jsonschema`（可选 P1） | 后端二次校验 parameters_schema 是合法 JSON Schema | **T-08 可选**，v1 P0 不强求 |

> v1 P0 后端不引入新依赖。JSON Schema 合法性校验在前端用 `JSON.parse` 即可；P1 如需严格校验再引入 `jsonschema`。

### 6.2 前端（TypeScript）

| 包 | 用途 | 是否新增 |
|---|---|---|
| `antd` | UI 组件 | 否（现有） |
| `@tanstack/react-query` | 数据获取 | 否（现有） |
| `@tanstack/react-router` | 路由 | 否（现有） |
| `axios` | HTTP | 否（现有） |
| `ajv`（可选 P1） | 前端 JSON Schema 合法性校验（T-08） | **P1 新增**，v1 P0 用 `JSON.parse` |

> v1 P0 前端不引入新依赖。P1 实现严格的 JSON Schema 校验时再装 `ajv`。

---

## 7. 共享知识（跨文件约定）

### 7.1 命名约定

- **工具 `name`**：正则 `^[a-z][a-z0-9_]*$`，最长 64 字符，创建后不可改（Q-5）。
- **数据库表名**：`ai_tool`（单数，与 `ai_conversation` / `ai_message` 一致）。
- **路由前缀**：`/api/v1/ai-tools`（kebab-case，与 `/api/v1/ai-config` 一致）。
- **前端目录**：`apps/web/src/ai_tools/`（snake_case，与 `governance/` 等同级）。
- **审计 action**：`ai_tool.create` / `ai_tool.update` / `ai_tool.toggle`。

### 7.2 权限约定

- 所有管理端点统一用 `require_permission("system:manage")`，依赖 `BUILTIN_ROLES` 中 `platform_administrator` 拥有此权限的事实。
- 前端 Tab 条件渲染判断：`currentUser.roles.includes('platform_administrator')`。
- 直接访问 URL 时 API 返回 403，前端显示"无权限"占位（复用现有 403 处理）。

### 7.3 乐观锁约定

- `ai_tool.lock_version` 初始 0，每次 UPDATE 时 `WHERE lock_version = :expected` 并 `SET lock_version = lock_version + 1`。
- `rowcount == 0` 时返回 `AppError(code="conflict", message="工具已被他人修改，请刷新后重试")`，HTTP 409。
- 前端编辑表单提交时必须携带从 GET 获取的 `lock_version`。

### 7.4 热更新约定

- `ToolRegistry.reload_from_db(session)` 在 `AIService.ask` 入口、`provider.complete` 之前调用。
- reload 为全量替换：`_tools` 字典清空后重建，`_enabled` 集合重新计算。线程安全由"ask 单协程持有 registry 引用 + reload 在 ask 入口同步执行"保证（不涉及多写并发）。
- 禁用工具：`_tools` 中保留 `ToolSpec`（供管理 API 通过 `get` 返回），但 `_enabled` 不含其 name，`validate()` 对其抛 `unknown_tool`。

### 7.5 种子数据约定

- `seed_tools_if_empty(session)` 仅在 `ai_tool` 表行数为 0 时执行 INSERT，幂等。
- 种子源：`packages.ai.tools.ALL_TOOLS`（不变），逐行映射为 `AIToolRow` 写入，`enabled=True`、`lock_version=0`。
- 重复启动不重复写入；管理员后续修改不会被种子覆盖。

### 7.6 前端 API 客户端约定

- 所有 AI 工具 API 函数命名 `api<Verb>AITool<...>`，返回类型显式声明。
- `useQuery` 的 `queryKey` 用 `['ai-tools']` 和 `['ai-tools', name]`；写操作成功后 `queryClient.invalidateQueries({ queryKey: ['ai-tools'] })`。
- 错误提示统一用 `extractApiError(err)`。

### 7.7 JSON Schema 编辑器约定（v1）

- 抽屉内 `parameters_schema` 用 antd `Input.TextArea` + `style={{ fontFamily: 'monospace' }}`。
- 实时校验：`onChange` 时 `JSON.parse`，合法显示绿色"合法 JSON"，非法显示红色"第 N 行: 错误信息"。
- 保存前再次校验，非法时禁用保存按钮。
- P1（T-08）再升级为 `ajv` 校验"是合法 JSON Schema 且顶层 type=object"。

---

## 8. 待明确事项

| 编号 | 事项 | 影响范围 | 倾向 |
|---|---|---|---|
| **U-1** | 是否新增独立权限 `ai_tool:manage` | 权限矩阵、`packages/auth/permissions.py` | v1 复用 `system:manage` 不改矩阵；若后续需把工具管理授权给非平台管理员角色（如 `platform_auditor` 只读管理），再新增 `ai_tool:manage` + `ai_tool:read` 并授予相应角色 |
| **U-2** | parameters_schema 后端是否强校验 | API 写端点、`jsonschema` 依赖 | v1 P0 仅校验 JSON 可解析；P1（T-08）加"顶层 type=object"校验，可选引 `jsonschema` 做完整 Draft 07 校验 |
| **U-3** | JSON 编辑器是否升级为 Monaco | 前端依赖、抽屉体验 | v1 用 TextArea（零依赖）；若用户反馈需语法高亮/补全，P1 引 `@monaco-editor/react`（约 2MB，按需懒加载） |
| **U-4** | 工具删除是否需要 | API、数据一致性 | PRD 未提，v1 不做。若后续确需，加 `DELETE /ai-tools/{name}`（软删除：`enabled=false` + `deleted_at` 标记），避免破坏历史 `tool_calls_json` |
| **U-5** | `candidate` 字段修改的二次确认 | 前端交互 | Q-6 倾向允许改但二次确认。v1 在抽屉中 candidate 字段用 Radio + 切换为"候选"时弹 Modal 提示"将影响 AI 自动执行行为，确认？" |
| **U-6** | 多组织/租户工具 | 数据模型 | Q-8 v1 全局，`ai_tool` 无 `organization_id`。若后续需组织级工具，加 `organization_id` 列 + RLS 策略 + `ToolRegistry` 按 `org_id` 过滤 |
| **U-7** | 进行中对话的禁用工具处理 | `AIService.ask` 行为 | Q-4 倾向"禁用即生效，进行中调用被拒绝"。当前设计因 `validate()` 在 tool_calls 处理阶段也走 registry（已 reload），禁用工具会抛 `unknown_tool` 并记 `status="rejected"`，符合预期。需在测试中验证多轮对话场景。 |
| **U-8** | `updated_by` 是否回显操作人姓名 | API 响应、前端列 | v1 仅存 `updated_by` UUID，列表展示时间戳不展示操作人；P1 可 join `app_user` 回显 display_name（T-07 审计详情中已含 actor 信息） |
| **U-9** | 工具调用统计（T-10）数据来源 | P2 范围 | v1 不实现。P2 时基于 `ai_message.tool_calls_json` 聚合统计近 7 天调用次数/成功率，无需额外埋点表 |

---

## 附录 A：验收对照表（P0）

| PRD 验收标准 | 对应任务 | 落地点 |
|---|---|---|
| 新增 `ai_tool` 表，字段齐全 | 任务 1 | migration 0038 |
| 修改工具后下次 ask 生效 | 任务 4,5 | `reload_from_db` + `ask` 入口调用 |
| 禁用工具不进 tool_schemas | 任务 4,5 | `list_enabled_tools` / `_build_tool_schemas` |
| 首次部署写 12 条种子 | 任务 3,7 | `seed_tools_if_empty` + lifespan |
| 列表/筛选/搜索 | 任务 11 | `AIToolsPage` |
| 启停开关 + 二次确认 | 任务 11 | `AIToolsPage` Switch + Modal |
| 编辑抽屉 + JSON 校验 | 任务 12 | `ToolEditDrawer` |
| 非管理员 403 | 任务 6,13 | `require_permission("system:manage")` + Tab 条件渲染 |

## 附录 B：验收对照表（P1）

| PRD 验收标准 | 对应任务 | 落地点 |
|---|---|---|
| 新建工具 | 任务 6,12 | `POST` 端点 + 抽屉新建模式 |
| 审计记录 | 任务 8 | `AuditRecorder.record` |
| JSON Schema 严格校验 | 任务 12（U-2） | `ajv`（P1） |
| 生效 toast + updated_at | 任务 11,12 | toast + Table 列 |
