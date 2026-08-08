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
 ├── system（系统室，code='system'，哨兵节点：管理性数据专区，仅 root 成员可见）
 ├── 实验室 A
 │    └── 课题组 A1
 └── 实验室 B
```

**设计原则（2026-08-02 用户确立）：能不例外，就不例外。** 每条规则必须具名、有边界；看似特例的东西要么归入一条原则，要么删除。本 ADR 据此自检：管理员特权无特例（规则 3）；"仅管理员可见"无特例（系统室，D2）；溯源不再是"豁免"而是原则实例（规则 5）；**私有数据是全系统唯一的具名例外**，边界见规则 4。

五条核心规则：

1. **归属（ownership）**：每张数据表带 `department_id NOT NULL`，表示数据归属的部门。归属是稳定 FK 值，不随部门树调整而变化。
2. **可见性（visibility）= 对称层级 + 横向白名单**（2026-08-02 按用户"信任是相互的"原则修订）：
   - **① 向下穿透**：父部门自动可见所有子孙部门的数据（`行.department_id ∈ 当前部门 + 全部后代`，递归）。
   - **② 向上回溯**：每个部门可见自己直系祖先链（父、祖父…root）的数据——与①对称。
   - **推论：root 数据全员可见**。root 是所有部门祖先链的终点，由②自然推出——"公共大厅"不是特例，是对称原则的定理（D2/D3 的"全组织共享"通道由此而来）。
   - **③ 横向白名单**：旁系部门（兄弟、叔侄）之间唯一的横向通道，`visible_departments @> [当前部门]`，精确匹配、不随树扩散。
   - **语义提醒**：②意味着上级部门对本单位下级没有私有数据（课题组能看到实验室本级数据）；真正的隔离只存在于旁系之间。
   - **"仅本级可见"在树内无解**：对称模型下祖先链是链式透明区，数据存链上任何位置都无法对直系亲属保密（可见性由结构决定，不存在"往上存一级"之类的存放技巧）。部门级 local 扩展当时仅作预留；**用户级私有已确定落地，见规则 4**。
3. **管理员 = root 成员**：管理员不再是"无部门的特殊角色"，而是归属 root 的用户。root 的后代集 = 整棵树，由通道 ① 自动获得全量可见——**无需 is_admin 开关或 bypass 角色（开放问题 1 就此关闭）**。推论：**root 的成员资格就是系统最高可见权限**，加入 root 必须是受控操作（仅现有 root 成员可加人 + 审计），应用层设不变式校验。注意层级规则只管"可见性"，编辑/管理**操作权限仍由角色体系**（lab_director 等）决定，两者正交。
4. **个人私有数据（2026-08-02 用户新增需求，确定落地）**：A 类中面向用户发布的表（fact、parameter、evidence_set、artifact、model、transformation_recipe）增加两列：`visibility_scope TEXT NOT NULL DEFAULT 'tree'`（`'tree'` / `'private'`）+ `owner_user_id UUID NOT NULL`。
   - 发布表单提供"发布为私有"勾选框；私有数据**仅 owner 本人可见——包括 root 管理员在内的其他任何人都不可见**（RLS 强制，隐私语义强于管理便利，这是有意取舍）。
   - 私有数据详情页名称标红，操作栏提供"公开"按钮（二次确认，提示不可逆）；公开即 `visibility_scope: private → tree`。
   - **单向不可逆**：应用层只暴露"公开"动作；DB 层 BEFORE UPDATE 触发器禁止 `tree → private` 回退（RLS 管不住字段级状态转换，必须由触发器兜底）；公开操作写审计日志。
   - 私有数据仍写 `department_id`（创建时归属部门快照），公开后按该归属正常进入树规则；`owner_user_id` 一旦写入不可改（RLS WITH CHECK + 触发器双保险）。
   - **数据层无例外**：所有 A 类表（含设备/对象）一律加这两列、策略同形；设备/对象的 UI 暂不暴露"发布为私有"勾选是产品取舍（资产归部门不归个人），数据层不搞差异。
   - **AI 会话同构扩展（2026-08-02 定，开放问题 3 关闭）**：ai_conversation（+ ai_message 随父）**不进入部门树规则**——否则向上通道会让上级链翻看所有 AI 对话，构成非预期行为变更。可见者 = 创建者 + 参与者，`conversation_participant` 是**唯一**跨人共享通道（精确到人；不走 visible_departments，不对上下级开放）。RLS 策略在私有分支上扩展：`OR id IN (SELECT conversation_id FROM conversation_participant WHERE user_id = 当前用户)`；participant 表策略与之配合，用 SECURITY DEFINER 函数防两表策略互引用递归。未来可加"发布到部门"动作，与私有公开同构（单向不可逆）。
   - **副本语义（copy-on-share，具名规则）**：消息文本与橱窗块（ai_showcase_item.content_snapshot，代码注释明言"源数据更新不影响已存卡片"）均为**发送时副本**。把数据带进会话 = 向会话所有当前和未来参与者公开该数据的副本；副本**不随源数据权限撤销而回收、不随源数据更新而刷新**（"看过即拥有"，物理上无法避免，接受为语义而非漏洞）。与白名单共享严格区分：白名单共享活数据（按部门、可撤销、跟随更新），会话共享死副本（按人、不可回收、不跟随）。邀请新参与者、未来发布会话时，UI 须明确提示副本将随之暴露。
5. **结构数据全员可读**：department 树、provenance_edge、object_relation、类型字典是"结构"而非"内容"——只描述关系与分类，不含业务内容。结构对全体登录用户可读（选择器、谱系图需要），写入限管理员角色。结构表不带租户列、不设 RLS 策略。**这不是内容规则的例外，而是数据的二分类：内容按归属受控（规则 1/2/4），结构全员可读。**

---

## 3. 五个关键决策点

### D1. organization 退役方式

- bootstrap 改为创建 **root 部门**（`code='root'`，`parent_id=NULL`），不再创建 organization 表。
- `department.organization_id` 列删除；唯一约束 `uq_department_org_code (organization_id, code)` 改为 **`(parent_id, code)` 同级唯一**（NULL parent 用 `COALESCE` 表达式索引或允许 root 级 code 全局唯一，迁移时确定）。
- 所有业务表的 `organization_id` 列在最终阶段统一 DROP（见 §7 阶段 3）。
- 命名规则：**`code='root'` 是内部哨兵值**，程序锚点，创建后锁定、不出现在任何 UI；**`display_name` = 公司/机构名**（部署时由 bootstrap 环境变量参数化，替代原 IRIP-DEMO 种子，如 `IRIP_ROOT_DEPT_NAME`），管理员可随时改。部门树顶层显示机构名；数据归属选择器中显示为"公共（{机构名}）"，避免被当作普通实验室。

### D2. 无归属数据的去处

以下数据在写入时天然没有用户部门上下文：

- 管理员 / 系统创建的数据（字典、内置组件、预置流程）；
- Celery worker 执行 job / derivation / 定时任务产生的数据（当前 worker `session_scope(factory)` 无 principal，payload 只带 organization_id）。

**规则：所有写入路径必须能解析出 department_id；解析不到时按敏感度分两档兜底（2026-08-02 修订）。**

- **公共档 → 挂 root**：内置组件、预置流程、公共字典类内容。root 是全员祖先，这些数据本来就该人人可见，兜底语义与"公共大厅"一致。
- **敏感档 → 挂"系统室"**：secret、系统审计事件、备份记录、connector 配置等管理性数据。**root 数据全员可见，敏感数据绝不能挂 root**。系统室 = root 的直系子部门（`code='system'`，哨兵节点，无真实成员），其数据按向上通道仅 root 成员（管理员）可见——"仅管理员可见"用树结构本身表达，不引入任何特殊机制。
- API 请求：取当前用户的 primary department；用户多部门时，写入接口显式传 `department_id`（前端选择器），缺省 primary。
- Worker：任务 payload 增加 `department_id`（提交 job 时从提交者上下文快照），worker `session_scope` 用该值设置 GUC；定时任务（Beat 发起、无用户）按产出物敏感度挂 root 或系统室。
- 审计字段保留 `created_by`，兜底不影响追责。

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

### D4. 溯源链（provenance / derivation）

血缘关系天然跨部门：A 室的 parameter 可能从 B 室共享出来的 fact 推导。按"能不例外就不例外"原则重新定性（2026-08-02）：

- `provenance_edge` 属于**结构数据**（规则 5）：只存两端 ID 与关系类型，不含业务内容，全员可读——它不是内容规则的"豁免"，而是结构/内容二分类的实例。端点本身的可见性由端点表策略保证；查询谱系时，不可见端点显示为"无权限节点"占位而非断链。
- `derivation_run` 是内容数据，带 department_id（按执行者归属）正常受控；其输入/输出引用不做跨部门 FK 级校验，展示层处理占位。

### D5. 部门树调整与可见性稳定性

风险：层级穿透（通道①）落地后，re-parent 直接改变 RLS 层的可见范围——原父部门立即失去该子树数据的可见性，新父部门立即获得。**这是有意语义（"组织调整 = 权限调整"），但影响面从应用层放大到了数据库层。**

**规则：**

- **归属不动**：re-parent 只改 department.parent_id，任何数据行的 department_id 不变（数据不产生、不复制、不改归属）。
- **可见性随树实时生效**：RLS 策略按当前树计算，无需任何数据迁移；调树操作写入审计日志（谁、何时、把哪个子树从哪移动到哪、影响多少行数据的可见性）。
- **防误操作**：re-parent 非空子树需二次确认（前端），治理页提供"影响预览"（该子树下各部门数据量、原/新父部门受影响用户列表），影响预览可作为 P1 增强，不阻塞 v1。
- **哨兵保护**：root / system 两个哨兵节点不可 re-parent、不可禁用、不可删除（应用层 + CHECK/触发器双保险）。

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
| audit_event | actor 部门；系统事件 → 系统室 |
| scope_grant | 已有 department_id（授权范围功能列，NULL=全组织 → 改 root 哨兵语义）；记录本身归系统室口径 |
| secret | 系统室（平台级敏感） |
| backup_record | 系统室 |
| connector 各表 | 系统室 |
| app_user | 已有（organization_id 退役，department_id 沿用） |

### C 类：结构数据（全员可读，无租户列，规则 5）

| 表 | 处理 |
|---|---|
| provenance_edge | 结构数据，全员可读（D4） |
| object_relation | 结构数据，全员可读 |
| object_type_dict 等字典 | 结构数据，全员可读 |
| department 自身 | 结构数据，全员可读（选择器需要）；写按角色权限 |

### D 类：退役

- `organization` 表：阶段 3 DROP。
- 所有表 `organization_id` 列：阶段 3 统一 DROP。

---

## 5. RLS 与 GUC 改造

1. **GUC 改名与新增**：`app.current_org_id` → `app.current_dept_id`，新增 `app.current_user_id`（私有数据判主用）。过渡期内（阶段 1–2）新旧 GUC 并存，策略双写；切换后删旧 GUC。
2. **`packages/common/database.py`**：
   - 连接默认 `SET app.current_dept_id = ''`、`SET app.current_user_id = ''`（fail closed 不变）；
   - `session_scope` 从 `principal` 设置两个 `SET LOCAL`。
3. **Principal**：`organization_id` 字段替换为 `department_id`（取用户 primary 部门快照，登录/refresh 时签发进 JWT claims）。
4. **层级可见函数 + 策略模板**（2026-08-02 修订）：

```sql
-- 可见部门集 = 子孙全集（向下①）∪ 直系祖先链（向上②），一条递归 CTE 搞定；
-- root 公共语义由"root 是所有人祖先"自然覆盖，策略无需 root 特例分支
CREATE FUNCTION current_visible_dept_ids() RETURNS SETOF uuid
LANGUAGE sql SECURITY DEFINER STABLE AS $$
  WITH RECURSIVE down AS (
    SELECT id FROM department
    WHERE id = NULLIF(current_setting('app.current_dept_id', true), '')::uuid
    UNION ALL
    SELECT d.id FROM department d JOIN down s ON d.parent_id = s.id
  ), up AS (
    SELECT d.parent_id AS id FROM department d
    WHERE d.id = NULLIF(current_setting('app.current_dept_id', true), '')::uuid
      AND d.parent_id IS NOT NULL
    UNION ALL
    SELECT d.parent_id FROM department d JOIN up ON d.id = up.id
    WHERE d.parent_id IS NOT NULL
  ) SELECT id FROM down UNION SELECT id FROM up
$$;

-- A 类表策略：私有仅本人 OR 对称层级 OR 横向白名单
CREATE POLICY tenant_isolation ON <table> USING (
  (visibility_scope = 'private'
    AND owner_user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid)
  OR (visibility_scope = 'tree' AND (
    department_id IN (SELECT current_visible_dept_ids())
    OR visible_departments @> jsonb_build_array(current_setting('app.current_dept_id', true))
  ))
);

-- 私有单向阀：公开后禁止回到私有（应用层之外的数据库兜底）
CREATE FUNCTION forbid_reprivatize() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.visibility_scope = 'tree' AND NEW.visibility_scope = 'private' THEN
    RAISE EXCEPTION 'visibility_scope 不允许 tree → private 回退';
  END IF;
  IF NEW.owner_user_id IS DISTINCT FROM OLD.owner_user_id THEN
    RAISE EXCEPTION 'owner_user_id 不可修改';
  END IF;
  RETURN NEW;
END $$;
```

   B 类表去掉白名单分支。管理员全可见由"root 成员 + 通道①"自动达成，**不再需要 is_admin GUC 或 bypass 角色**；department 表自身策略为 `USING (id IN (SELECT current_visible_dept_ids()))`（登录用户可见自己子树，root 成员见全树）。
5. **`visible_departments` 建 GIN 索引**（`USING gin (visible_departments jsonb_path_ops)`）；`department.parent_id` 加索引（递归遍历用）。

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
- 私有数据三件套（规则 4）：发布表单"发布为私有"勾选框（默认不勾）；详情页私有数据名称**标红** + 操作栏"公开"按钮（确认弹窗明确提示**不可逆**）；列表页可选"私有"徽标（P1）。
- 部门管理 UI：re-parent 二次确认 + 影响预览（P1）。

### 部署

- `deployments/compose/bootstrap.py`：删 organization 建表逻辑，改为幂等创建 root + system 两个哨兵部门；admin 用户挂 root。

---

## 7. 分阶段迁移计划

### 阶段 0：冻结（本 ADR）

- 评审 D1–D5，冻结表分类（§4）。
- 配套更新 docs/arch-department.md。

### 阶段 1：加列回填（双跑，不切换）

- 一个迁移批次：A/B 类表 `ADD COLUMN department_id UUID`（先 NULL）→ 按 §4 依据回填 → `SET NOT NULL`；需要共享的表加 `visible_departments` + GIN 索引；规则 4 的 A 类表同步加 `visibility_scope`（默认 `'tree'`）+ `owner_user_id`（回填 = created_by/上传者）；ai_conversation 加私有+参与者策略（存量会话按创建者归属，可见性与现状一致）。
- department 加 root + system 哨兵行；唯一约束改造（改造前跑防御性冲突校验，发现冲突即中止并输出清单，开放问题 2）。
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
- 集成：A/B 类表 RLS 策略（正/反例）；visible_departments 白名单生效；私有数据仅 owner 可见（含 root 成员不可见的反例）；公开单向阀触发器（tree→private 报错、owner_user_id 不可改）；系统室数据普通用户不可见、root 成员可见；会话仅创建者+参与者可见（同部门非参与者、上级均不可见）；副本语义（参与者可见会话内副本，源表权限回收不影响）；worker 无用户上下文写 root/系统室；re-parent 后可见性变化符合 D5。
- 迁移：回填正确性抽查（创建者 → primary 部门映射）；NOT NULL 约束；双写一致性。
- 回归：现有 8 个预存在单测失败需同步修复口径（auth 权限矩阵、AI tool 数量等）。

## 10. 开放问题

1. ~~管理员"全可见"在 RLS 层的实现形式~~ **已定（2026-08-02）**：层级穿透规则下，管理员 = root 成员，经通道①自动全可见；无需 is_admin GUC 或 bypass 角色。root 成员资格成为最高可见权限，加入 root 须受控 + 审计（见 §2 规则 3）。
2. ~~`department.code` 唯一约束改为同级唯一后，存量 code 冲突检查~~ **已定（2026-08-02）**：按"无冲突"推进——现约束 `(organization_id, code)` 在单 org 下等价于全局唯一，同级唯一 `(parent_id, code)` 是**更弱**约束，存量数据逻辑上不可能冲突；阶段 1 迁移脚本附带防御性校验（发现冲突则中止并报告清单），不作为冻结阻塞项。
3. ~~ai_conversation 协作场景：参与者可见性走 participant 表还是 visible_departments~~ **已定（2026-08-02）**：走 participant 表按人授权，且为唯一通道——会话整体不进部门树规则（避免上级链翻看所有 AI 对话的非预期行为变更）；默认仅创建者+参与者可见，未来"发布到部门"与私有公开同构（单向阀）。副本语义（copy-on-share）同步确立，见 §2 规则 4。
4. ~~root 部门的展示名与机构名配置~~ **已定（2026-08-02）**：code='root' 内部哨兵不可改，display_name=公司/机构名，bootstrap 环境变量参数化，见 D1。
5. 私有数据的账号生命周期处置：用户离职/注销后，其私有数据按规则 4 仅本人可见 → 实际等于永久封存（连 root 管理员也看不到）。是否提供"管理员强制公开/移交"通道涉及隐私语义，单独评审；v1 默认不提供（封存），注销流程中对用户提示其私有数据将不可恢复。
