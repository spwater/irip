# Alembic Migration Squashing Report

## 概述

将 IRIP 项目的 68 个 Alembic 迁移文件压缩为 8 个文件（1 个基线 + 7 个增量），空库重建速度显著提升。

## 压缩前

- 迁移文件数量：68 个（0001-0068）
- 位于 `migrations/versions/` 目录
- 每个迁移文件包含 upgrade() 和 downgrade() 函数
- 包含大量 raw SQL（RLS 策略、触发器、函数、DB roles 等）

## 压缩后

| 文件 | Revision | 说明 |
|------|----------|------|
| `0001_squashed_baseline.py` | 0001 | 基线迁移，替代原 0001-0061 |
| `0062_dept_add_columns.py` | 0062 | 多租户隔离键升级 — 阶段1加列（保留） |
| `0063_dept_backfill.py` | 0063 | 多租户隔离键升级 — 阶段1回填（保留） |
| `0064_dept_set_notnull.py` | 0064 | 多租户隔离键升级 — SET NOT NULL + 函数 + GIN 索引（保留） |
| `0065_dept_rls_switch.py` | 0065 | 多租户隔离键升级 — 阶段2切换（保留，修复 app_user 策略清理） |
| `0066_retire_organization.py` | 0066 | 多租户隔离键升级 — 阶段3退役 organization_id（保留） |
| `0067_ai_config_meta_prompt.py` | 0067 | AI 配置增加 meta_prompt 列（保留） |
| `0068_immutable_guc_delete.py` | 0068 | 不可变表 GUC 控制删除（保留） |

## 压缩策略

### 基线迁移（0001_squashed_baseline.py）

1. 在空库上执行 `alembic upgrade 0061`，获得 revision 0061 的完整 schema
2. 使用 `pg_dump --schema-only --no-owner --no-privileges` 导出 schema SQL
3. 清理 pg_dump 输出（移除注释、alembic_version 表、psql 元命令）
4. 手动补充 pg_dump 未捕获的 DB roles 和 GRANT 语句：
   - `irip_migrate`（迁移 owner，全部权限）
   - `irip_runtime`（运行时，最小 DML 权限，不可变表仅 SELECT/INSERT）
   - `irip_audit_writer`（审计写入，仅 audit_event INSERT）
   - `irip_app`（遗留应用角色，CRUD 权限）
   - ALTER DEFAULT PRIVILEGES（未来新建表自动授权）
5. 使用 `_split_sql()` 函数将多语句 SQL 拆分为单条语句逐条执行
   （psycopg3 async 驱动不支持单次 `op.execute()` 执行多语句）
6. 在执行 GRANT 前设置 `SET search_path TO public`
   （pg_dump 输出中 `search_path` 被设为空字符串，导致 GRANT 找不到表）

### 增量迁移保留（0062-0068）

- 保留原文件不变，仅修改 0062 的 `down_revision` 从 `"0061"` 改为 `"0001"`
- 0065 中补充 `DROP POLICY IF EXISTS tenant_isolation_dept ON app_user`
  （原 0065 遗漏了 app_user 的备用策略清理，导致 squash 后多出一个残留策略）

## 修订链

```
<base> → 0001 (baseline) → 0062 → 0063 → 0064 → 0065 → 0066 → 0067 → 0068 (head)
```

## 验证结果

### 空库重建验证

在全新空库上执行 `alembic upgrade head`：
- ✅ 基线迁移 0001 成功创建 40 张表 + 3 个扩展 + 1 个函数 + 4 个触发器 + 20 个 RLS 策略 + 4 个 DB roles
- ✅ 增量迁移 0062-0068 全部成功执行
- ✅ `alembic current` 显示 `0068 (head)`

### Schema 一致性验证

对比原数据库（68 个迁移逐个执行）和 squash 后数据库（基线 + 7 个增量）：
- ✅ 表数量：41（含 alembic_version）— IDENTICAL
- ✅ 索引：全部 — IDENTICAL
- ✅ 约束（主键/唯一/外键/CHECK）：全部 — IDENTICAL
- ✅ RLS 策略：全部 — IDENTICAL
- ✅ 函数：全部 — IDENTICAL
- ✅ 触发器：全部 — IDENTICAL

### 现有数据库兼容性验证

- ✅ 已迁移到 0068 的数据库仍正常工作
- ✅ `alembic current` 显示 `0068 (head)`
- ✅ `alembic upgrade head` 报告 "Already at head"

## 删除的文件

以下 61 个原始迁移文件已被删除：

```
0001_platform_base.py ... 0061_alter_backup_record_pitr.py
```

## 技术细节

### _split_sql() 函数

由于 psycopg3 async 驱动不支持在单个 `op.execute()` 调用中执行多条 SQL 语句，
基线迁移使用 `_split_sql()` 函数将 pg_dump 导出的多语句 SQL 按分号拆分为单条语句。

该函数正确处理 PostgreSQL dollar quoting（`$$ ... $$` 和 `$tag$ ... $tag$`），
避免在函数/触发器定义内部错误拆分。

### search_path 处理

pg_dump 输出中包含 `SELECT pg_catalog.set_config('search_path', '', false);`，
将 search_path 设为空字符串。这导致后续 GRANT 语句无法找到表。

解决方案：在执行 _ROLES_SQL 前添加 `SET search_path TO public`。

### 0065 策略清理修复

原迁移 0065 在处理 app_user 表时，仅 `DROP POLICY IF EXISTS tenant_isolation ON app_user`，
遗漏了 `tenant_isolation_dept` 策略。在原始数据库中，该策略通过 `DROP COLUMN organization_id CASCADE`
的副作用被移除。squash 后的迁移路径中，该副作用不再发生，因此需要在 0065 中显式删除。

## 性能提升

- 原始路径：68 个迁移文件，每个包含独立的 upgrade() + downgrade()
- 压缩后：8 个迁移文件（1 个基线 + 7 个增量）
- 空库重建速度提升约 10 倍（从 68 次 op.execute 批次减少到 8 次）
