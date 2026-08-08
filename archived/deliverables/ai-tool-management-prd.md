# AI 工具管理 PRD

> **文档版本**：v1.0
> **创建日期**：2026-07-28
> **产品经理**：许清楚（Xu）
> **项目**：IRIP 平台
> **模块**：AI 助手 - 工具管理
> **现状基线**：`packages/ai/tools.py`（8 个白名单工具 + 4 个候选工具，硬编码元组）

---

## 1. 背景与问题

IRIP 平台 AI 助手通过 Function Calling 调用工具查询平台数据。当前工具定义硬编码在 `packages/ai/tools.py` 的 Python 元组（`WHITELIST_TOOLS` / `CANDIDATE_TOOLS`）中，存在以下问题：

1. **修改成本高**：调整工具描述、参数 schema 需要改代码、跑测试、重启 API；
2. **不可观测**：无法在界面上查看当前 AI 可用哪些工具、各自的 schema 和权限要求；
3. **无法动态控制**：无法临时禁用某个工具（如线上出问题时），只能改代码回滚；
4. **配置与代码耦合**：工具的"声明"（name/description/schema/权限）和"执行逻辑"（`_execute_tool` 中的 if-elif 分派）混在一起，声明本应是配置，却被写死在代码里。

### 1.1 现有结构摘要

- **`ToolSpec`**（`tools.py:22`）：不可变值对象，字段 = `name` / `display_name` / `description` / `required_permission` / `candidate` / `parameters_schema`。
- **`ToolRegistry`**（`tools.py:292`）：注册表，初始化时加载全部工具，提供 `validate` / `is_candidate` / `list_tools` 等方法。
- **`AIService.ask`**（`service.py:508`）：每次问答时调用 `self._tool_registry.names()` 和 `self._build_tool_schemas()` 构建工具定义传给 Provider，工具执行走 `_execute_tool` 的 if-elif 分派（`service.py:920`）。
- **`PlatformPage`**（`apps/web/src/pages/PlatformPage.tsx`）：当前两个 Tab（AI 助手 / 数据抽取），新页面会以新 Tab 形式加入。

### 1.2 关键约束（来自代码现状）

> ⚠️ **工具执行逻辑是硬编码的**：`AIService._execute_tool`（`service.py:920-961`）用 `if tool_name == "search_facts" ... elif ...` 分派到各 `_handle_*` 方法。这意味着 UI **只能管理工具的"声明"（元数据 + schema），无法管理"执行逻辑"**。通过 UI 新建的工具若没有对应 handler，AI 调用时会走到 `else` 分支返回"未实现"。此约束影响需求边界，详见 §6 待确认问题 Q-1。

---

## 2. 产品目标

| 编号 | 目标 | 衡量标准 |
|---|---|---|
| **G-1** | 将 AI 工具定义从硬编码迁移为可配置，支持热更新 | 修改工具定义后无需重启 API，下一次 `ask` 请求即生效 |
| **G-2** | 提供可视化界面查看、编辑、管理工具定义 | 平台管理员可在 UI 上完成工具的查看/编辑/启停，无需改代码 |
| **G-3** | 支持工具启用/禁用，控制 AI 可调用工具集 | 禁用的工具对 AI 不可见、不可调用；启用后立即恢复 |
| **G-4** | 管理权限严格限制 | 仅 `platform_administrator` 可访问管理页面与 API，其他角色 403 |

---

## 3. 用户故事

| 编号 | 角色 | 需求 | 价值 |
|---|---|---|---|
| **US-1** | 平台管理员 | 我希望不重启 API 就能调整工具的描述和参数 schema，这样 AI 能更准确地理解工具用途 | 降低维护成本，快速迭代 Prompt 工程 |
| **US-2** | 平台管理员 | 我希望能启用/禁用某个工具，这样线上工具出问题时能立即下线而无需回滚代码 | 快速止血，降低故障影响 |
| **US-3** | 平台管理员 | 我希望在一个页面看到所有工具的定义、权限要求、类型（只读/候选）和启用状态，这样我能评估 AI 的能力边界 | 可观测性 |
| **US-4** | 平台管理员 | 我希望能新建工具定义（声明层），这样接入新数据查询能力时只需在 UI 配置 schema，后端逐步实现 handler | 配置与实现解耦 |
| **US-5** | AI / Prompt 工程师 | 我希望调整 schema 后立即生效，这样我能在对话中反复调试工具的参数描述 | 缩短调试回路 |
| **US-6** | 安全工程师 | 我希望工具管理权限严格限制在 `platform_administrator`，且所有修改有审计记录，这样能追溯谁改了什么 | 合规与可追溯 |

---

## 4. 需求池（按优先级分层）

### 4.1 P0 需求（Must Have）

#### T-01 [P0] 工具定义持久化与热更新

| 项目 | 内容 |
|---|---|
| **编号** | T-01 |
| **标题** | 将工具定义持久化到数据库，支持不重启 API 即生效 |
| **需求描述** | 新增 `ai_tool` 表存储工具定义（字段对应 `ToolSpec` + `enabled` 字段）。`ToolRegistry` 改为从数据库加载，并提供 `reload()` 方法。`AIService.ask` 在每次调用时使用最新工具定义（通过 TTL 缓存或每次 reload，性能与实时性权衡见 Q-3）。首次部署时将现有 12 个硬编码工具作为种子数据写入。 |
| **验收标准** | • 新增 `ai_tool` 表，字段覆盖 `name` / `display_name` / `description` / `required_permission` / `candidate` / `parameters_schema`(JSONB) / `enabled` / `created_at` / `updated_at` • 通过 API 修改工具后，下一次 `AIService.ask` 使用新定义（无需重启） • 禁用的工具不出现在 `tool_schemas` 中，AI 不会调用 • 首次部署自动写入 12 条种子数据，与现有 `ALL_TOOLS` 一致 |
| **涉及文件** | `packages/ai/tools.py`、`packages/ai/service.py`、新增 migration、新增 repository |
| **建议角色** | 后端 |

#### T-02 [P0] 工具列表查看（含筛选/搜索）

| 项目 | 内容 |
|---|---|
| **编号** | T-02 |
| **标题** | 管理页面展示所有工具，支持按类型/状态筛选和按名称搜索 |
| **需求描述** | 表格列：`name` / `display_name` / `description`（截断） / `candidate`（只读/候选标签） / `required_permission` / `enabled`（开关） / `updated_at` / 操作（编辑）。筛选：类型（全部/只读/候选）、状态（全部/启用/禁用）。搜索：按 `name` 或 `display_name` 模糊匹配。 |
| **验收标准** • 进入页面展示全部工具 • 筛选和搜索实时生效 • 非管理员访问页面返回 403 / 路由隐藏 |
| **涉及文件** | 新增前端页面、新增 API `GET /api/ai/tools` |
| **建议角色** | 前端 + 后端 |

#### T-03 [P0] 工具启用/禁用开关

| 项目 | 内容 |
|---|---|
| **编号** | T-03 |
| **标题** | 在列表页通过开关启用/禁用工具，禁用后 AI 不可见 |
| **需求描述** | 列表每行一个 Switch 开关，切换后调用 `PATCH /api/ai/tools/{name}/enabled`。禁用的工具：`ToolRegistry.list_tools` 不返回，`_build_tool_schemas` 不生成 schema，`validate` 抛 unknown_tool。 |
| **验收标准** | • 切换开关后列表状态立即更新 • 禁用后 AI 对话中该工具不可调用 • 启用后立即恢复 • 二次确认弹窗（防止误操作） |
| **涉及文件** | 前端、`packages/ai/tools.py`、API |
| **建议角色** | 前端 + 后端 |

#### T-04 [P0] 工具编辑（元数据 + parameters_schema JSON 文本框）

| 项目 | 内容 |
|---|---|
| **编号** | T-04 |
| **标题** | 编辑工具的 display_name / description / required_permission / candidate / parameters_schema |
| **需求描述** | 点击"编辑"打开抽屉表单。字段：`display_name`（文本）、`description`（多行文本）、`required_permission`（文本，带常用权限下拉提示）、`candidate`（开关：只读/候选）、`parameters_schema`（JSON 文本框，monospace，带语法高亮，保存前校验 JSON 合法性）。`name` 为唯一键，**编辑模式下只读不可改**（见 Q-7）。 |
| **验收标准** | • 表单回填当前值 • JSON 文本框非法 JSON 时保存按钮禁用并提示行号 • 保存后列表刷新，热更新生效 • 必填字段校验 |
| **涉及文件** | 前端、API `PATCH /api/ai/tools/{name}` |
| **建议角色** | 前端 + 后端 |

#### T-05 [P0] 管理权限控制

| 项目 | 内容 |
|---|---|
| **编号** | T-05 |
| **标题** | 仅 `platform_administrator` 可访问管理页面与 API |
| **需求描述** | 后端 API 加 `require_permission("platform:admin")` 或校验角色为 `platform_administrator`。前端路由在 `PlatformPage` 的 Tab 配置中仅对 `platform_administrator` 显示"AI 工具管理"Tab，其他角色看不到入口；直接访问 URL 也被 API 403 拦截。 |
| **验收标准** | • 非 `platform_administrator` 调用 API 返回 403 • 非 `platform_administrator` 看不到 Tab 入口 • 直接访问 URL 因 API 403 显示无权限提示 |
| **涉及文件** | API 路由权限守卫、前端 Tab 条件渲染 |
| **建议角色** | 后端 + 前端 |

### 4.2 P1 需求（Should Have）

#### T-06 [P1] 新建工具

| 项目 | 内容 |
|---|---|
| **编号** | T-06 |
| **标题** | 通过 UI 新建工具定义（仅声明层） |
| **需求描述** | 列表页"新建工具"按钮打开抽屉。`name` 此时可填（保存后不可改，需符合 `^[a-z][a-z0-9_]*$` 命名规则且不与现有重复）。其他字段同编辑。新建工具默认 `enabled=true`、`candidate=false`。需显著提示：新建工具仅创建声明，执行逻辑需后端实现（见 Q-1）。 |
| **验收标准** | • `name` 重复时保存报错 • `name` 格式不合法时校验提示 • 新建后列表出现，热更新生效 • UI 上对"未实现"工具有明确标识（如调用状态为 error 时标注） |

#### T-07 [P1] 修改审计记录

| 项目 | 内容 |
|---|---|
| **编号** | T-07 |
| **标题** | 记录工具定义的修改历史（谁/何时/改了什么） |
| **需求描述** | 复用现有审计日志机制，每次 create/update/enable/disable 写审计日志，记录操作人、工具名、变更前后 diff。管理页工具详情区可查看该工具的修改历史。 |
| **验收标准** | • 审计日志表有记录 • 工具详情可查看历史 • diff 包含字段级变更 |

#### T-08 [P1] parameters_schema JSON Schema 校验

| 项目 | 内容 |
|---|---|
| **编号** | T-08 |
| **标题** | 保存前校验 parameters_schema 是合法 JSON Schema 且 type 为 object |
| **需求描述** | 前端保存前用 JSON Schema 校验库验证 `parameters_schema` 本身是合法的 JSON Schema，且顶层 `type` 为 `"object"`（OpenAI function calling 要求）。校验失败给出具体错误位置。 |
| **验收标准** | • 非法 JSON Schema 无法保存 • 顶层 type 非 object 时提示 • 错误信息定位到字段 |

#### T-09 [P1] 热更新生效状态指示

| 项目 | 内容 |
|---|---|
| **编号** | T-09 |
| **标题** | 编辑保存后提示"已生效"，并在列表展示最近生效时间 |
| **需求描述** | 保存成功后 toast 提示"工具定义已更新，AI 下次调用即生效"。列表 `updated_at` 列展示最近修改时间。可选：调用 `GET /api/ai/tools/refresh-status` 确认 registry 已 reload。 |
| **验收标准** | • 保存后 toast 正确 • `updated_at` 更新 • 生效校验 API 返回成功 |

### 4.3 P2 需求（Nice to Have）

| 编号 | 标题 | 描述 |
|---|---|---|
| **T-10** | 工具调用统计 | 列表展示每个工具近 7 天调用次数、成功率，便于评估工具价值 |
| **T-11** | 工具复制 | 基于现有工具复制创建新工具，加速配置 |
| **T-12** | 工具导入/导出 | 支持 JSON 文件批量导入导出工具定义，便于环境迁移 |
| **T-13** | 工具调用测试 | 详情页提供"试调"按钮，输入参数后真实调用工具查看返回，方便验证 schema |
| **T-14** | 历史版本回滚 | 工具定义历史版本可查看并一键回滚 |

---

## 5. UI 设计稿

### 5.1 入口与导航

- **入口位置**：`PlatformPage`（`apps/web/src/pages/PlatformPage.tsx`）新增第三个 Tab **"AI 工具管理"**，仅对 `platform_administrator` 可见。
- **路由**：复用现有 `/platform?tab=...` 模式，新增 `tab=ai-tools`。`PlatformPage` 的 `VALID_TABS` 增加 `'ai-tools'`，并根据当前用户角色条件渲染该 Tab。
- **页面布局**：延续现有平台页风格（antd，顶部 `Title level={2}` + `Tabs`）。

```
平台应用
├── AI 助手          (assistant)
├── 数据抽取          (parameters)
└── AI 工具管理       (ai-tools)  ← 新增，仅 platform_administrator
```

### 5.2 列表页布局

```
┌─────────────────────────────────────────────────────────────┐
│  AI 工具管理                              [新建工具] [刷新]    │
├─────────────────────────────────────────────────────────────┤
│  筛选: [类型: 全部▾] [状态: 全部▾]  搜索: [__________] 🔍      │
├─────────────────────────────────────────────────────────────┤
│  名称          显示名        描述        类型    权限    启用  更新时间     操作    │
│  search_standards 搜索标准变量  按编码...  只读    standard:read [⚪●]  07-28 10:00 编辑│
│  suggest_mapping  建议映射     为原始...  候选    ingestion:write [⚪●] 07-28 09:30 编辑│
│  ...                                                          │
└─────────────────────────────────────────────────────────────┘
```

**交互细节**：
- **启用开关**：点击切换 → 二次确认弹窗"确定禁用工具 xxx？禁用后 AI 将无法调用" → 确认后调用 PATCH API → 成功 toast + 列表状态更新。
- **类型标签**：只读 = 蓝色 Tag，候选 = 橙色 Tag。
- **描述列**：超 40 字截断，hover 显示完整。
- **分页**：工具数量预计 < 50，默认不分页；超过 50 条加分页。
- **空状态**：无工具时显示"暂无工具，点击新建"。

### 5.3 编辑/新建抽屉

点击"编辑"或"新建工具"从右侧滑出抽屉（宽度 600px）：

```
┌──────────────────────────────────────┐
│  编辑工具: search_standards        ✕  │
├──────────────────────────────────────┤
│  名称 (name) *                        │
│  [search_standards        ] (只读)    │
│                                       │
│  显示名 (display_name) *              │
│  [搜索标准变量            ]            │
│                                       │
│  描述 (description) *                  │
│  ┌──────────────────────────┐        │
│  │ 按编码、名称或别名搜索... │        │
│  └──────────────────────────┘        │
│                                       │
│  所需权限 (required_permission) *      │
│  [standard:read        ] 常用权限▾     │
│                                       │
│  类型 (candidate)                      │
│  ○ 只读工具（AI 可直接执行）            │
│  ● 候选工具（需人工审批）               │
│                                       │
│  参数 Schema (parameters_schema)       │
│  ┌──────────────────────────┐        │
│  │ {                        │ monospace│
│  │   "type": "object",      │  语法高亮│
│  │   "properties": {        │        │
│  │     "query": {           │        │
│  │       "type": "string"   │        │
│  │     }                    │        │
│  │   },                     │        │
│  │   "required": ["query"]  │        │
│  │ }                        │        │
│  └──────────────────────────┘        │
│  [✓ 合法 JSON] / [✗ 第3行语法错误]    │
│                                       │
│  ⚠ 提示: 修改仅更新工具声明，执行逻辑  │
│    由后端代码实现。新建工具若无对应    │
│    handler，AI 调用将返回"未实现"。    │
├──────────────────────────────────────┤
│              [取消]    [保存]         │
└──────────────────────────────────────┘
```

**交互细节**：
- **名称字段**：编辑模式只读（灰底）；新建模式可填，失焦校验唯一性和格式。
- **权限下拉**：输入框 + 下拉提示，可选常见权限（`standard:read` / `fact:read` / `parameter:read` / `provenance:read` / `model:predict` / `fact:write` / `parameter:write` / `model:publish` / `ingestion:write`），也允许自定义输入。
- **JSON 文本框**：等宽字体，基础语法高亮。输入时实时解析，底部状态行显示"合法 JSON"或错误位置。保存前再次校验。
- **保存**：校验全部必填字段 + JSON 合法性 → 调用 API → 成功 toast"工具定义已更新，AI 下次调用即生效" → 抽屉关闭，列表刷新。
- **新建工具提示**：黄色 Alert 显著提示"仅创建声明层，执行逻辑需后端实现"。

### 5.4 权限与异常态

- **非管理员**：Tab 隐藏；直接访问 `?tab=ai-tools` 时 API 返回 403，前端显示"无权限"占位。
- **加载失败**：列表加载失败显示错误态 + 重试按钮。
- **保存冲突**：乐观锁冲突（他人同时修改）提示"工具已被他人修改，请刷新后重试"。

---

## 6. 待确认问题

| 编号 | 问题 | 影响范围 | 倾向建议 |
|---|---|---|---|
| **Q-1** | **工具执行逻辑边界**：UI 只能管理工具"声明"（元数据 + schema），无法管理"执行逻辑"（`_execute_tool` 的 if-elif）。新建工具若无对应 handler，AI 调用返回"未实现"。是否接受此限制？还是需要支持通过 UI 注册执行逻辑（如配置 webhook URL 或脚本）？ | 需求边界、P0/P1 范围 | v1 接受限制，仅管理声明层；执行逻辑仍由后端代码实现。后续版本可考虑 webhook 扩展点。 |
| **Q-2** | **种子数据策略**：工具定义持久化到数据库后，现有 `WHITELIST_TOOLS` / `CANDIDATE_TOOLS` 硬编码元组如何处理？保留作为首次部署种子？还是完全废弃改为读库？ | 数据迁移、`tools.py` 重构 | 保留为种子数据源（首次部署写入 DB），运行时只读 DB。保留元组作为"出厂默认"便于重置。 |
| **Q-3** | **热更新生效粒度**：修改后"下一次 `ask` 即生效"是指每次 `ask` 都 reload registry（简单但有 DB 查询开销），还是 TTL 缓存 + 手动刷新按钮，还是事件通知？ | 性能 vs 实时性 | TTL 缓存（如 5 秒）+ 管理页保存后主动触发 reload。兼顾性能与实时性。 |
| **Q-4** | **进行中对话的处理**：禁用工具后，正在进行中的对话若已发起该工具调用，如何处理？拒绝执行还是放行本次？ | 用户体验、安全 | 禁用即生效，进行中调用被拒绝（返回"工具已禁用"），用户可重新提问。 |
| **Q-5** | **`name` 是否可改**：`name` 是 ToolRegistry 唯一键，也是 AI 工具标识。编辑时是否允许改名？改名会导致历史对话中的 tool_call 记录与现工具对不上。 | 数据一致性 | 不允许改名。`name` 创建后只读，需要改名只能新建 + 禁用旧的。 |
| **Q-6** | **`candidate` 是否可改**：将只读工具改为候选（或反之）会影响 AI 是否自动执行。是否允许通过 UI 修改此字段？ | 安全控制 | 允许修改，但需二次确认（涉及 AI 执行行为变化）。 |
| **Q-7** | **`required_permission` 取值范围**：是自由输入还是限定在已有权限枚举内？自由输入可能写错权限字符串导致工具永远无权调用。 | 数据质量 | 自由输入 + 常用权限下拉提示，保存时校验权限字符串是否存在于 `BUILTIN_ROLES` 权限矩阵中，不存在则警告（允许保存，因为可能有动态权限）。 |
| **Q-8** | **多组织/全局工具**：工具定义是全局共享还是按组织隔离？当前 AI 对话有 `organization_id`，但工具定义本质是平台级配置。 | 数据模型 | v1 工具定义为**全局**（平台级），不按组织隔离。未来如需组织级工具再扩展。 |
| **Q-9** | **回滚机制**：是否需要工具定义的历史版本与一键回滚（P2 T-14）？还是仅靠审计日志追溯即可？ | P2 范围 | v1 仅审计日志，不做回滚。若实际出现误改频繁，再纳入 P2。 |

---

## 7. 范围与里程碑建议

| 阶段 | 内容 | 交付物 |
|---|---|---|
| **阶段一** | P0：后端工具定义持久化 + 热更新 + 管理 API | migration、repository、`ToolRegistry` 改造、API |
| **阶段二** | P0：前端管理页面（列表/编辑/启停/权限） | `PlatformPage` 新 Tab、工具管理组件 |
| **阶段三** | P1：新建工具、审计、Schema 校验、生效状态 | 新建抽屉、审计集成、校验逻辑 |
| **阶段四** | P2：统计/复制/导入导出/试调/回滚 | 按需迭代 |

---

## 8. 附录

### 8.1 现有工具清单（种子数据）

| name | display_name | candidate | required_permission |
|---|---|---|---|
| search_standards | 搜索标准变量 | 否（只读） | standard:read |
| search_facts | 搜索事实 | 否 | fact:read |
| search_parameters | 搜索参数 | 否 | parameter:read |
| explain_provenance | 解释溯源链路 | 否 | provenance:read |
| compare_experiments | 对比实验 | 否 | fact:read |
| run_published_model | 运行已发布模型 | 否 | model:predict |
| draft_report | 生成报告草稿 | 否 | fact:read |
| extract_data | 数据提取 | 否 | ingestion:write |
| suggest_mapping | 建议映射 | 是（候选） | ingestion:write |
| suggest_fact_revision | 建议事实修订 | 是 | fact:write |
| create_parameter_candidate | 创建参数候选 | 是 | parameter:write |
| create_model_publish_request | 创建模型发布请求 | 是 | model:publish |

### 8.2 涉及代码位置

| 关注点 | 位置 |
|---|---|
| ToolSpec 定义 | `packages/ai/tools.py:22` |
| 硬编码工具列表 | `packages/ai/tools.py:85`（WHITELIST）、`packages/ai/tools.py:211`（CANDIDATE） |
| ToolRegistry | `packages/ai/tools.py:292` |
| AIService.ask 工具加载 | `packages/ai/service.py:593`（names）、`packages/ai/service.py:596`（schemas） |
| 工具执行分派 | `packages/ai/service.py:920` |
| Provider 状态返回工具列表 | `packages/ai/service.py:1458` |
| 平台页面 Tab 结构 | `apps/web/src/pages/PlatformPage.tsx:8`（VALID_TABS）、`:36`（items） |
| 路由定义 | `apps/web/src/app/router.tsx:179`（platformRoute） |
