"""0069 迁移脚本验证：建表 + 加列 + RLS + 触发器 + 数据迁移。

测试策略（与 test_dept_tenant_upgrade.py 一致）：
- 迁移脚本：读取源文件做代码审查验证（SQL 内容断言）
- ORM 实体：检查 SQLAlchemy mapped_column 定义
- 需要 DB 的集成测试在 DB 不可用时自动 skip

对应 PRD 验收标准：
- experiment_project 表含 A 类 4 列 + UniqueConstraint + GIN 索引
- flow_definition.project_id 列添加成功
- RLS 策略存在（4 分支）
- forbid_reprivatize 触发器存在
"""

import glob
from pathlib import Path

#: 迁移脚本目录
MIGRATIONS_DIR = Path(__file__).parents[3] / "migrations" / "versions"


def _glob_migration_source(rev: str) -> str:
    """用 glob 模式找到迁移脚本文件并读取内容。"""
    pattern = str(MIGRATIONS_DIR / f"{rev}_*.py")
    matches = glob.glob(pattern)
    assert matches, f"找不到迁移脚本 {rev}"
    with open(matches[0]) as f:
        return f.read()


def _load_migration_module(rev: str):
    """动态导入指定 revision 的迁移脚本模块。"""
    import importlib

    return importlib.import_module(f"migrations.versions.{rev}")


# ===========================================================================
# 1. 迁移链连续性
# ===========================================================================


class TestMigrationChain:
    """验证 0069 迁移链 down_revision 连续性。"""

    def test_migration_0069_down_revision(self):
        """0069.down_revision == '0068'"""
        mod = _load_migration_module("0069_experiment_project")
        assert mod.down_revision == "0068", (
            f"0069.down_revision 应为 '0068'，实际为 {mod.down_revision!r}"
        )

    def test_migration_0069_revision(self):
        """0069.revision == '0069'"""
        mod = _load_migration_module("0069_experiment_project")
        assert mod.revision == "0069", f"0069.revision 应为 '0069'，实际为 {mod.revision!r}"


# ===========================================================================
# 2. 建表验证（代码审查）
# ===========================================================================


class TestCreateTable:
    """验证 experiment_project 表创建语句。"""

    def test_creates_experiment_project_table(self):
        """0069 创建 experiment_project 表"""
        source = _glob_migration_source("0069")
        assert "CREATE TABLE IF NOT EXISTS experiment_project" in source, (
            "0069 应创建 experiment_project 表"
        )

    def test_table_has_a_class_four_columns(self):
        """experiment_project 表含 A 类 4 列"""
        source = _glob_migration_source("0069")
        for col in ["department_id", "visible_departments", "visibility_scope", "owner_user_id"]:
            assert col in source, f"experiment_project 表应包含 A 类列 '{col}'"

    def test_department_id_not_null_with_fk(self):
        """department_id 为 NOT NULL 且有 FK→department.id"""
        source = _glob_migration_source("0069")
        assert "department_id     UUID            NOT NULL REFERENCES department(id)" in source, (
            "department_id 应为 NOT NULL 且有 FK→department.id"
        )

    def test_visible_departments_not_null_default(self):
        """visible_departments 为 NOT NULL DEFAULT '[]'"""
        source = _glob_migration_source("0069")
        assert "visible_departments JSONB" in source
        assert "NOT NULL DEFAULT '[]'::jsonb" in source, (
            "visible_departments 应为 NOT NULL DEFAULT '[]'::jsonb"
        )

    def test_visibility_scope_not_null_default_tree(self):
        """visibility_scope 为 NOT NULL DEFAULT 'tree'"""
        source = _glob_migration_source("0069")
        assert "visibility_scope  TEXT            NOT NULL DEFAULT 'tree'" in source, (
            "visibility_scope 应为 NOT NULL DEFAULT 'tree'"
        )

    def test_owner_user_id_not_null_with_fk(self):
        """owner_user_id 为 NOT NULL 且有 FK→app_user.id"""
        source = _glob_migration_source("0069")
        assert "owner_user_id     UUID            NOT NULL REFERENCES app_user(id)" in source, (
            "owner_user_id 应为 NOT NULL 且有 FK→app_user.id"
        )

    def test_unique_constraint_dept_code(self):
        """UniqueConstraint (department_id, code)"""
        source = _glob_migration_source("0069")
        assert "uq_experiment_project_dept_code" in source, (
            "应有唯一约束 uq_experiment_project_dept_code"
        )
        assert "UNIQUE (department_id, code)" in source, "唯一约束应为 (department_id, code)"

    def test_gin_index_on_visible_departments(self):
        """GIN 索引在 visible_departments 列上"""
        source = _glob_migration_source("0069")
        assert "ix_experiment_project_visible_depts_gin" in source, (
            "应有 GIN 索引 ix_experiment_project_visible_depts_gin"
        )
        assert "USING GIN (visible_departments)" in source, "GIN 索引应在 visible_departments 列上"

    def test_business_columns(self):
        """业务字段：code, display_name, description, status, lock_version"""
        source = _glob_migration_source("0069")
        for col in ["code", "display_name", "description", "status", "lock_version"]:
            assert col in source, f"应有业务字段 '{col}'"
        assert "DEFAULT 'active'" in source, "status 默认值应为 'active'"
        assert "lock_version      INTEGER         NOT NULL DEFAULT 0" in source


# ===========================================================================
# 3. RLS 策略验证（代码审查）
# ===========================================================================


class TestRLSPolicy:
    """验证 experiment_project 表 RLS 策略（4 分支）。"""

    def test_enables_rls(self):
        """启用 RLS"""
        source = _glob_migration_source("0069")
        assert "ALTER TABLE experiment_project ENABLE ROW LEVEL SECURITY" in source
        assert "ALTER TABLE experiment_project FORCE ROW LEVEL SECURITY" in source

    def test_drops_old_policy_before_create(self):
        """创建策略前先 DROP 旧策略"""
        source = _glob_migration_source("0069")
        assert "DROP POLICY IF EXISTS tenant_isolation ON experiment_project" in source

    def test_rls_has_private_branch(self):
        """RLS 策略含私有分支（visibility_scope = 'private'）"""
        source = _glob_migration_source("0069")
        assert "visibility_scope = 'private'" in source, "RLS 策略应含私有分支"

    def test_rls_private_branch_checks_owner(self):
        """私有分支检查 owner_user_id = current_user_id"""
        source = _glob_migration_source("0069")
        assert "owner_user_id = NULLIF(current_setting('app.current_user_id'" in source, (
            "私有分支应检查 owner_user_id = current_user_id"
        )

    def test_rls_has_tree_branch(self):
        """RLS 策略含层级分支（visibility_scope = 'tree'）"""
        source = _glob_migration_source("0069")
        assert "visibility_scope = 'tree'" in source, "RLS 策略应含层级分支"

    def test_rls_tree_branch_uses_current_visible_dept_ids(self):
        """层级分支使用 current_visible_dept_ids()"""
        source = _glob_migration_source("0069")
        assert "current_visible_dept_ids()" in source, "层级分支应使用 current_visible_dept_ids()"

    def test_rls_has_explicit_branch(self):
        """RLS 策略含白名单分支（visibility_scope = 'explicit'）"""
        source = _glob_migration_source("0069")
        assert "visibility_scope = 'explicit'" in source, "RLS 策略应含白名单分支"

    def test_rls_has_all_branch(self):
        """RLS 策略含全可见分支（visibility_scope = 'all'）"""
        source = _glob_migration_source("0069")
        assert "visibility_scope = 'all'" in source, "RLS 策略应含全可见分支"

    def test_rls_uses_jsonb_contains(self):
        """白名单分支使用 JSONB @> 操作符"""
        source = _glob_migration_source("0069")
        assert "@> jsonb_build_array" in source, (
            "白名单分支应使用 visible_departments @> jsonb_build_array(...)"
        )

    def test_rls_policy_sql_balanced_parentheses(self):
        """RLS 策略 SQL 括号配平验证（关键：防止 USING() 被提前关闭）"""
        source = _glob_migration_source("0069")
        # 提取 CREATE POLICY 语句
        start = source.find("CREATE POLICY tenant_isolation ON experiment_project")
        assert start != -1, "应包含 CREATE POLICY 语句"
        # 找到该 SQL 语句的结束（以 ) 结尾的行）
        end = source.find(")", source.find("visibility_scope = 'all'", start))
        policy_sql = source[start : end + 1]

        # 验证括号配平
        open_count = policy_sql.count("(")
        close_count = policy_sql.count(")")
        assert open_count == close_count, (
            f"RLS 策略 SQL 括号不配平：开括号 {open_count} 个，闭括号 {close_count} 个。"
            f"这会导致 USING(...) 被提前关闭，引发 syntax error at OR。"
        )


# ===========================================================================
# 4. forbid_reprivatize 触发器验证
# ===========================================================================


class TestForbidReprivatizeTrigger:
    """验证 forbid_reprivatize() 触发器。"""

    def test_creates_trigger(self):
        """0069 创建 forbid_reprivatize 触发器"""
        source = _glob_migration_source("0069")
        assert "trg_forbid_reprivatize" in source, "应创建 trg_forbid_reprivatize 触发器"

    def test_trigger_is_before_update(self):
        """触发器为 BEFORE UPDATE"""
        source = _glob_migration_source("0069")
        assert "BEFORE UPDATE ON experiment_project" in source, "触发器应为 BEFORE UPDATE"

    def test_trigger_executes_forbid_reprivatize_function(self):
        """触发器执行 forbid_reprivatize() 函数"""
        source = _glob_migration_source("0069")
        assert "EXECUTE FUNCTION forbid_reprivatize()" in source, (
            "触发器应执行 forbid_reprivatize() 函数"
        )

    def test_trigger_drops_old_first(self):
        """先 DROP 旧触发器再创建（幂等）"""
        source = _glob_migration_source("0069")
        assert "DROP TRIGGER IF EXISTS trg_forbid_reprivatize ON experiment_project" in source, (
            "应先 DROP 旧触发器再创建"
        )


# ===========================================================================
# 5. flow_definition 加列验证
# ===========================================================================


class TestFlowDefinitionProjectId:
    """验证 flow_definition 表增加 project_id 列。"""

    def test_adds_project_id_column(self):
        """flow_definition 加 project_id UUID 列"""
        source = _glob_migration_source("0069")
        assert "ALTER TABLE flow_definition" in source
        assert "ADD COLUMN IF NOT EXISTS project_id UUID" in source, (
            "flow_definition 应增加 project_id UUID 列"
        )

    def test_adds_fk_constraint(self):
        """flow_definition.project_id 有 FK→experiment_project.id"""
        source = _glob_migration_source("0069")
        assert "fk_flow_definition_project_id" in source, (
            "应有 FK 约束 fk_flow_definition_project_id"
        )
        assert "FOREIGN KEY (project_id) REFERENCES experiment_project(id)" in source, (
            "FK 应指向 experiment_project.id"
        )

    def test_fk_uses_do_block_for_idempotency(self):
        """FK 约束使用 DO $$ BEGIN ... END $$ 块保证幂等"""
        source = _glob_migration_source("0069")
        assert "DO $$" in source, "FK 约束应使用 DO $$ 块保证幂等"
        assert "IF NOT EXISTS" in source

    def test_project_name_deprecated_comment(self):
        """flow_definition.project_name 加 DEPRECATED COMMENT"""
        source = _glob_migration_source("0069")
        assert (
            "COMMENT ON COLUMN flow_definition.project_name IS 'DEPRECATED: replaced by project_id'"
            in source
        ), "project_name 应加 DEPRECATED COMMENT"


# ===========================================================================
# 6. 存量数据迁移验证
# ===========================================================================


class TestDataMigration:
    """验证存量数据迁移逻辑（代码审查）。"""

    def test_inserts_from_flow_definition(self):
        """存量迁移从 flow_definition 按 (department_id, project_name) 去重创建项目"""
        source = _glob_migration_source("0069")
        assert "INSERT INTO experiment_project" in source, (
            "应有 INSERT INTO experiment_project 语句"
        )

    def test_migration_uses_gen_random_uuid_for_id(self):
        """存量迁移使用 gen_random_uuid() 生成 id"""
        source = _glob_migration_source("0069")
        insert_section = source[source.find("INSERT INTO experiment_project") :]
        assert "gen_random_uuid()" in insert_section, "存量迁移应使用 gen_random_uuid() 生成 id"

    def test_migration_generates_proj_code(self):
        """存量迁移生成 'proj_' 前缀编码"""
        source = _glob_migration_source("0069")
        insert_section = source[source.find("INSERT INTO experiment_project") :]
        assert "'proj_'" in insert_section, "存量迁移应生成 'proj_' 前缀编码"

    def test_migration_takes_earliest_owner(self):
        """owner_user_id 取该 project_name 下最早创建任务的 owner"""
        source = _glob_migration_source("0069")
        assert "ORDER BY fd2.created_at ASC" in source, "应按 created_at ASC 取最早创建任务的 owner"
        assert "LIMIT 1" in source

    def test_migration_is_idempotent(self):
        """存量迁移幂等（NOT EXISTS 检查）"""
        source = _glob_migration_source("0069")
        assert "WHERE NOT EXISTS" in source, "存量迁移应使用 WHERE NOT EXISTS 保证幂等"

    def test_migration_filters_non_empty_project_name(self):
        """迁移过滤 project_name 非空"""
        source = _glob_migration_source("0069")
        assert "fd.project_name IS NOT NULL" in source
        assert "fd.project_name <> ''" in source, "应过滤 project_name 非空"

    def test_backfill_flow_definition_project_id(self):
        """回填 flow_definition.project_id"""
        source = _glob_migration_source("0069")
        assert "UPDATE flow_definition fd" in source
        assert "SET project_id = ep.id" in source, "应回填 flow_definition.project_id"

    def test_backfill_joins_on_dept_and_project_name(self):
        """回填按 department_id + project_name 关联"""
        source = _glob_migration_source("0069")
        update_section = source[source.find("UPDATE flow_definition fd") :]
        assert "ep.department_id = fd.department_id" in update_section
        assert "ep.display_name = fd.project_name" in update_section


# ===========================================================================
# 7. ORM 实体验证
# ===========================================================================


class TestExperimentProjectORM:
    """验证 ExperimentProject ORM 实体定义。"""

    def test_table_name(self):
        """ORM 表名为 experiment_project"""
        from packages.experiment_project.entities import ExperimentProject

        assert ExperimentProject.__tablename__ == "experiment_project"

    def test_has_all_columns(self):
        """ORM 含全部必要列"""
        import packages.experiment_project.entities  # noqa: F401
        from packages.common.database import Base

        table = Base.metadata.tables.get("experiment_project")
        assert table is not None, "experiment_project 表应注册到 Base.metadata"
        expected_cols = {
            "id",
            "department_id",
            "code",
            "display_name",
            "description",
            "status",
            "visible_departments",
            "visibility_scope",
            "owner_user_id",
            "created_at",
            "updated_at",
            "lock_version",
        }
        actual_cols = set(table.columns.keys())
        missing = expected_cols - actual_cols
        assert not missing, f"缺少列: {missing}"

    def test_department_id_not_null_with_fk(self):
        """department_id 为 NOT NULL 且有 FK→department.id"""
        import packages.experiment_project.entities  # noqa: F401
        from packages.common.database import Base

        table = Base.metadata.tables["experiment_project"]
        col = table.columns["department_id"]
        assert not col.nullable, "department_id 应为 NOT NULL"
        fk_targets = [fk.target_fullname for fk in col.foreign_keys]
        assert any("department.id" in t for t in fk_targets), (
            f"department_id FK 应指向 department.id，实际: {fk_targets}"
        )

    def test_owner_user_id_not_null_with_fk(self):
        """owner_user_id 为 NOT NULL 且有 FK→app_user.id"""
        import packages.experiment_project.entities  # noqa: F401
        from packages.common.database import Base

        table = Base.metadata.tables["experiment_project"]
        col = table.columns["owner_user_id"]
        assert not col.nullable, "owner_user_id 应为 NOT NULL"
        fk_targets = [fk.target_fullname for fk in col.foreign_keys]
        assert any("app_user.id" in t for t in fk_targets), (
            f"owner_user_id FK 应指向 app_user.id，实际: {fk_targets}"
        )

    def test_visible_departments_not_null(self):
        """visible_departments 为 NOT NULL"""
        import packages.experiment_project.entities  # noqa: F401
        from packages.common.database import Base

        table = Base.metadata.tables["experiment_project"]
        col = table.columns["visible_departments"]
        assert not col.nullable, "visible_departments 应为 NOT NULL"

    def test_visibility_scope_not_null(self):
        """visibility_scope 为 NOT NULL"""
        import packages.experiment_project.entities  # noqa: F401
        from packages.common.database import Base

        table = Base.metadata.tables["experiment_project"]
        col = table.columns["visibility_scope"]
        assert not col.nullable, "visibility_scope 应为 NOT NULL"

    def test_unique_constraint(self):
        """UniqueConstraint (department_id, code)"""
        import packages.experiment_project.entities  # noqa: F401
        from packages.common.database import Base

        table = Base.metadata.tables["experiment_project"]
        constraint_names = [c.name for c in table.constraints if hasattr(c, "name")]
        assert "uq_experiment_project_dept_code" in constraint_names, (
            f"应有唯一约束 uq_experiment_project_dept_code，实际: {constraint_names}"
        )

    def test_status_enum(self):
        """ExperimentProjectStatus 枚举有 ACTIVE 和 ARCHIVED"""
        from packages.experiment_project.entities import ExperimentProjectStatus

        assert ExperimentProjectStatus.ACTIVE.value == "active"
        assert ExperimentProjectStatus.ARCHIVED.value == "archived"


class TestFlowDefinitionProjectIdColumn:
    """验证 FlowDefinition ORM 增加 project_id 列。"""

    def test_flow_definition_has_project_id(self):
        """FlowDefinition ORM 含 project_id 列"""
        import packages.components.flow.flow_runtime  # noqa: F401
        from packages.common.database import Base

        table = Base.metadata.tables["flow_definition"]
        assert "project_id" in table.columns, "flow_definition 应包含 project_id 列"

    def test_project_id_nullable_with_fk(self):
        """project_id 为 nullable 且有 FK→experiment_project.id"""
        import packages.components.flow.flow_runtime  # noqa: F401
        from packages.common.database import Base

        table = Base.metadata.tables["flow_definition"]
        col = table.columns["project_id"]
        assert col.nullable, "project_id 应为 nullable"
        fk_targets = [fk.target_fullname for fk in col.foreign_keys]
        assert any("experiment_project.id" in t for t in fk_targets), (
            f"project_id FK 应指向 experiment_project.id，实际: {fk_targets}"
        )


# ===========================================================================
# 8. 权限常量验证
# ===========================================================================


class TestPermissions:
    """验证 EXPERIMENT_PROJECT_MANAGE / READ 权限常量。"""

    def test_permission_constants_defined(self):
        """EXPERIMENT_PROJECT_MANAGE / READ 权限常量已定义"""
        from packages.auth.permissions import Permission

        assert Permission.EXPERIMENT_PROJECT_MANAGE == "experiment_project:manage"
        assert Permission.EXPERIMENT_PROJECT_READ == "experiment_project:read"

    def test_platform_administrator_has_manage(self):
        """platform_administrator 拥有 EXPERIMENT_PROJECT_MANAGE"""
        from packages.auth.permissions import BUILTIN_ROLES, Permission

        perms = BUILTIN_ROLES["platform_administrator"]["permissions"]
        assert Permission.EXPERIMENT_PROJECT_MANAGE in perms
        assert Permission.EXPERIMENT_PROJECT_READ in perms

    def test_lab_director_has_manage(self):
        """lab_director 拥有 EXPERIMENT_PROJECT_MANAGE"""
        from packages.auth.permissions import BUILTIN_ROLES, Permission

        perms = BUILTIN_ROLES["lab_director"]["permissions"]
        assert Permission.EXPERIMENT_PROJECT_MANAGE in perms
        assert Permission.EXPERIMENT_PROJECT_READ in perms

    def test_all_roles_have_read(self):
        """全部角色拥有 EXPERIMENT_PROJECT_READ"""
        from packages.auth.permissions import BUILTIN_ROLES, Permission

        for role_code, role_def in BUILTIN_ROLES.items():
            perms = role_def["permissions"]
            assert Permission.EXPERIMENT_PROJECT_READ in perms, (
                f"角色 '{role_code}' 应拥有 EXPERIMENT_PROJECT_READ 权限"
            )
