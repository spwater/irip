# 应用层部门过滤移除方案

## 背景

IRIP 项目有两层数据隔离：
1. **DB 层 RLS**：18 个有 department_id 的表全部启用 RLS（relforcerowsecurity=true），RLS 策略使用 `current_visible_dept_ids()` 函数
2. **应用层过滤**：Python 代码中用 `compute_visible_dept_ids()` + `department_id.in_()` 或 `_get_descendant_dept_ids()` 做 SQL WHERE 过滤

应用层过滤是 RLS 引入前的老代码，现在 RLS 已覆盖全部表，应用层过滤变成冗余的"双重过滤"，且对平台管理员造成可见性 bug（RLS 放行但应用层拦截）。

---

## 1. 三种过滤模式分类

### 模式 A：`compute_visible_dept_ids` + `department_id.in_()` — SQL WHERE 可见性过滤

这是最常见的模式，在 service/repository 层做 SQL WHERE 过滤，与 RLS 完全重复。

### 模式 B：`_get_descendant_dept_ids` + `department_id.in_()` — 子树过滤

在 equipment/experiment_project repository 中用递归 CTE 做子树展开过滤。既用于可见性过滤（默认 department_id = service.department_id），也用于用户指定的部门筛选。

### 模式 C：`should_filter_by_department` → 路由层 department_id 传给 service

在路由层判断用户角色，决定是否传 department_id 给 service 做过滤。

---

## 2. 移除清单（按文件）

### 2.1 packages/standards/objects/object_graph.py — 模式 A（4 处）

| # | 方法 | 行号 | 用途 | 移除方式 |
|---|------|------|------|----------|
| 1 | `add_object` | L90 | 编码唯一性检查：`compute_visible_dept_ids` → `IndustrialObject.department_id.in_(visible_ids)` | **简化**：删除 `compute_visible_dept_ids` + `.in_()`，保留唯一性检查本身（RLS 自动过滤可见范围） |
| 2 | `get_object_by_code` | L265 | 按编码查找：`compute_visible_dept_ids` → `IndustrialObject.department_id.in_(visible_ids)` | **直接删**：删除 `compute_visible_dept_ids` + `.in_()`，RLS 自动过滤 |
| 3 | `list_objects` | L345 | 列表过滤：`compute_visible_dept_ids` → `query.where(IndustrialObject.department_id.in_(visible_ids))` | **直接删**：删除 `compute_visible_dept_ids` + `.in_()`，RLS 自动过滤 |
| 4 | `_get_and_check_org` | L391 | 单条可见性检查：`compute_visible_dept_ids` → `if obj.department_id not in visible_ids: raise not_found` | **简化**：删除可见性检查，仅保留 `obj is None → not_found`（RLS 保证不可见行查不到） |

### 2.2 packages/facts/repository.py — 模式 A（3 处）

| # | 方法 | 行号 | 用途 | 移除方式 |
|---|------|------|------|----------|
| 1 | `get_fact` | L224 | 单条可见性检查：`compute_visible_dept_ids` → `if fact.department_id not in visible_ids: raise not_found` | **简化**：删除可见性检查，保留 `fact is None → not_found` |
| 2 | `search_facts` | L276 | 搜索过滤：`Fact.department_id.in_(await compute_visible_dept_ids(...))` | **直接删**：删除 `.in_()` 条件 |
| 3 | `list_facts` | L361 | 列表过滤：`Fact.department_id.in_(await compute_visible_dept_ids(...))` | **直接删**：删除 `.in_()` 条件 |

### 2.3 packages/facts/service.py — 模式 A（1 处）

| # | 方法 | 行号 | 用途 | 移除方式 |
|---|------|------|------|----------|
| 1 | `create` | L186 | 创建事实时校验工业对象存在：`compute_visible_dept_ids` → `IndustrialObject.department_id.in_(visible_ids)` | **简化**：删除 `compute_visible_dept_ids` + `.in_()`，保留对象存在校验（RLS 自动过滤） |

### 2.4 packages/provenance/evidence.py — 模式 A（4 处）

| # | 方法 | 行号 | 用途 | 移除方式 |
|---|------|------|------|----------|
| 1 | `freeze` | L184 | 加载证据集：`compute_visible_dept_ids` → `EvidenceSet.department_id.in_(visible_ids)` | **简化**：删除 `.in_()`，保留 `is None → not_found` |
| 2 | `freeze` | L212 | 查询活跃事实：`Fact.department_id.in_(visible_ids)` | **直接删**：删除 `.in_()` 条件 |
| 3 | `get_set` | L279 | 加载证据集：`compute_visible_dept_ids` → `EvidenceSet.department_id.in_(visible_ids)` | **简化**：删除 `.in_()`，保留 `is None → not_found` |
| 4 | `list_members` | L329 | 校验证据集存在：`compute_visible_dept_ids` → `EvidenceSet.department_id.in_(visible_ids)` | **简化**：删除 `.in_()`，保留 `is None → not_found` |

### 2.5 packages/provenance/recipes.py — 模式 A（4 处）

| # | 方法 | 行号 | 用途 | 移除方式 |
|---|------|------|------|----------|
| 1 | `create_recipe` | L121 | 编码唯一性检查：`compute_visible_dept_ids` → `TransformationRecipe.department_id.in_(visible_ids)` | **简化**：删除 `.in_()`，保留唯一性检查 |
| 2 | `publish_version` | L205 | 加载配方：`compute_visible_dept_ids` → `TransformationRecipe.department_id.in_(visible_ids)` | **简化**：删除 `.in_()`，保留 `is None → not_found` |
| 3 | `get_recipe` | L283 | 加载配方：`compute_visible_dept_ids` → `TransformationRecipe.department_id.in_(visible_ids)` | **简化**：删除 `.in_()`，保留 `is None → not_found` |
| 4 | `list_recipes` | L338 | 列表过滤：`compute_visible_dept_ids` → `TransformationRecipe.department_id.in_(visible_ids)` | **直接删**：删除 `.in_()` 条件 |

### 2.6 packages/provenance/graph.py — 模式 A（4 处）

| # | 方法 | 行号 | 用途 | 移除方式 |
|---|------|------|------|----------|
| 1 | `get_graph` | L143 | 加载推导运行：`compute_visible_dept_ids` → `DerivationRun.department_id.in_(visible_ids)` | **简化**：删除 `.in_()`，保留 `is None → not_found` |
| 2 | `get_graph` | L173 | 过滤溯源边：`ProvenanceEdge.department_id.in_(visible_ids)` | **直接删**：删除 `.in_()` 条件 |
| 3 | `get_paths_to_raw` | L245 | 过滤溯源边（起始）：`compute_visible_dept_ids` → `ProvenanceEdge.department_id.in_(visible_ids)` | **直接删**：删除 `.in_()` 条件 |
| 4 | `get_paths_to_raw` | L318 | 过滤溯源边（向上遍历）：`ProvenanceEdge.department_id.in_(visible_ids)` | **直接删**：删除 `.in_()` 条件 |

### 2.7 packages/provenance/derivations.py — 模式 A（2 处）

| # | 方法 | 行号 | 用途 | 移除方式 |
|---|------|------|------|----------|
| 1 | `get_run` | L382 | 加载推导运行：`compute_visible_dept_ids` → `DerivationRun.department_id.in_(visible_ids)` | **简化**：删除 `.in_()`，保留 `is None → not_found` |
| 2 | `list_runs` | L424 | 列表过滤：`compute_visible_dept_ids` → `DerivationRun.department_id.in_(visible_ids)` | **直接删**：删除 `.in_()` 条件 |

### 2.8 packages/models/service.py — 模式 A（3 处）

| # | 方法 | 行号 | 用途 | 移除方式 |
|---|------|------|------|----------|
| 1 | `create_model` | L146 | 编码唯一性检查：`compute_visible_dept_ids` → `Model.department_id.in_(visible_ids)` | **简化**：删除 `.in_()`，保留唯一性检查 |
| 2 | `list_models` | L661 | 列表过滤：`compute_visible_dept_ids` → `Model.department_id.in_(visible_ids)` | **直接删**：删除 `.in_()` 条件 |
| 3 | `_get_model_owned` | L697 | 加载模型：`compute_visible_dept_ids` → `Model.department_id.in_(visible_ids)` | **简化**：删除 `.in_()`，保留 `is None → not_found`（此方法被多个方法调用，修改后所有调用者自动生效） |

### 2.9 packages/parameters/service.py — 模式 A（9 处）

| # | 方法 | 行号 | 用途 | 移除方式 |
|---|------|------|------|----------|
| 1 | `create_parameter` | L146 | 唯一性检查：`compute_visible_dept_ids` → `Parameter.department_id.in_(visible_ids)` | **简化**：删除 `.in_()`，保留唯一性检查 |
| 2 | `create_candidate` | L222 | 验证推导运行：`compute_visible_dept_ids` → `DerivationRun.department_id.in_(visible_ids)` | **简化**：删除 `.in_()`，保留 `is None → not_found` |
| 3 | `create_candidate` | L253 | 验证参数存在：`Parameter.department_id.in_(visible_ids)` | **简化**：删除 `.in_()`，保留 `is None → not_found` |
| 4 | `approve` | L388 | 验证推导运行：`DerivationRun.department_id.in_(visible_ids)` | **简化**：删除 `.in_()`，保留 `is None → not_found` |
| 5 | `reject` | L554 | 验证候选归属：JOIN Parameter → `Parameter.department_id.in_(visible_ids)` | **简化**：删除 `.in_()` 条件 |
| 6 | `get_parameter` | L636 | 加载参数：`Parameter.department_id.in_(visible_ids)` | **简化**：删除 `.in_()`，保留 `is None → not_found` |
| 7 | `get_version` | L689 | 校验参数存在：`Parameter.department_id.in_(visible_ids)` | **简化**：删除 `.in_()`，保留 `is None → not_found` |
| 8 | `list_parameters` | L767 | 列表过滤：`Parameter.department_id.in_(visible_ids)` | **直接删**：删除 `.in_()` 条件 |
| 9 | `deprecate` | L885 | 加载参数：`Parameter.department_id.in_(visible_ids)` | **简化**：删除 `.in_()`，保留 `is None → not_found` |

### 2.10 packages/jobs/service.py — 模式 A（4 处）

| # | 方法 | 行号 | 用途 | 移除方式 |
|---|------|------|------|----------|
| 1 | `request_cancel` | L186 | 租户隔离检查：`compute_visible_dept_ids` → `if job.department_id not in visible_ids: raise not_found` | **简化**：删除可见性检查，保留 `job is None → not_found` |
| 2 | `get` | L261 | 租户隔离检查：`if job.department_id != self._dept_id: raise not_found`（精确匹配，非 visible_ids） | **简化**：删除可见性检查，保留 `job is None → not_found` |
| 3 | `list` | L329 | 列表过滤：`compute_visible_dept_ids` → `Job.department_id.in_(visible_ids)` | **直接删**：删除 `.in_()` 条件 |
| 4 | `get_raw` | L413 | 租户隔离检查：`compute_visible_dept_ids` → `if job.department_id not in visible_ids: raise not_found` | **简化**：删除可见性检查，保留 `job is None → not_found` |

### 2.11 packages/common/artifacts.py — 模式 A（6 处）

| # | 方法 | 行号 | 用途 | 移除方式 |
|---|------|------|------|----------|
| 1 | `verify` | L319 | 加载工件：`compute_visible_dept_ids` → `Artifact.department_id.in_(visible_ids)` | **简化**：删除 `.in_()`，保留 `is None → not_found` |
| 2 | `get_artifact` | L358 | 加载工件：`compute_visible_dept_ids` → `Artifact.department_id.in_(visible_ids)` | **简化**：删除 `.in_()`，保留 `is None → not_found` |
| 3 | `get_bytes` | L403 | 加载工件：`compute_visible_dept_ids` → `Artifact.department_id.in_(visible_ids)` | **简化**：删除 `.in_()`，保留 `is None → not_found` |
| 4 | `delete_artifact` | L435 | 加载工件：`compute_visible_dept_ids` → `Artifact.department_id.in_(visible_ids)` | **简化**：删除 `.in_()` |
| 5 | `presign_download` | L639 | 加载工件：`compute_visible_dept_ids` → `Artifact.department_id.in_(visible_ids)` | **简化**：删除 `.in_()`，保留 `is None → not_found` |
| 6 | `open_stream` | L679 | 加载工件：`compute_visible_dept_ids` → `Artifact.department_id.in_(visible_ids)` | **简化**：删除 `.in_()`，保留 `is None → not_found` |

### 2.12 packages/connectors/mapping.py — 模式 A（1 处）

| # | 方法 | 行号 | 用途 | 移除方式 |
|---|------|------|------|----------|
| 1 | `SecretStore.get` | L73 | 加载密钥：`compute_visible_dept_ids` → `Secret.department_id.in_(visible_ids)` | **简化**：删除 `.in_()`，保留 `is None → not_found` |

### 2.13 packages/components/flow/flow_runtime.py — 模式 A（14 处）

| # | 方法 | 行号 | 用途 | 移除方式 |
|---|------|------|------|----------|
| 1 | `create_definition` | L648 | 编码唯一性检查：`compute_visible_dept_ids` → `FlowDefinition.department_id.in_(visible_ids)` | **简化**：删除 `.in_()`，保留唯一性检查 |
| 2 | `publish_version` | L772 | 加载流程定义：`FlowDefinition.department_id.in_(visible_ids)` | **简化**：删除 `.in_()`，保留 `is None → not_found` |
| 3 | `list_definitions` | L831 | 列表过滤：`FlowDefinition.department_id.in_(visible_ids)` | **直接删**：删除 `.in_()` 条件 |
| 4 | `get_definition` | L868 | 加载流程定义：`FlowDefinition.department_id.in_(visible_ids)` | **简化**：删除 `.in_()`，保留 `is None → not_found` |
| 5 | `deprecate_definition` | L905 | 加载流程定义：`FlowDefinition.department_id.in_(visible_ids)` | **简化**：删除 `.in_()`，保留 `is None → not_found` |
| 6 | `restore_definition` | L939 | 加载流程定义：`FlowDefinition.department_id.in_(visible_ids)` | **简化**：删除 `.in_()`，保留 `is None → not_found` |
| 7 | `get_definition_by_id` | L975 | 加载流程版本：`FlowDefinition.department_id.in_(visible_ids)` | **简化**：删除 `.in_()`，保留 `is None → not_found` |
| 8 | `execute` | L1101 | 加载执行记录：`FlowRun.department_id.in_(visible_ids)` | **简化**：删除 `.in_()`，保留 `is None → not_found` |
| 9 | `resume` | L1351 | 加载执行记录：`FlowRun.department_id.in_(visible_ids)` | **简化**：删除 `.in_()`，保留 `is None → not_found` |
| 10 | `cancel` | L1527 | 加载执行记录：`FlowRun.department_id.in_(visible_ids)` | **简化**：删除 `.in_()`，保留 `is None → not_found` |
| 11 | `retry_node` | L1567 | 加载执行记录：`FlowRun.department_id.in_(visible_ids)` | **简化**：删除 `.in_()`，保留 `is None → not_found` |
| 12 | `get_run` | L1748 | 加载执行记录：`FlowRun.department_id.in_(visible_ids)` | **简化**：删除 `.in_()`，保留 `is None → not_found` |
| 13 | `delete_run` | L1783 | 加载执行记录：`FlowRun.department_id.in_(visible_ids)` | **简化**：删除 `.in_()` |
| 14 | `delete_flow` | L1825 | 加载流程定义：`FlowDefinition.department_id.in_(visible_ids)` | **简化**：删除 `.in_()` |

### 2.14 packages/components/registry/registry.py — 模式 A（11 处）

| # | 方法 | 行号 | 用途 | 移除方式 |
|---|------|------|------|----------|
| 1 | `publish` | L291 | 查找已有组件：`Component.department_id.in_(visible_ids)` | **简化**：删除 `.in_()` |
| 2 | `get` | L419 | 加载组件：`Component.department_id.in_(visible_ids)` | **简化**：删除 `.in_()`，保留 `is None → not_found` |
| 3 | `get_latest` | L466 | 加载组件：`Component.department_id.in_(visible_ids)` | **简化**：删除 `.in_()`，保留 `is None → not_found` |
| 4 | `get_version_by_id` | L525 | 加载组件版本：`Component.department_id.in_(visible_ids)` | **简化**：删除 `.in_()`，保留 `is None → not_found` |
| 5 | `get_by_component_id` | L563 | 加载组件版本：`Component.department_id.in_(visible_ids)` | **简化**：删除 `.in_()`，保留 `is None → not_found` |
| 6 | `list` | L610 | 列表过滤：`Component.department_id.in_(visible_ids)` | **直接删**：删除 `.in_()` 条件 |
| 7 | `list` | L641 | 二次过滤：`c.department_id in visible_ids` | **直接删**：删除 Python 层过滤 |
| 8 | `deprecate` | L682 | 加载组件：`Component.department_id.in_(visible_ids)` | **简化**：删除 `.in_()`，保留 `is None → not_found` |
| 9 | `restore` | L715 | 加载组件：`Component.department_id.in_(visible_ids)` | **简化**：删除 `.in_()`，保留 `is None → not_found` |
| 10 | `activate_version` | L756 | 加载版本：JOIN Component → `Component.department_id.in_(visible_ids)` | **简化**：删除 `.in_()`，保留 `is None → not_found` |
| 11 | `delete_component` | L800 | 加载组件：`Component.department_id.in_(visible_ids)` | **简化**：删除 `.in_()`，保留 `is None → not_found` |

### 2.15 packages/equipment/repository.py — 模式 B（2 处调用 + 函数定义）

| # | 方法 | 行号 | 用途 | 移除方式 |
|---|------|------|------|----------|
| 1 | `select_list` | L135 | `_get_descendant_dept_ids` 用于 department_id + visible_dept_id 的 OR 过滤 | **保留**：这是用户指定的部门筛选（当用户传入 department_id 参数时），不是可见性过滤。但当 department_id 默认为 service.department_id 时（should_filter_by_department=true 的场景），它是可见性过滤，应随模式 C 一起移除 |
| 2 | `select_list` | L144 | `_get_descendant_dept_ids` 用于仅 department_id 的过滤 | **保留**：同上 |

**注意**：`_get_descendant_dept_ids` 函数本身**保留**，因为 `check_management_permission` 也使用它做权限检查。

### 2.16 packages/experiment_project/repository.py — 模式 B（2 处调用 + 函数定义）

| # | 方法 | 行号 | 用途 | 移除方式 |
|---|------|------|------|----------|
| 1 | `select_list` | L136 | `_get_descendant_dept_ids` 用于 department_id + visible_dept_id 的 OR 过滤 | **保留**：同 equipment/repository.py 逻辑 |
| 2 | `select_list` | L147 | `_get_descendant_dept_ids` 用于仅 department_id 的过滤 | **保留**：同上 |

### 2.17 apps/api/routers/flows.py — 模式 A（2 处）

| # | 方法 | 行号 | 用途 | 移除方式 |
|---|------|------|------|----------|
| 1 | `delete_flow` | L549 | 归属检查前查询：`compute_visible_dept_ids` → `FlowDefinition.department_id.in_(visible_ids)` | **简化**：删除 `.in_()`，保留 `is None → not_found`（后续 `check_management_permission` 做权限控制） |
| 2 | `update_flow` | L598 | 归属检查前查询：`compute_visible_dept_ids` → `FlowDefinition.department_id.in_(visible_ids)` | **简化**：删除 `.in_()`，保留 `is None → not_found` |

### 2.18 apps/api/routers/components.py — 模式 A（1 处）

| # | 方法 | 行号 | 用途 | 移除方式 |
|---|------|------|------|----------|
| 1 | `publish_component` | L269 | 校验实验对象编码存在：`compute_visible_dept_ids` → `IndustrialObject.department_id.in_(visible_ids)` | **简化**：删除 `.in_()`，保留 `is None → validation_failed` |

### 2.19 apps/api/routers/objects.py — 模式 A（0 处）

路由层无 `compute_visible_dept_ids` 调用，可见性由 service 层处理。

### 2.20 apps/api/routers/equipment.py — 模式 C（1 处）

| # | 方法 | 行号 | 用途 | 移除方式 |
|---|------|------|------|----------|
| 1 | `list_equipment` | L235 | `should_filter_by_department(current_user)` → 设置 dept_id 和 visible_dept_id | **简化**：移除 `should_filter_by_department` 分支，统一传 `dept_id = UUID(department_id) if department_id else None`，`visible_dept_id = None`。RLS 自动处理可见性 |

### 2.21 apps/api/routers/experiment_projects.py — 模式 C（1 处）

| # | 方法 | 行号 | 用途 | 移除方式 |
|---|------|------|------|----------|
| 1 | `list_projects` | L273 | `should_filter_by_department(current_user)` → 设置 dept_id 和 visible_dept_id | **简化**：同 equipment.py |

### 2.22 packages/departments/service.py — 模式 A（0 处）

无 `compute_visible_dept_ids` 调用。部门管理不需要按部门隔离（部门表未启用 RLS）。

### 2.23 packages/experiment_project/service.py — 模式 A（0 处）

无 `compute_visible_dept_ids` 调用（仅有未使用的 import）。可见性由 repository 层的 `_get_descendant_dept_ids` 处理（模式 B）。

---

## 3. 保留清单（不能移除的 department_id 使用）

### 3.1 编码唯一性检查（业务逻辑，不是可见性过滤）

| 文件 | 方法 | 代码 | 原因 |
|------|------|------|------|
| equipment/repository.py | `select_by_org_and_code` | `Equipment.department_id == department_id` | 精确匹配，编码唯一性是部门内业务约束 |
| experiment_project/repository.py | `select_by_dept_and_code` | `ExperimentProject.department_id == department_id` | 精确匹配，编码唯一性是部门内业务约束 |

### 3.2 创建数据时设置 department_id（写入逻辑）

所有 `department_id=self._dept_id` 或 `department_id=department_id or self._dept_id` 的写入操作**全部保留**。

涉及文件（不完全列表）：
- object_graph.py: `add_object` → `department_id=department_id or self._dept_id`
- evidence.py: `create_set` → `department_id=self._dept_id`
- recipes.py: `create_recipe` → `department_id=self._dept_id`
- derivations.py: `create_run` → `department_id=self._dept_id`
- models/service.py: `create_model` → `department_id=self._dept_id`
- parameters/service.py: `create_parameter` → `department_id=self._dept_id`
- jobs/service.py: `accept` → `department_id=self._dept_id`
- artifacts.py: `put_bytes` → `department_id=self._dept_id`
- flow_runtime.py: `create_definition` → `department_id=department_id or self._dept_id`
- registry.py: `publish` → `department_id=department_id or self._dept_id`
- graph.py: `add_edge` → `department_id=self._dept_id`
- parameters/service.py: `approve` → `ProvenanceEdge(department_id=self._dept_id, ...)`

### 3.3 管理权限检查（权限控制，不是可见性过滤）

| 文件 | 方法/函数 | 原因 |
|------|-----------|------|
| dept_scope.py | `check_management_permission` | 管理权单向向下（所有者+上级），与可见性过滤不同 |
| dept_scope.py | `should_filter_by_department` | 用于 `check_management_permission` 内部判断 root 成员，保留 |
| dept_scope.py | `can_edit_department` | 同步快查，用于权限判断 |
| dept_scope.py | `can_reparent_department` | 哨兵保护检查 |
| dept_scope.py | `check_is_root_member` | 判断 root 成员身份 |
| 所有路由中的 `check_management_permission` 调用 | — | 管理权限，保留 |

### 3.4 幂等键查找（精确匹配，业务逻辑）

| 文件 | 方法 | 代码 | 原因 |
|------|------|------|------|
| facts/repository.py | `find_by_idempotency_key` | `Fact.department_id == org_id` | 精确匹配，同部门+同幂等键 = 同一事实 |
| jobs/service.py | `accept` → `JobRepository.get_by_idempotency_dept` | `Job.department_id == dept_id` | 精确匹配，同部门+同幂等键 = 同一作业 |

### 3.5 `_get_descendant_dept_ids` 函数（保留）

`_get_descendant_dept_ids` 函数定义保留，因为：
1. `check_management_permission` 使用它做权限检查（严格后代判断）
2. equipment/experiment_project repository 的 `select_list` 使用它做用户指定的部门筛选

### 3.6 `visible_departments` JSONB 列过滤（需评估）

equipment/repository.py 和 experiment_project/repository.py 的 `select_list` 方法中有 `Entity.visible_departments.contains([str(visible_dept_id)])` 过滤。这是跨实验室可见性特性（`visible_departments` JSONB 列），需确认 RLS 策略是否已覆盖此列。

**如果 RLS 策略包含 `visible_departments @> ARRAY[current_dept_id]` 条件**，则此过滤可移除。
**如果 RLS 策略仅检查 `department_id IN current_visible_dept_ids()`**，则此过滤需保留。

建议：查看 RLS 策略定义确认。若确认 RLS 覆盖，则随模式 C 移除 `visible_dept_id` 参数。

---

## 4. 风险点

### 4.1 `compute_visible_dept_ids` 的副作用：设置 GUC

`compute_visible_dept_ids` 函数不仅计算可见部门集合，还会设置 GUC（`set_dept_guc` + `set_user_guc`）。移除调用后，需确保 `_scoped_session()` 已正确设置 GUC。

**确认**：根据前提条件，`ScopedSessionMixin._scoped_session()` 全部通过 GUC 设置。因此移除 `compute_visible_dept_ids` 不影响 GUC 设置。

### 4.2 编码唯一性检查的范围变化

移除 `department_id.in_(visible_ids)` 后，编码唯一性检查变为"RLS 可见范围内的唯一性"。由于 RLS 和 `compute_visible_dept_ids` 使用相同的 `current_visible_dept_ids()` 函数，结果应一致。

**风险**：如果存在 `compute_visible_dept_ids` 的退路路径（无 actor_id 时走 dept_id 递归），结果可能与 RLS 略有不同。但前提条件确认 Worker 路径全部正确设置 GUC，API Service 全部通过 `_scoped_session()` 设置 GUC，因此不会触发退路。

### 4.3 平台管理员可见性

移除应用层过滤后，平台管理员的可见性完全由 RLS 控制。RLS 通过 root 部门挂载放行全部数据。需确认 root 部门成员的 RLS 策略确实放行全部行。

**确认**：根据前提条件，`should_filter_by_department` 返回 False 的用户（root 成员/平台管理员），RLS 通过 root 挂载放行全部。

### 4.4 `visible_departments` JSONB 列过滤

如果 RLS 策略未覆盖 `visible_departments` 列，移除 `visible_dept_id` 参数会导致跨实验室可见性失效。需确认 RLS 策略。

### 4.5 `_get_descendant_dept_ids` 保留但行为变化

模式 C 移除后，equipment/experiment_project 的 `select_list` 方法不再收到默认的 `department_id = service.department_id`。当用户不传 `department_id` 参数时，`department_id` 为 None，`_get_descendant_dept_ids` 不会被调用，列表不做部门过滤，完全依赖 RLS。这是预期行为。

### 4.6 级联效应

`_get_model_owned` 是 `ModelService` 的内部方法，被 `create_version`、`submit_for_validation`、`validate`、`publish`、`rollback`、`deprecate`、`predict`、`get_model` 等 8+ 个方法调用。修改此方法后所有调用者自动生效，需确保测试覆盖。

### 4.7 flow_runtime 中 execute/resume/cancel/retry_node 的 worker 路径

这些方法在 API 路径和 Worker 路径都可能被调用。Worker 路径通过 `set_dept_guc + set_user_guc` 设置 GUC，RLS 正确生效。移除应用层过滤后，Worker 路径的可见性完全由 RLS 保证。

---

## 5. 执行顺序

### 阶段 1：packages 层移除（先底层后上层）

按依赖顺序，先修改被依赖的模块：

1. **packages/common/artifacts.py**（6 处）— 被多个服务依赖
2. **packages/facts/repository.py**（3 处）+ **packages/facts/service.py**（1 处）— 被证明层和模型层依赖
3. **packages/standards/objects/object_graph.py**（4 处）— 被组件路由和事实服务依赖
4. **packages/provenance/evidence.py**（4 处）+ **recipes.py**（4 处）+ **graph.py**（4 处）+ **derivations.py**（2 处）
5. **packages/models/service.py**（3 处）
6. **packages/parameters/service.py**（9 处）
7. **packages/jobs/service.py**（4 处）
8. **packages/connectors/mapping.py**（1 处）
9. **packages/components/registry/registry.py**（11 处）
10. **packages/components/flow/flow_runtime.py**（14 处）

### 阶段 2：路由层移除

11. **apps/api/routers/flows.py**（2 处）
12. **apps/api/routers/components.py**（1 处）
13. **apps/api/routers/equipment.py**（1 处，模式 C）
14. **apps/api/routers/experiment_projects.py**（1 处，模式 C）

### 阶段 3：清理

15. 移除各文件中未使用的 `compute_visible_dept_ids` import
16. `compute_visible_dept_ids` 函数标记为 deprecated（保留函数定义，因为 `check_management_permission` 间接依赖 `_get_descendant_dept_ids`）

---

## 6. 移除统计

| 分类 | 文件数 | 移除/简化处数 |
|------|--------|-------------|
| 模式 A（compute_visible_dept_ids + .in_） | 15 个 packages 文件 | ~71 处 |
| 模式 A（路由层） | 2 个 router 文件 | 3 处 |
| 模式 B（_get_descendant_dept_ids） | 2 个 repository 文件 | 保留（用户筛选 + 权限检查） |
| 模式 C（should_filter_by_department） | 2 个 router 文件 | 2 处 |
| **合计** | **19 个文件** | **~76 处移除/简化** |

保留不动的文件：
- packages/departments/service.py（0 处应用层过滤）
- packages/experiment_project/service.py（0 处应用层过滤，仅有未使用 import）
- apps/api/routers/objects.py（0 处应用层过滤）
- apps/api/dependencies/dept_scope.py（`should_filter_by_department` 和 `check_management_permission` 保留）
