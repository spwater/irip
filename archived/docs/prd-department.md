# IRIP 增量 PRD — 机构/实验室管理

## 1. 项目信息

- **Language:** 中文
- **Programming Language:** Python 3.12+（后端）、TypeScript + React 18（前端）
- **Project Name:** irip-platform
- **原始需求：** 用户原话——"我希望在治理中，把数据所有机构加上。每一条实验与事实，都应该归属于某个机构，如，无机非材料实验室，固废实验室，仿真实验室，粉磨实验室，这样的。"
- **范围：** 增量需求，基于 Phase V0 平台骨架，新增机构/实验室管理功能。不重写 V0 PRD。
- **上游文档：** `docs/prd-v0.md`、`docs/arch-v0.md`

### 1.1 概念澄清

| 概念 | 对应英文标识 | 层级 | 说明 |
|---|---|---|---|
| 组织（Organization） | `organization` | 顶层 | V0 已有，单组织模式，如 "IRIP-DEMO"。由 bootstrap 创建，不在 Alembic 迁移中。 |
| 机构/实验室（Department） | `department` | 子层 | **本 PRD 新增**。组织内部的业务单元，如"无机非材料实验室""固废实验室"。所有业务数据归属到此层。 |

> **命名说明：** 用户口语中"机构"指组织内部的实验室/部门。因 `organization` 已被顶层组织占用，本 PRD 的"机构/实验室"在数据库与代码中统一使用 `department`（部门/实验室）作为标识，UI 显示为"机构管理"或"实验室管理"。

---

## 2. 产品目标

1. **建立组织内机构/实验室的完整生命周期管理**：管理员可在治理模块中创建、编辑、启用/禁用实验室，并维护实验室的基本信息（名称、编码、描述），为平台业务数据提供归属维度。
2. **实现业务数据的机构归属与筛选**：所有业务数据（实验与事实、标准、模型等）必须关联到某个机构/实验室，用户可按实验室维度筛选、浏览数据，形成以实验室为边界的数据视图。
3. **支撑按实验室隔离的用户权限体系**：用户与实验室建立关联，权限可在实验室粒度进行隔离，使研究员只能访问其所属实验室的数据，防止跨实验室数据泄露。

---

## 3. 用户故事

1. **As a** 平台管理员，**I want** 在治理页面的"机构管理"子页面中创建实验室（如"无机非材料实验室""固废实验室"），**so that** 平台业务数据有明确的归属维度。
2. **As a** 平台管理员，**I want** 编辑已有实验室的名称和描述，或将其禁用，**so that** 实验室信息保持准确且历史数据不受影响。
3. **As a** 平台管理员，**I want** 将用户分配到一个或多个实验室，**so that** 用户只能访问其所属实验室的数据。
4. **As a** 研究员，**I want** 在录入实验与事实时从下拉列表选择所属实验室，**so that** 每条数据自动归属到正确的实验室。
5. **As a** 研究员，**I want** 在实验与事实列表中按实验室筛选数据，**so that** 我可以聚焦于自己实验室的数据进行查阅和分析。

---

## 4. 需求池

### P0 — 必须（本增量验收门强制要求）

| # | 需求描述 | 优先级 |
|---|---------|--------|
| D1 | 新增 `department` 表（Alembic 迁移），字段：`id` UUID PK、`organization_id` UUID NOT NULL、`code` TEXT UNIQUE、`display_name` TEXT NOT NULL、`description` TEXT NULL、`status` TEXT NOT NULL DEFAULT 'active'、`sort_order` INT DEFAULT 0、`created_at`/`updated_at` timestamptz、`lock_version` INT。唯一约束 `(organization_id, code)`。 | P0 |
| D2 | 提供机构/实验室 CRUD API：`POST /api/v1/departments`（创建）、`GET /api/v1/departments`（分页列表，支持 status 筛选）、`GET /api/v1/departments/{id}`（详情）、`PATCH /api/v1/departments/{id}`（编辑名称/描述/排序）、`PATCH /api/v1/departments/{id}/status`（启用/禁用）。所有写操作需 `department:manage` 权限，读操作需 `department:read` 权限。 | P0 |
| D3 | 新增权限常量 `department:manage` 和 `department:read`，并将 `department:manage` 授予 `platform_administrator` 角色，`department:read` 授予全部角色（通过 `BUILTIN_ROLES` 更新 + 迁移种子回写）。 | P0 |
| D4 | 禁用实验室时执行软禁用（`status = 'disabled'`），已关联该实验室的历史数据保持不变，新数据录入时不再出现在可选列表中。禁用操作不可删除实验室。 | P0 |
| D5 | 前端治理页面新增"机构管理"子页面（Tab 或子路由），展示实验室列表（表格），支持创建（弹窗表单）、编辑（弹窗表单）、启用/禁用（状态切换按钮）。列表按 `sort_order` + `created_at` 排序。 | P0 |
| D6 | 定义业务数据关联约定：后续 V1 Task 15（实验与事实）及所有业务表（如 `fact`、`standard`、`model`）必须包含 `department_id` UUID NOT NULL FK → `department.id` 字段。本 PRD 仅定义约定，不实现 Facts 模块代码。 | P0 |
| D7 | Bootstrap 脚本扩展：创建组织后，可选地根据环境变量 `IRIP_SEED_DEPARTMENTS`（JSON 数组，如 `[{"code":"inorganic_lab","name":"无机非材料实验室"},{"code":"solid_waste_lab","name":"固废实验室"},{"code":"simulation_lab","name":"仿真实验室"},{"code":"grinding_lab","name":"粉磨实验室"}]`）幂等创建种子实验室。未设置环境变量时跳过。 | P0 |

### P1 — 重要（支撑权限隔离与用户关联）

| # | 需求描述 | 优先级 |
|---|---------|--------|
| D8 | 新增 `app_user_department` 关联表（多对多）：`user_id` UUID FK → `app_user.id`、`department_id` UUID FK → `department.id`、`is_primary` BOOLEAN DEFAULT false、`created_at` timestamptz。唯一约束 `(user_id, department_id)`。 | P1 |
| D9 | 提供用户-实验室关联管理 API：`PUT /api/v1/users/{id}/departments`（批量设置用户所属实验室）、`GET /api/v1/users/{id}/departments`（查看用户所属实验室）、`GET /api/v1/departments/{id}/users`（查看实验室下用户）。需 `user:manage` 权限。 | P1 |
| D10 | 在 `scope_grant` 表中新增可选字段 `department_id` UUID NULL FK → `department.id`，支持按实验室维度进行对象级授权。当 `department_id` 非 NULL 时，授权仅对该实验室范围内的资源生效。授权查询逻辑扩展：`department_id IS NULL`（全组织）或 `department_id = resource.department_id`（特定实验室）。 | P1 |
| D11 | 前端机构管理页面增加"成员管理"功能：在实验室列表的操作列增加"成员"按钮，点击弹出抽屉展示该实验室下用户列表，支持添加/移除用户。 | P1 |
| D12 | 所有业务数据列表查询 API 支持按 `department_id` 筛选参数。用户仅能查询其有 `department:read` 权限的实验室数据（platform_administrator 可查看全部）。 | P1 |

### P2 — 可选（增强体验）

| # | 需求描述 | 优先级 |
|---|---------|--------|
| D13 | 机构管理页面增加实验室数据统计面板：展示每个实验室的实验与事实数量、最近活动时间、成员人数等汇总指标。 | P2 |
| D14 | 支持实验室排序拖拽（前端拖拽修改 `sort_order`，批量保存）。 | P2 |
| D15 | 实验室编码修改时进行历史引用检查提示（不阻止修改，仅提示该编码被 N 条数据引用）。 | P2 |
| D16 | 治理页面导航增加"机构管理"入口在侧边栏或面包屑中的显式标识，并在实验室被禁用时以灰色标签区分。 | P2 |

---

## 5. UI 设计稿描述

治理页面（`/governance`）从当前的占位卡片升级为带 Tab 切换的管理页面。首期包含"机构管理"Tab，后续可扩展审计日志、授权管理等子页。

### 5.1 治理页面布局（GovernancePage 升级）

```
┌─────────────────────────────────────────────────────────┐
│  治理                                                    │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐                 │
│  │机构管理  │ │审计日志  │ │授权管理  │   ← Tab 切换      │
│  └──────────┘ └──────────┘ └──────────┘                 │
│  ┌─────────────────────────────────────────────────────┐│
│  │  机构管理                                              ││
│  │                                                       ││
│  │  [+ 新建实验室]                    状态: [全部 ▾]      ││
│  │  ┌──────────────────────────────────────────────┐    ││
│  │  │ 编码      | 名称             | 状态 | 成员 | 操作│    ││
│  │  ├──────────────────────────────────────────────┤    ││
│  │  │ inorganic_lab | 无机非材料实验室 | 启用 | 5  | 编辑/禁用/成员││
│  │  │ solid_waste_lab| 固废实验室      | 启用 | 3  | 编辑/禁用/成员││
│  │  │ simulation_lab | 仿真实验室      | 启用 | 2  | 编辑/禁用/成员││
│  │  │ grinding_lab   | 粉磨实验室      | 禁用 | 0  | 编辑/启用/成员││
│  │  └──────────────────────────────────────────────┘    ││
│  │  共 4 条                          < 1 >               ││
│  └─────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────┘
```

### 5.2 新建/编辑实验室弹窗

- **触发**：点击"新建实验室"按钮或列表行的"编辑"按钮。
- **表单字段**：
  - 编码（code）：必填，仅小写字母/数字/下划线，唯一性校验。编辑时锁定不可改（或确认后允许改）。
  - 名称（display_name）：必填，中文，如"无机非材料实验室"。
  - 描述（description）：选填，多行文本。
  - 排序（sort_order）：选填，整数，默认 0。
- **交互**：表单校验 → 提交 → 成功提示并刷新列表 → 关闭弹窗。
- **技术要点**：调用 `POST /api/v1/departments` 或 `PATCH /api/v1/departments/{id}`；Ant Design `Modal` + `Form`。

### 5.3 启用/禁用

- 在列表行的操作列中显示状态切换按钮：
  - 当前为"启用"时显示"禁用"按钮（红色文字链接）。
  - 当前为"禁用"时显示"启用"按钮（绿色文字链接）。
- 点击后弹出确认提示（Ant Design `Popconfirm`），确认后调用 `PATCH /api/v1/departments/{id}/status`。
- 禁用实验室的行以灰色文字/标签区分。

### 5.4 成员管理抽屉（P1）

- **触发**：点击列表行操作列的"成员"按钮。
- **布局**：从右侧滑出抽屉（Ant Design `Drawer`），宽度 480px。
- **内容**：
  - 顶部显示实验室名称。
  - 用户列表：展示已加入该实验室的用户（姓名、邮箱、是否主要实验室）。
  - "添加成员"按钮：弹出用户选择器（从系统用户中选择，支持搜索）。
  - 每行用户右侧"移除"按钮。
- **技术要点**：调用 `GET /api/v1/departments/{id}/users` 和 `PUT /api/v1/users/{id}/departments`。

### 5.5 业务数据录入中的实验室选择器

- 在实验与事实录入页面（V1 Task 15 实现时）的表单中，增加"所属实验室"下拉选择器（Ant Design `Select`）。
- 选项来源：`GET /api/v1/departments?status=active`，仅展示启用状态的实验室。
- 默认选中用户的"主要实验室"（`is_primary = true` 的 `app_user_department` 记录）。
- 必填校验：未选择时不允许提交。

### 5.6 业务数据列表中的实验室筛选

- 在实验与事实列表页的筛选栏增加"实验室"下拉筛选器。
- 普通用户仅能选择其有权限的实验室；platform_administrator 可选择全部。
- 筛选时调用 `GET /api/v1/facts?department_id={id}`（Facts API 在 V1 实现）。

---

## 6. 数据模型约定（供架构师参考）

### 6.1 `department` 表

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | UUID PK | DEFAULT gen_random_uuid() | 主键 |
| organization_id | UUID | NOT NULL FK→organization.id | 所属顶层组织 |
| code | TEXT | NOT NULL | 实验室编码，如 `inorganic_lab` |
| display_name | TEXT | NOT NULL | 中文显示名，如"无机非材料实验室" |
| description | TEXT | NULL | 描述 |
| status | TEXT | NOT NULL DEFAULT 'active' | `active` / `disabled` |
| sort_order | INT | NOT NULL DEFAULT 0 | 排序权重 |
| created_at | timestamptz | NOT NULL DEFAULT now() | 创建时间 |
| updated_at | timestamptz | NOT NULL DEFAULT now() | 更新时间 |
| lock_version | INT | NOT NULL DEFAULT 0 | 乐观锁 |

**唯一约束：** `uq_department_org_code (organization_id, code)`
**索引：** `ix_department_organization_id (organization_id)`、`ix_department_status (status)`

### 6.2 `app_user_department` 关联表（P1）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| user_id | UUID | NOT NULL FK→app_user.id | 用户 |
| department_id | UUID | NOT NULL FK→department.id | 实验室 |
| is_primary | BOOLEAN | NOT NULL DEFAULT false | 是否主要实验室 |
| created_at | timestamptz | NOT NULL DEFAULT now() | 关联创建时间 |

**唯一约束：** `uq_user_department (user_id, department_id)`
**索引：** `ix_user_department_department_id (department_id)`、`ix_user_department_user_id (user_id)`

### 6.3 `scope_grant` 扩展（P1）

在现有 `scope_grant` 表中新增可选字段：

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| department_id | UUID | NULL FK→department.id | 实验室级授权范围 |

授权查询逻辑扩展：当 `department_id` 为 NULL 时表示全组织范围（保持现有行为）；当 `department_id` 非 NULL 时，仅对该实验室内的资源生效。资源引用（`ResourceRef`）需扩展 `department_id` 字段以支持匹配。

### 6.4 业务表关联约定

所有后续业务表（`fact`、`standard`、`model` 等）在创建时必须包含：

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| department_id | UUID | NOT NULL FK→department.id | 所属实验室 |

此约定在 V1 Task 15（Facts 模块）及后续业务模块迁移中强制执行。

---

## 7. API 接口约定

### 7.1 机构/实验室管理

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| POST | `/api/v1/departments` | `department:manage` | 创建实验室 |
| GET | `/api/v1/departments` | `department:read` | 分页列表（支持 `status`、`search` 筛选） |
| GET | `/api/v1/departments/{id}` | `department:read` | 实验室详情 |
| PATCH | `/api/v1/departments/{id}` | `department:manage` | 编辑名称/描述/排序 |
| PATCH | `/api/v1/departments/{id}/status` | `department:manage` | 启用/禁用 |

### 7.2 用户-实验室关联（P1）

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| PUT | `/api/v1/users/{id}/departments` | `user:manage` | 批量设置用户所属实验室 |
| GET | `/api/v1/users/{id}/departments` | `user:manage` 或本人 | 查看用户所属实验室 |
| GET | `/api/v1/departments/{id}/users` | `department:read` | 查看实验室下用户 |

### 7.3 请求/响应示例

**创建实验室：**

```json
// POST /api/v1/departments
// Request:
{
  "code": "inorganic_lab",
  "display_name": "无机非材料实验室",
  "description": "负责无机非金属材料的实验与研究",
  "sort_order": 1
}
// Response 201:
{
  "id": "0197a1b2-...",
  "organization_id": "0197...",
  "code": "inorganic_lab",
  "display_name": "无机非材料实验室",
  "description": "负责无机非金属材料的实验与研究",
  "status": "active",
  "sort_order": 1,
  "created_at": "2026-07-21T08:00:00Z",
  "updated_at": "2026-07-21T08:00:00Z"
}
```

**列表查询：**

```json
// GET /api/v1/departments?status=active&cursor=...&limit=20
// Response 200:
{
  "items": [
    {
      "id": "0197a1b2-...",
      "code": "inorganic_lab",
      "display_name": "无机非材料实验室",
      "status": "active",
      "sort_order": 1,
      "member_count": 5
    }
  ],
  "next_cursor": null,
  "has_more": false
}
```

---

## 8. 权限矩阵增量

在现有 7 个内置角色基础上新增 `department:manage` 和 `department:read` 权限：

| 角色 | department:manage | department:read | 说明 |
|---|---|---|---|
| platform_administrator | ✅ | ✅ | 可管理全部实验室 |
| standard_owner | ❌ | ✅ | 可查看实验室（关联标准时） |
| data_steward | ❌ | ✅ | 可查看实验室（录入事实时选择） |
| researcher | ❌ | ✅ | 可查看实验室（选择归属） |
| model_engineer | ❌ | ✅ | 可查看实验室（关联模型时） |
| reviewer | ❌ | ✅ | 可查看实验室（审核数据时） |
| read_only_user | ❌ | ✅ | 可查看实验室（浏览数据时筛选） |

---

## 9. 待确认问题

1. **实验室编码是否允许修改？** 编码作为业务数据的外部引用标识，修改可能影响历史数据关联。建议：创建后锁定不可改（仅名称/描述可编辑），或允许修改但需二次确认。需用户确认。

2. **实验室是否支持层级结构？** 当前需求为扁平结构（实验室直接隶属于组织）。未来是否需要支持实验室下设子组（如"无机非材料实验室 → 水泥组 / 陶瓷组"）？建议 V1 保持扁平，层级结构延后。需用户确认。

3. **禁用实验室后已关联数据如何处理？** 当前方案：软禁用，历史数据保留关联不变，新数据不可选。是否需要支持"禁用时批量迁移数据到其他实验室"？建议不支持（数据迁移风险高），仅禁用。需用户确认。

4. **用户与实验室的关系是否需要审批流？** 当前方案：管理员直接分配。是否需要实验室负责人审批加入请求？建议 V1 不做审批流，管理员直接分配。需用户确认。

5. **"主要实验室"的用途？** 用户可关联多个实验室，其中一个标记为 `is_primary`。主要实验室用于业务数据录入时的默认选中。是否还需要其他用途（如默认数据筛选范围）？需用户确认。

6. **种子实验室列表是否固化？** 用户举例的四个实验室（无机非材料、固废、仿真、粉磨）是否应在 bootstrap 时自动创建，还是完全由管理员手动创建？当前方案：通过环境变量可选创建，不强制。需用户确认。

7. **UI 中"机构"还是"实验室"作为统一术语？** 用户原话交替使用"机构"和"实验室"。建议 UI 统一使用"实验室"（因 `department` 表名已反映部门语义，且用户举例均为实验室）。需用户确认。

---

> **验收门：** 管理员可在治理页面创建/编辑/禁用实验室；后续 Facts 模块（V1 Task 15）录入数据时必须选择实验室并按实验室筛选；用户与实验室关联后仅能访问授权范围内的数据。
