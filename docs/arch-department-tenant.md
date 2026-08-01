# ADR：Department 扶正为多租户隔离键，Organization 退役

- 状态：草案（Draft）
- 日期：2026-08-01
- 关联文档：docs/arch-department.md、迁移 0032 / 0048 / 0060
- 决策者：待定（本文档为方案起草，评审后冻结）

---

## 1. 背景

### 1.1 现状：两套并行、各司其职的"组织"概念

| 层 | 机制 | 强度 | 现状 |
|---|---|---|---|
| organization | RLS + 每事务 `SET LOCAL app.current_org_id` | 硬隔离（DB 兜底） | 全库仅 1 个常量（IRIP-DEMO），无 CRUD API / UI；表由 bootstrap.py 创建，不在 Alembic 体系内，无任何 FK 指向它 |
| department | 归属（department_id）+ 共享白名单（visible_departments JSONB）+ 后代部门递归 | 软隔离（应用层 WHERE） | 仅 equipment / industrial_object / app_user 落地；有完整 CRUD 与 UI |
| user | 角色 + app_user_department 归属 | 权限 | 正常运行 |

### 1.2 问题

1. **organization_id 是恒真过滤**：单组织部署下，RLS 每条策略的过滤条件永远为真，硬隔离能力没有被实际使用，但每张租户表都背着这一列和每事务一次 GUC 设置的成本。
2. **数据表缺 department_id 是历史遗留而非设计**：department 表 0006（2026-07-21）才引入，fact / parameter / job / provenance / model 等核心数据表先于它存在，之后未回填。当前"数据全组织共享"是惯性，不是意图。
3. **概念冗余**：用户可感知、可管理的组织单元只有 department；organization 对用户不可见、不可管理，却占据安全模型的锚点位置。

### 1.3 决策

**department_id 成为全系统唯一的多租户隔离键（租户 = department 树的节点），organization 表及所有 organization_id 列退役。**

组织（原 organization 角色）由 department 树的**根节点**承担。

---

## 2. 目标概念模型

```
root（根部门，code='root'，哨兵节点 = 原 organization）
 └── 实验室 A
 │    └── 课题组 A1
 └── 实验室 B
```

三条核心规则：

1. **归属（ownership）**：每张数据表带 `department_id NOT NULL`，表示数据归属的部门。归属是稳定 FK 值，不随部门树调整而变化。
2. **可见性（visibility）**：`归属部门 ∈ 用户可见部门集` OR `visible_departments @> [用户部门]`。可见部门集 = 用户所在部门及其后代（递归 CTE，沿用 dept_scope 现状）。
3. **根部门语义**：挂在 root 的数据 = 全组织共享数据（原"organization 级"数据的去处）。root 不是一个真实实验室，不出现于实验室选择器的业务语境（或显示为"公共"）。

---

## 3. 五个关键决策点

### D1. organization 退役方式

- bootstrap 改为创建 **root 部门**（`code='root'`，`parent_id=NULL`），不再创建 organization 表。
- `department.organization_id` 列删除；唯一约束 `uq_department_org_code (organization_id, code)` 改为 **`(parent_id, code)` 同级唯一**（NULL parent 用 `COALESCE` 表达式索引或允许 root 级 code 全局唯一，迁移时确定）。
- 所有业务表的 `organization_id` 列在最终阶段统一 DROP（见 §7 阶段 3）。
- 命名接受现实：department 同时表达"机构"和"实验室"，UI 层用语不变（根节点显示为机构名）。

### D2. 无归属数据的去处

以下数据在写入时天然没有用户部门上下文：

- 管理员 / 系统创建的数据（字典、内置组件、预置流程）；
- Celery worker 执行 job / derivation / 定时任务产生的数据（当前 worker `session_scope(factory)` 无 principal，payload 只带 organization_id）。

**规则：所有写入路径必须能解析出 department_id；解析不到时挂 root 部门。**

具体：

- API 请求：取当前用户的 primary department；用户多部门时，写入接口显式传 `department_id`（前端选择器），缺省 primary。
- Worker：任务 payload 增加 `department_id`（提交 job 时从提交者上下文快照），worker `session_scope` 用该值设置 GUC；定时任务（Beat 发起、无用户）挂 root。
- 审计字段保留 `created_by`，root 兜底不影响追责。

### D3. 跨部门共享

科研数据平台的核心价值是数据复用，跨室共享必须保留，机制沿用设备/对象已验证的白名单：

- 需要共享的表（fact、parameter、evidence_set 等，见 §4 分类）同时带 `visible_departments JSONB NOT NULL DEFAULT '[]'`，配 GIN 索引。
- RLS 策略写为：

```sql
USING (
  department_id = current_setting('app.current_dept_id', true)::uuid
  OR visible_departments @> jsonb_build_array(current_setting('app.current_dept_id', true))
)
```

- 共享授权是**显式动作**（数据 owner 或管理员操作），不走"后代自动可见"——后代递归只用于用户侧的可见部门集计算，不用于数据侧授权。
- 不共享的表（audit_event、secret、job 等）只保留等值条件，策略更简。

### D4. 溯源链（provenance / derivation）豁免

血缘关系天然跨部门：A 室的 parameter 可能从 B 室共享出来的 fact 推导。若 derivation_run / provenance_edge 严格按部门隔离，谱系图断链。

**规则：**

- `provenance_edge` **豁免部门 RLS**（不设 department_id，不设策略）——它只存两端 ID 与关系类型，不含业务内容；端点本身的可见性由端点表的策略保证。查询谱系时，不可见端点显示为"无权限节点"占位而非断链。
- `derivation_run` 带 department_id（按执行者归属），但其输入/输出引用不做跨部门 FK 级校验，展示层处理占位。

### D5. 部门树调整与可见性稳定性

风险：可见性含"后代递归"时，re-parent 一个部门会改变其整个子树的可见范围——数据可见性被组织架构调整绑架。

**规则：**

- **归属不动**：re-parent 只改 department.parent_id，任何数据行的 department_id 不变。
- **可见性随树**：用户可见部门集按当前树实时计算——即"组织调整 = 权限调整"是**有意语义**，与设备可见性现状一致；调树操作写入审计日志（谁、何时、把哪个子树从哪移动到哪）。
- **防误操作**：re-parent 非空子树需二次确认（前端），治理页提供"影响预览"（该子树下各部门数据量、受影响用户数），影响预览可作为 P1 增强，不阻塞 v1。

---

## 4. 数据表分类改造清单

以 0059 之后仍存活的表为准（0032 清单中的 method / variable / standard_package / mapping_profile / fact_template / ingestion_job 已在 0055–0058 删除）。

### A 类：补 department_id NOT NULL + visible_departments（归属 + 可共享）

| 表 | 存量回填依据 |
|---|---|
| fact | 创建者 primary 部门；无创建者 → root |
| parameter（含 parameter_version 随父） | 同上 |
| evidence_set | 同上 |
| artifact | 关联 job/对象的归属；孤立 → root |
| model | 创建者 primary 部门 → root |
| transformation_recipe | 同上 |
| component（component_version 随父表查询，不加列） | root（内置组件全组织共享） |
| flow_definition | root 或创建者部门 |
| industrial_object | 已有 department_id（nullable → 回填 root 后改 NOT NULL） |
| equipment | 已有 department_id，无需动 |

### B 类：只补 department_id NOT NULL（归属，不开放共享）

| 表 | 存量回填依据 |
|---|---|
| job | 提交者部门快照 |
| flow_run | 关联 flow_definition / 提交者 |
| derivation_run | 执行者部门 |
| ai_conversation（+ ai_message 随父） | 创建者部门 |
| audit_event | actor 部门；系统事件 → root |
| scope_grant | 已有 department_id（NULL=全组织 → 改 root 哨兵语义） |
| secret | root（平台级） |
| backup_record | root |
| connector 各表 | root 或管理员部门 |
| app_user | 已有（organization_id 退役，department_id 沿用） |

### C 类：豁免 / 无租户列

| 表 | 处理 |
|---|---|
| provenance_edge | 豁免 RLS（D4） |
| object_relation | 跟随两端 object 可见性，应用层控制；不加列 |
| object_type_dict 等字典 | 全组织公共 → 挂 root 或豁免（倾向 root，规则统一） |
| department 自身 | 全树对登录用户可读（选择器需要），写按权限；不加 department_id 自引用列 |

### D 类：退役

- `organization` 表：阶段 3 DROP。
- 所有表 `organization_id` 列：阶段 3 统一 DROP。

---

## 5. RLS 与 GUC 改造

1. **GUC 改名**：`app.current_org_id` → `app.current_dept_id`。过渡期内（阶段 1–2）两个 GUC 并存，策略双写；切换后删旧 GUC。
2. **`packages/common/database.py`**：
   - 连接默认 `SET app.current_dept_id = ''`（fail closed 不变）；
   - `session_scope` 从 `principal.department_id` 设置 `SET LOCAL`。
3. **Principal**：`organization_id` 字段替换为 `department_id`（取用户 primary 部门快照，登录/refresh 时签发进 JWT claims）。
4. **策略模板**（A 类表）：

```sql
CREATE POLICY tenant_isolation ON <table> USING (
  department_id = current_setting('app.current_dept_id', true)::uuid
  OR visible_departments @> jsonb_build_array(current_setting('app.current_dept_id', true))
);
```

   B 类表去掉 OR 分支。root 用户（系统管理员）可见全部——通过给管理员角色发"root 部门 + 全树可见"的应用层语义实现，RLS 层可用单独的 bypass 策略角色（沿用 0034/0048 的 DB 角色体系），迁移时确定。
5. **`visible_departments` 建 GIN 索引**（`USING gin (visible_departments jsonb_path_ops)`）。

---

## 6. 应用层改造清单

### 后端

- `packages/common/database.py`：GUC 改名 + principal 字段（§5）。
- `packages/common/query_scope.py`：QueryScope 从 organization_id 切换为 department_id + 可见部门集。
- `apps/api/dependencies/dept_scope.py`：升级为全局可见性依赖（目前仅设备/对象路由使用）；`get_visible_department_ids` 成为所有列表查询的标配。
- 各 repository：写入强制 department_id；查询过滤从 org 换 dept + 白名单。
- auth：JWT claims 加 primary department_id；`/me` 返回部门集。
- worker（`apps/worker/celery_app.py`、`tasks/flows.py`）：payload 带 department_id；session_scope 设置 GUC；Beat 定时任务挂 root。

### 前端

- `api/client.ts`：`organizationId` → `departmentId`。
- `AuthProvider.tsx`：去掉 `'unknown'` fallback，改为 primary 部门。
- `useJobStore.ts`：存储键从 org+user 换 dept+user。
- 数据创建表单：多部门用户增加"归属部门"选择器（默认 primary）。
- 部门管理 UI：re-parent 二次确认 + 影响预览（P1）。

### 部署

- `deployments/compose/bootstrap.py`：删 organization 建表逻辑，改为幂等创建 root 部门；admin 用户挂 root。

---

## 7. 分阶段迁移计划

### 阶段 0：冻结（本 ADR）

- 评审 D1–D5，冻结表分类（§4）。
- 配套更新 docs/arch-department.md。

### 阶段 1：加列回填（双跑，不切换）

- 一个迁移批次：A/B 类表 `ADD COLUMN department_id UUID`（先 NULL）→ 按 §4 依据回填 → `SET NOT NULL`；需要共享的表加 `visible_departments` + GIN 索引。
- department 加 root 哨兵行；唯一约束改造。
- 应用层双写：新数据同时写 organization_id 和 department_id。
- **此阶段 RLS 仍锚 org，行为完全不变，可随时中止。**

### 阶段 2：切换

- Principal / GUC / 策略换锚（§5），应用查询切换。
- feature flag 或快速回滚迁移保障；worker 同步切换。
- 前端字段切换。

### 阶段 3：退役

- 观察期（建议 ≥1 个迭代）无回滚后：DROP 所有 organization_id 列、DROP organization 表、删旧 GUC 与双写代码。
- 清理 0032 策略定义中已删表的残留引用。

---

## 8. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| 回填归属错误 | 数据对原使用部门不可见 | 回填脚本输出逐表审计报告（每部门分得行数）；灰度期间提供管理员"数据移交"工具（批量改 department_id） |
| 应用层漏改查询 | 跨部门可见（软隔离期）或无数据（RLS 期） | RLS 切换后 DB 兜底，漏改表现为"看不到"而非"看到不该看的"——fail closed 方向安全 |
| re-parent 引发可见性地震 | 子树数据突然对另一分支可见 | D5：二次确认 + 审计 + （P1）影响预览 |
| root 部门被滥用为垃圾桶 | 公共数据膨胀、归属意识弱化 | root 挂数需管理员权限；治理页暴露 root 数据量 |
| 大表加列 + NOT NULL 锁表 | 迁移窗口长 | 先加 NULL 列回填再 SET NOT NULL；fact 等大表分批回填 |
| RLS 策略 JSONB 分支性能 | 列表查询变慢 | jsonb_path_ops GIN 索引；切换前后对 fact 列表做基准对比 |

## 9. 测试矩阵

- 单元：QueryScope dept 过滤；dept_scope 可见集递归；root 兜底解析。
- 集成：A/B 类表 RLS 策略（正/反例）；visible_departments 白名单生效；worker 无用户上下文写 root；re-parent 后可见性变化符合 D5。
- 迁移：回填正确性抽查（创建者 → primary 部门映射）；NOT NULL 约束；双写一致性。
- 回归：现有 8 个预存在单测失败需同步修复口径（auth 权限矩阵、AI tool 数量等）。

## 10. 开放问题

1. 管理员"全可见"在 RLS 层的实现形式：bypass 角色 vs 策略内角色判断——迁移 0034 的 DB 角色体系如何复用？
2. `department.code` 唯一约束改为同级唯一后，存量 code 冲突检查（当前按 org 唯一，实际等价于全局唯一，预计无冲突，需脚本验证）。
3. ai_conversation 协作场景（conversation_participant 跨部门 @提及）：会话归属部门 vs 参与者跨部门——参与者可见性走 participant 表还是 visible_departments？
4. root 部门的展示名与机构名配置（bootstrap 参数化，替代 IRIP-DEMO）。
