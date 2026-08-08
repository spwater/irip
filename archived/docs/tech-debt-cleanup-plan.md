# 技术债清理方案

> 目标：14 个后端测试失败 + 15 个前端 TS 错误 → 全部清零
> 预计工作量：半天
> 路径：`/Users/shuipei/Desktop/snowSP/irip/`

---

## 一、后端测试修复（14 个失败）

### 第 1 组：IDOR 测试（4 个失败）— org→dept 迁移遗留

**文件**：`tests/unit/test_idor_fix.py`
**失败类**：`TestEquipmentRepositoryIdorFix`
**失败测试**：
1. `test_select_by_id_includes_org_filter` — 调用 `select_by_id(session, id, org_id)` 3 参数，但签名只有 2 参数
2. `test_select_by_id_wrong_org_returns_none` — 同上
3. `test_update_status_includes_org_filter` — 调用传 `department_id=` kwarg，但方法不接受
4. `test_select_by_id_signature_has_org_param` — 检查签名有 `department_id` 参数，但实际没有

**根因**：Equipment repository 的 `select_by_id` 和 `update_status` 方法在 org→dept 迁移后移除了 org 过滤参数（改为 RLS 在 session 级别处理），但测试还在验证旧的签名和行为。

**修复方案**：这 4 个测试验证的是"显式 org 过滤"模式，现在已改为 RLS 隐式过滤。应该**删除这 4 个测试**（或改为验证 RLS 生效），因为显式过滤的设计已经不存在了。

**具体操作**：
```python
# tests/unit/test_idor_fix.py
# 删除 TestEquipmentRepositoryIdorFix 类中这 4 个方法：
# - test_select_by_id_includes_org_filter
# - test_select_by_id_wrong_org_returns_none
# - test_update_status_includes_org_filter
# - test_select_by_id_signature_has_org_param
# 保留该类中其他 8 个通过的测试
```

---

### 第 2 组：权限矩阵测试（3 个失败）— 新增权限未同步到测试

**文件**：`tests/unit/auth/test_permissions.py`
**失败类**：`TestRolePermissionMatrix`
**失败测试**：
1. `test_lab_director_permissions` — 期望权限集合 ≠ 实际
2. `test_lab_member_permissions` — 同上
3. `test_lab_viewer_permissions` — 同上

**根因**：新增了 `experiment_project:manage`、`experiment_project:read` 等权限，但测试中硬编码的期望权限集合没有更新。

**修复方案**：更新测试中 3 个角色的期望权限集合，加入新增的权限。

**具体操作**：
- 读取 `packages/auth/permissions.py` 中 `lab_director`、`lab_member`、`lab_viewer` 三个角色的实际权限定义
- 更新 `test_permissions.py` 中对应的 `expected` 集合

```python
# 查看 packages/auth/permissions.py 中 ROLE_PERMISSIONS 定义
# 把测试里的 expected sets 更新为与代码一致
```

---

### 第 3 组：权限总数测试（1 个失败）— 计数过时

**文件**：`tests/unit/auth/test_department_permissions.py`
**失败测试**：`test_total_permission_count`
**错误**：`assert len(Permission.all()) == 49`，实际 51

**根因**：新增了 2 个权限（`experiment_project:manage` + `experiment_project:read`），总数从 49 变为 51。

**修复方案**：改一行。

```python
# tests/unit/auth/test_department_permissions.py:95
assert len(Permission.all()) == 51  # 原来是 49，加了 experiment_project:manage + experiment_project:read
```

---

### 第 4 组：AI 工具管理测试（3 个失败）— 工具数量变化

**文件**：`tests/unit/ai/test_tool_management.py`
**失败测试**：
1. `test_all_tools_count_is_12` — `assert 15 == 13`（实际 15 个工具，期望 13）
2. `test_reload_rebuilds_tools_and_enabled` — mock 的工具数量与实际不符
3. `test_reload_replaces_previous_state` — 同上

**根因**：`packages/ai/tools.py` 的 `PLUGIN_TOOLS` 新增了工具（converter 插件化后从 6 合并为 2，但总数变了），测试硬编码的数量没更新。

**修复方案**：
- 读取 `packages/ai/tools.py` 的 `PLUGIN_TOOLS` 确认实际工具数量
- 更新测试中的期望数量（从 13 改为 15）

```python
# tests/unit/ai/test_tool_management.py
# test_all_tools_count_is_12:
#   assert len(tools) == 15  # 原 13，改名 test_all_tools_count_is_15
# test_reload_rebuilds_tools_and_enabled / test_reload_replaces_previous_state:
#   更新 mock 返回的工具列表数量
```

---

### 第 5 组：AI 工具策略测试（3 个失败）

**文件**：`tests/unit/ai/test_tool_policy.py`
**失败测试**：
1. `test_all_tools_is_union` — 工具集合并集不匹配
2. `test_default_registry_has_all_tools` — 注册表工具数不匹配
3. `test_tool_names_returned` — 工具名称列表不匹配

**根因**：与第 4 组相同，工具数量/名称变化后测试未同步。

**修复方案**：
- 读取实际工具定义，更新测试中的期望集合

---

## 二、前端 TS 错误修复（15 个错误）

### 第 6 组：FlowDetail.tsx 未使用变量（8 个错误）

**文件**：`apps/web/src/features/components/FlowDetail.tsx`

| 行号 | 变量 | 处理方式 |
|------|------|---------|
| 24 | `ClusterOutlined` import | 删除 import |
| 55 | `DepartmentListItem` import | 删除 import |
| 106 | `runModalOpen` state | 删除 state 声明（含 setter） |
| 113 | `uploadLoading` state | 删除 state 声明（含 setter） |
| 132 | `setRunSelectedComp` | 检查是否仅声明未用，删除或使用 |
| 136 | `setRunParams` | 同上 |
| 335 | `runParamEntries` | 删除变量声明 |
| 532 | `handleCreateRun` | 删除函数声明 |

**注意**：需检查这些变量是否被 JSX 中以 `{runModalOpen && ...}` 方式使用——如果只是 state 声明了但 UI 逻辑被移除了，可以安全删除。如果 setter 被传入子组件作为 prop，则不能删。逐个确认后再删。

---

### 第 7 组：EquipmentPage.tsx（1 个错误）

**文件**：`apps/web/src/features/equipment/EquipmentPage.tsx:111`
**错误**：`deptMap` declared but never read

**修复**：删除 `deptMap` 变量声明。

---

### 第 8 组：FactModal.tsx 类型错误（1 个错误）

**文件**：`apps/web/src/features/facts/FactModal.tsx:48`
**错误**：`Property 'user_id' does not exist on type 'CurrentUser'`

**根因**：`CurrentUser` 类型（`api/client.ts:8`）的字段是 `id`，不是 `user_id`。

**修复**：
```typescript
// 第 48 行
owner_user_id: user?.user_id,
// 改为
owner_user_id: user?.id,
```

---

### 第 9 组：DepartmentManagement.tsx（2 个错误）

**文件**：`apps/web/src/features/governance/DepartmentManagement.tsx`

1. **:319** `handleReparentClick` declared but never read — 删除函数声明
2. **:349** `Type 'string | null' is not assignable to type 'string | undefined'` — `reparentNewParent` 是 `string | null`，但 `apiUpdateDepartment` 的 `parent_id` 参数类型是 `string | null`（api/departments.ts:130 确认是 `string | null`），所以问题可能在别处

**修复**：
- 删除 `handleReparentClick` 函数（如果确认未在 JSX 中使用）
- 第 349 行的类型错误：检查 `reparentNewParent` 的类型声明，如果是 `string | null` 且 API 接受 `string | null`，可能是中间有类型转换问题，加一个 `?? undefined` 或改 API 类型

---

### 第 10 组：ExperimentalObjectPage.tsx（3 个错误）

**文件**：`apps/web/src/features/standards/ExperimentalObjectPage.tsx`

1. **:176** `equipmentMap` declared but never read — 删除
2. **:216** `watchedDeptId` declared but never read — 删除
3. **:429** `isObjectRow` declared but never read — 删除

---

## 三、执行顺序

建议按依赖关系从低到高：

1. **先删后端 IDOR 测试**（第 1 组，最快，4 个测试直接删）
2. **修权限总数**（第 3 组，改 1 行数字）
3. **修权限矩阵**（第 2 组，需对照代码更新 3 个 expected set）
4. **修 AI 工具测试**（第 4+5 组，需对照实际工具定义更新数量和名称）
5. **修前端未使用变量**（第 6+7+10 组，逐个删除）
6. **修 FactModal 类型**（第 8 组，改 1 个字段名）
7. **修 DepartmentManagement**（第 9 组，需仔细检查类型）

---

## 四、验收标准

```bash
# 后端
.venv/bin/python -m pytest tests/unit/ -q --tb=no
# 期望：0 failed, 0 errors

# 前端
cd apps/web && npx tsc --noEmit
# 期望：无输出（0 errors）
```
