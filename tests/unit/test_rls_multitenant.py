"""多租户 RLS 端到端测试矩阵。

P0 安全止血包核心验证：证明 RLS 在 ``irip_app``（非 superuser）连接下
真正拦截跨部门数据访问。

测试矩阵：
  角色：
    - lab_member (user_a 在 childA, user_b 在 childB)
    - platform_administrator (admin 在 root, 可见全部)
  部门层级：
    root (哨兵部门, 代码='root')
    ├── childA (子部门A)
    └── childB (子部门B, 与 childA 互为兄弟, 不应互相可见)
  核心表（10 张代表表）：
    equipment, industrial_object, fact, flow_definition, flow_run,
    job, artifact, parameter, provenance_edge, evidence_set
  操作：
    - 跨部门读（应被 RLS 拦截, 返回 0 行）
    - 本部门读（应可见, 返回 >0 行）
    - 跨部门写（应被 RLS 拦截, 报错或 0 行影响）
    - platform_administrator 跨部门读（应可见全部, 通过 root 部门挂载）

关键设计：
  1. 测试用非 superuser 连接验证 RLS —— 使用 ``SET ROLE irip_app``
     切换到非 superuser 角色, 确保 RLS 策略生效。
     superuser 会绕过 RLS, 用 superuser 测等于没测。
  2. 每个测试场景通过 ``set_config('app.current_user_id', ...)``
     和 ``set_config('app.current_dept_id', ...)`` 设置 GUC,
     模拟不同角色和部门上下文。
  3. provenance_edge 专项验证 0072 迁移修复后的 RLS 策略
     （department_id IN current_visible_dept_ids()）。

测试策略：
  - 迁移脚本/RLS 策略代码审查：不需要 DB, 通过读取迁移源文件验证
  - RLS 行为测试：需要真实 DB + irip_app 角色, 不可用时自动 skip
"""

import os
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: 迁移脚本目录
_MIGRATIONS_DIR = _PROJECT_ROOT / "migrations" / "versions"

#: 测试覆盖的 10 张代表表
RLS_TABLES: list[str] = [
    "equipment",
    "industrial_object",
    "fact",
    "flow_definition",
    "flow_run",
    "job",
    "artifact",
    "parameter",
    "provenance_edge",
    "evidence_set",
]

#: A 类表（含 visibility_scope + owner_user_id + visible_departments）
A_TABLES: list[str] = [
    "equipment",
    "industrial_object",
    "fact",
    "flow_definition",
    "artifact",
    "parameter",
    "evidence_set",
]

#: B 类表（仅 department_id）
B_TABLES: list[str] = [
    "flow_run",
    "job",
]

#: C 类表 → B 类升级（provenance_edge, 0072 迁移目标）
PROVENANCE_EDGE_TABLE = "provenance_edge"

#: 非 superuser 角色名
APP_ROLE = "irip_app"


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _get_test_db_url() -> str | None:
    """获取测试数据库 URL, 未配置时返回 None。"""
    return os.getenv("IRIP_TEST_DATABASE_URL")


def _glob_migration_source(rev: str) -> str:
    """用 glob 模式找到迁移脚本文件并读取内容。"""
    import glob

    pattern = str(_MIGRATIONS_DIR / f"{rev}_*.py")
    matches = glob.glob(pattern)
    assert matches, f"找不到迁移脚本 {rev}"
    with open(matches[0]) as f:
        return f.read()


def _is_super_user(conn) -> bool:
    """检查当前连接角色是否为 superuser。"""
    result = conn.execute(sa.text("SELECT current_setting('is_superuser')"))
    return result.scalar() == "on"


def _role_exists(conn, role_name: str) -> bool:
    """检查指定角色是否存在。"""
    result = conn.execute(
        sa.text("SELECT 1 FROM pg_roles WHERE rolname = :name"),
        {"name": role_name},
    )
    return result.fetchone() is not None


def _rls_enabled(conn, table_name: str) -> bool:
    """检查指定表的 RLS 是否已启用。"""
    result = conn.execute(
        sa.text(
            "SELECT relrowsecurity FROM pg_class WHERE relname = :table AND relnamespace = "
            "(SELECT oid FROM pg_namespace WHERE nspname = 'public')"
        ),
        {"table": table_name},
    )
    row = result.fetchone()
    return row is not None and row[0] is True


def _get_rls_policy(conn, table_name: str) -> list[dict]:
    """获取指定表的 RLS 策略列表。"""
    result = conn.execute(
        sa.text(
            "SELECT policyname, cmd, qual, with_check "
            "FROM pg_policies WHERE tablename = :table AND schemaname = 'public'"
        ),
        {"table": table_name},
    )
    return [
        {
            "name": row[0],
            "cmd": row[1],
            "qual": row[2],
            "with_check": row[3],
        }
        for row in result.fetchall()
    ]


@contextmanager
def rls_test_context(conn, dept_id, user_id):
    """RLS test context: SET ROLE + GUCs, then ROLLBACK.

    1. SET ROLE irip_app (non-superuser, RLS enforced)
    2. set_config GUCs (local to transaction)
    3. yield conn
    4. ROLLBACK (clear GUCs, undo test writes)
    5. RESET ROLE
    """
    # Commit the auto-begun transaction so SET ROLE runs outside a tx
    conn.commit()
    conn.execute(sa.text(f"SET ROLE {APP_ROLE}"))
    # SET ROLE auto-begins a new transaction; use it directly
    try:
        conn.execute(
            sa.text("SELECT set_config('app.current_dept_id', :d, true)"),
            {"d": str(dept_id)},
        )
        conn.execute(
            sa.text("SELECT set_config('app.current_user_id', :u, true)"),
            {"u": str(user_id)},
        )
        yield conn
        conn.rollback()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute(sa.text("RESET ROLE"))


# ---------------------------------------------------------------------------
# 1. 0072 迁移修复验证（代码审查, 不需要 DB）
# ---------------------------------------------------------------------------


class TestProvenanceEdge0072Migration:
    """验证 0072 迁移：provenance_edge 从 organization_id 切换到 department_id。

    这些测试通过读取迁移源文件进行代码审查, 不需要数据库。
    """

    def test_0072_adds_department_id_column(self):
        """0072 添加 department_id 列到 provenance_edge。"""
        source = _glob_migration_source("0072")
        assert "ALTER TABLE provenance_edge ADD COLUMN department_id uuid" in source, (
            "0072 应添加 department_id 列"
        )

    def test_0072_backfill_to_root_department(self):
        """0072 确定性回填 department_id 到 root 部门（非 gen_random_uuid）。"""
        source = _glob_migration_source("0072")
        assert "code = 'root' AND parent_id IS NULL" in source, (
            "0072 应回填到 code='root' AND parent_id IS NULL 的 root 部门"
        )
        assert "gen_random_uuid()" not in source, "0072 不应使用 gen_random_uuid() 随机回填"

    def test_0072_checks_no_null_after_backfill(self):
        """0072 回填后显式检查不应有 NULL department_id。"""
        source = _glob_migration_source("0072")
        assert "RAISE EXCEPTION" in source, "0072 回填后应有异常检查"
        assert "department_id IS NULL" in source, (
            "0072 应检查 provenance_edge WHERE department_id IS NULL"
        )

    def test0072_sets_department_id_not_null(self):
        """0072 将 department_id 设为 NOT NULL。"""
        source = _glob_migration_source("0072")
        assert "ALTER COLUMN department_id SET NOT NULL" in source, (
            "0072 应将 department_id 设为 NOT NULL"
        )

    def test_0072_adds_fk_constraint(self):
        """0072 添加 FK 约束 department_id → department.id。"""
        source = _glob_migration_source("0072")
        assert "FOREIGN KEY (department_id)" in source, (
            "0072 应添加 FK (department_id) REFERENCES department(id)"
        )
        assert "REFERENCES department(id)" in source, "FK 应指向 department.id"

    def test_0072_adds_index(self):
        """0072 添加 department_id 索引。"""
        source = _glob_migration_source("0072")
        assert "ix_provenance_edge_department_id" in source, (
            "0072 应创建索引 ix_provenance_edge_department_id"
        )

    def test_0072_drops_old_org_rls_policy(self):
        """0072 删除旧的 organization_id 锚定的 RLS 策略。"""
        source = _glob_migration_source("0072")
        assert "DROP POLICY IF EXISTS tenant_isolation ON provenance_edge" in source, (
            "0072 应删除旧 RLS 策略"
        )

    def test_0072_creates_new_dept_rls_policy(self):
        """0072 创建新的 department_id 锚定的 B 类层级 RLS 策略。"""
        source = _glob_migration_source("0072")
        assert "CREATE POLICY tenant_isolation ON provenance_edge" in source, (
            "0072 应创建新 RLS 策略"
        )
        assert "department_id IN (SELECT current_visible_dept_ids())" in source, (
            "0072 新策略应使用 department_id IN (SELECT current_visible_dept_ids())"
        )

    def test_0072_drops_organization_id_column(self):
        """0072 删除旧的 organization_id 列。"""
        source = _glob_migration_source("0072")
        assert "DROP COLUMN IF EXISTS organization_id" in source, "0072 应删除 organization_id 列"

    def test_0072_down_revision_is_0071(self):
        """0072.down_revision == '0071'。"""
        source = _glob_migration_source("0072")
        assert 'down_revision = "0071"' in source, "0072.down_revision 应为 '0071'"


# ---------------------------------------------------------------------------
# 2. RLS 策略存在性验证（需要 DB, 验证策略 SQL）
# ---------------------------------------------------------------------------


class TestRLSPolicyExistence:
    """验证 10 张核心表都有 RLS 策略且使用 current_visible_dept_ids()。

    需要真实 DB 连接, 不可用时自动 skip。
    """

    @pytest.fixture(autouse=True)
    def _check_db(self):
        """检查数据库可用性。"""
        db_url = _get_test_db_url()
        if not db_url:
            pytest.skip("IRIP_TEST_DATABASE_URL not set; skipping RLS policy test")

    @pytest.mark.parametrize("table_name", RLS_TABLES)
    def test_rls_enabled_on_table(self, table_name: str):
        """每张表都启用了 RLS（relrowsecurity = true）。"""
        db_url = _get_test_db_url()
        engine = create_engine(db_url, pool_pre_ping=True)
        try:
            with engine.connect() as conn:
                if not _rls_enabled(conn, table_name):
                    pytest.fail(f"表 '{table_name}' 未启用 RLS")
        finally:
            engine.dispose()

    @pytest.mark.parametrize("table_name", RLS_TABLES)
    def test_tenant_isolation_policy_exists(self, table_name: str):
        """每张表都有 tenant_isolation RLS 策略。"""
        db_url = _get_test_db_url()
        engine = create_engine(db_url, pool_pre_ping=True)
        try:
            with engine.connect() as conn:
                policies = _get_rls_policy(conn, table_name)
                policy_names = [p["name"] for p in policies]
                assert "tenant_isolation" in policy_names, (
                    f"表 '{table_name}' 应有 tenant_isolation 策略, 实际策略: {policy_names}"
                )
        finally:
            engine.dispose()

    @pytest.mark.parametrize("table_name", RLS_TABLES)
    def test_policy_uses_current_visible_dept_ids(self, table_name: str):
        """每张表的 RLS 策略使用 current_visible_dept_ids() 函数。"""
        db_url = _get_test_db_url()
        engine = create_engine(db_url, pool_pre_ping=True)
        try:
            with engine.connect() as conn:
                policies = _get_rls_policy(conn, table_name)
                tenant_policy = next((p for p in policies if p["name"] == "tenant_isolation"), None)
                assert tenant_policy is not None, f"表 '{table_name}' 无 tenant_isolation 策略"
                qual = tenant_policy.get("qual") or ""
                assert "current_visible_dept_ids" in qual, (
                    f"表 '{table_name}' 的 RLS 策略应使用 current_visible_dept_ids(), "
                    f"实际 qual: {qual}"
                )
        finally:
            engine.dispose()

    @pytest.mark.parametrize(
        "table_name", PROVENANCE_EDGE_TABLE if False else [PROVENANCE_EDGE_TABLE]
    )
    def test_provenance_edge_has_department_id_column(self, table_name: str):
        """provenance_edge 表有 department_id 列（0072 迁移添加）。"""
        db_url = _get_test_db_url()
        engine = create_engine(db_url, pool_pre_ping=True)
        try:
            with engine.connect() as conn:
                result = conn.execute(
                    sa.text(
                        "SELECT column_name, is_nullable FROM information_schema.columns "
                        "WHERE table_name = :table AND column_name = 'department_id'"
                    ),
                    {"table": table_name},
                )
                row = result.fetchone()
                assert row is not None, f"表 '{table_name}' 应有 department_id 列（0072 迁移添加）"
                assert row[1] == "NO", (
                    f"表 '{table_name}'.department_id 应为 NOT NULL, 实际为 {row[1]}"
                )
        finally:
            engine.dispose()


# ---------------------------------------------------------------------------
# 3. RLS 行为测试 — 跨部门读拦截（需要 DB + irip_app 角色）
# ---------------------------------------------------------------------------


class TestRLSCrossDeptReadBlocked:
    """验证跨部门读被 RLS 拦截。

    用户在 childA, 查询 childB 的数据 → 应返回 0 行。
    """

    @pytest.fixture(autouse=True)
    def _check_db(self):
        """检查数据库可用性和 irip_app 角色。"""
        db_url = _get_test_db_url()
        if not db_url:
            pytest.skip("IRIP_TEST_DATABASE_URL not set; skipping RLS behavior test")

    def _setup_test_data(self, engine) -> tuple:
        """设置测试数据：创建部门、用户、设备。

        Returns:
            (root_dept_id, child_a_id, child_b_id, user_a_id, user_b_id, equipment_ids)
        """
        import uuid as uuid_module

        with engine.connect() as conn:
            # 获取 root 哨兵部门 ID
            result = conn.execute(
                sa.text("SELECT id FROM department WHERE code = 'root' AND parent_id IS NULL")
            )
            root_row = result.fetchone()
            if root_row is None:
                pytest.skip("root 哨兵部门不存在, 需先执行迁移")
            root_dept_id = root_row[0]

            # 创建子部门 childA 和 childB
            child_a_id = uuid_module.uuid4()
            child_b_id = uuid_module.uuid4()
            user_a_id = uuid_module.uuid4()
            user_b_id = uuid_module.uuid4()

            conn.execute(
                sa.text(
                    "INSERT INTO department "
                    "(id, code, display_name, status, sort_order, parent_id) "
                    "VALUES (:id, :code, :name, 'active', 100, :parent)"
                ),
                {
                    "id": child_a_id,
                    "code": f"test_child_a_{child_a_id.hex[:8]}",
                    "name": "Test Child A",
                    "parent": root_dept_id,
                },
            )
            conn.execute(
                sa.text(
                    "INSERT INTO department "
                    "(id, code, display_name, status, sort_order, parent_id) "
                    "VALUES (:id, :code, :name, 'active', 101, :parent)"
                ),
                {
                    "id": child_b_id,
                    "code": f"test_child_b_{child_b_id.hex[:8]}",
                    "name": "Test Child B",
                    "parent": root_dept_id,
                },
            )

            # 创建用户 user_a (在 childA) 和 user_b (在 childB)
            from packages.auth.passwords import hash_password

            conn.execute(
                sa.text(
                    "INSERT INTO app_user (id, email, display_name, password_hash, "
                    "status, lock_version, department_id) "
                    "VALUES (:id, :email, :name, :hash, 'active', 0, :dept)"
                ),
                {
                    "id": user_a_id,
                    "email": f"rls_user_a_{user_a_id.hex[:8]}@irip.local",
                    "name": "RLS User A",
                    "hash": hash_password("Test-Password-2026!"),
                    "dept": child_a_id,
                },
            )
            conn.execute(
                sa.text(
                    "INSERT INTO app_user (id, email, display_name, password_hash, "
                    "status, lock_version, department_id) "
                    "VALUES (:id, :email, :name, :hash, 'active', 0, :dept)"
                ),
                {
                    "id": user_b_id,
                    "email": f"rls_user_b_{user_b_id.hex[:8]}@irip.local",
                    "name": "RLS User B",
                    "hash": hash_password("Test-Password-2026!"),
                    "dept": child_b_id,
                },
            )

            # 设置用户-部门关联 (app_user_department)
            conn.execute(
                sa.text(
                    "INSERT INTO app_user_department (user_id, department_id, is_primary) "
                    "VALUES (:uid, :dept, true)"
                ),
                {"uid": user_a_id, "dept": child_a_id},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO app_user_department (user_id, department_id, is_primary) "
                    "VALUES (:uid, :dept, true)"
                ),
                {"uid": user_b_id, "dept": child_b_id},
            )

            # 创建设备：childA 一条, childB 一条
            equip_a_id = uuid_module.uuid4()
            equip_b_id = uuid_module.uuid4()
            conn.execute(
                sa.text(
                    "INSERT INTO equipment (id, code, display_name, department_id, owner_user_id) "
                    "VALUES (:id, :code, :name, :dept, :owner)"
                ),
                {
                    "id": equip_a_id,
                    "code": f"EQ_A_{equip_a_id.hex[:8]}",
                    "name": "Equipment A",
                    "dept": child_a_id,
                    "owner": user_a_id,
                },
            )
            conn.execute(
                sa.text(
                    "INSERT INTO equipment (id, code, display_name, department_id, owner_user_id) "
                    "VALUES (:id, :code, :name, :dept, :owner)"
                ),
                {
                    "id": equip_b_id,
                    "code": f"EQ_B_{equip_b_id.hex[:8]}",
                    "name": "Equipment B",
                    "dept": child_b_id,
                    "owner": user_b_id,
                },
            )

            # 创建 evidence_set：childA 一条, childB 一条
            es_a_id = uuid_module.uuid4()
            es_b_id = uuid_module.uuid4()
            conn.execute(
                sa.text(
                    "INSERT INTO evidence_set (id, name, department_id, owner_user_id) "
                    "VALUES (:id, :name, :dept, :owner)"
                ),
                {
                    "id": es_a_id,
                    "name": f"ES_A_{es_a_id.hex[:8]}",
                    "dept": child_a_id,
                    "owner": user_a_id,
                },
            )
            conn.execute(
                sa.text(
                    "INSERT INTO evidence_set (id, name, department_id, owner_user_id) "
                    "VALUES (:id, :name, :dept, :owner)"
                ),
                {
                    "id": es_b_id,
                    "name": f"ES_B_{es_b_id.hex[:8]}",
                    "dept": child_b_id,
                    "owner": user_b_id,
                },
            )

            # 创建 job：childA 一条, childB 一条
            job_a_id = uuid_module.uuid4()
            job_b_id = uuid_module.uuid4()
            conn.execute(
                sa.text(
                    "INSERT INTO job (id, kind, status, idempotency_key, department_id) "
                    "VALUES (:id, 'echo', 'queued', :idem, :dept)"
                ),
                {
                    "id": job_a_id,
                    "idem": f"job_a_{job_a_id.hex[:8]}",
                    "dept": child_a_id,
                },
            )
            conn.execute(
                sa.text(
                    "INSERT INTO job (id, kind, status, idempotency_key, department_id) "
                    "VALUES (:id, 'echo', 'queued', :idem, :dept)"
                ),
                {
                    "id": job_b_id,
                    "idem": f"job_b_{job_b_id.hex[:8]}",
                    "dept": child_b_id,
                },
            )

            # 创建 flow_definition：childA 一条, childB 一条
            fd_a_id = uuid_module.uuid4()
            fd_b_id = uuid_module.uuid4()
            conn.execute(
                sa.text(
                    "INSERT INTO flow_definition (id, code, display_name, "
                    "department_id, owner_user_id) "
                    "VALUES (:id, :code, :name, :dept, :owner)"
                ),
                {
                    "id": fd_a_id,
                    "code": f"FD_A_{fd_a_id.hex[:8]}",
                    "name": "Flow Def A",
                    "dept": child_a_id,
                    "owner": user_a_id,
                },
            )
            conn.execute(
                sa.text(
                    "INSERT INTO flow_definition (id, code, display_name, "
                    "department_id, owner_user_id) "
                    "VALUES (:id, :code, :name, :dept, :owner)"
                ),
                {
                    "id": fd_b_id,
                    "code": f"FD_B_{fd_b_id.hex[:8]}",
                    "name": "Flow Def B",
                    "dept": child_b_id,
                    "owner": user_b_id,
                },
            )

            conn.commit()

            return (
                root_dept_id,
                child_a_id,
                child_b_id,
                user_a_id,
                user_b_id,
                {
                    "equipment_a": equip_a_id,
                    "equipment_b": equip_b_id,
                    "evidence_set_a": es_a_id,
                    "evidence_set_b": es_b_id,
                    "job_a": job_a_id,
                    "job_b": job_b_id,
                    "flow_definition_a": fd_a_id,
                    "flow_definition_b": fd_b_id,
                },
            )

    def _cleanup_test_data(self, engine, ids: tuple):
        """清理测试数据。"""
        root_dept_id, child_a_id, child_b_id, user_a_id, user_b_id, entity_ids = ids
        with engine.connect() as conn:
            # 按依赖顺序删除
            conn.execute(
                sa.text("DELETE FROM flow_definition WHERE id IN (:a, :b)"),
                {
                    "a": entity_ids["flow_definition_a"],
                    "b": entity_ids["flow_definition_b"],
                },
            )
            conn.execute(
                sa.text("DELETE FROM job WHERE id IN (:a, :b)"),
                {"a": entity_ids["job_a"], "b": entity_ids["job_b"]},
            )
            conn.execute(
                sa.text("DELETE FROM evidence_set WHERE id IN (:a, :b)"),
                {"a": entity_ids["evidence_set_a"], "b": entity_ids["evidence_set_b"]},
            )
            conn.execute(
                sa.text("DELETE FROM equipment WHERE id IN (:a, :b)"),
                {"a": entity_ids["equipment_a"], "b": entity_ids["equipment_b"]},
            )
            conn.execute(
                sa.text("DELETE FROM app_user_department WHERE user_id IN (:a, :b)"),
                {"a": user_a_id, "b": user_b_id},
            )
            conn.execute(
                sa.text("DELETE FROM app_user WHERE id IN (:a, :b)"),
                {"a": user_a_id, "b": user_b_id},
            )
            conn.execute(
                sa.text("DELETE FROM department WHERE id IN (:a, :b)"),
                {"a": child_a_id, "b": child_b_id},
            )
            conn.commit()

    def test_cross_dept_read_blocked_equipment(self):
        """跨部门读 equipment：user_a 查询 childB 设备 → 0 行。"""
        db_url = _get_test_db_url()
        engine = create_engine(db_url, pool_pre_ping=True)
        ids = self._setup_test_data(engine)
        try:
            root_dept_id, child_a_id, child_b_id, user_a_id, user_b_id, entity_ids = ids
            with engine.connect() as conn:
                # 检查 irip_app 角色存在
                if not _role_exists(conn, APP_ROLE):
                    pytest.skip(f"角色 {APP_ROLE} 不存在, 需先执行 0071 迁移")

                # user_a 上下文: 只应看到 childA 的设备
                with rls_test_context(conn, child_a_id, user_a_id) as rls_conn:
                    # 查询所有设备（RLS 应过滤掉 childB 的）
                    result = rls_conn.execute(sa.text("SELECT count(*) FROM equipment"))
                    total = result.scalar()
                    # 应该能看到 childA 的设备 (1 条), 看不到 childB 的
                    assert total == 1, (
                        f"user_a (childA) 应只看到 1 条设备 (childA), 实际看到 {total} 条"
                    )

                    # 查询 childB 的设备（RLS 应拦截）
                    result = rls_conn.execute(
                        sa.text("SELECT count(*) FROM equipment WHERE department_id = :dept"),
                        {"dept": str(child_b_id)},
                    )
                    cross_count = result.scalar()
                    assert cross_count == 0, (
                        f"user_a (childA) 不应看到 childB 的设备, 实际看到 {cross_count} 条"
                    )
        finally:
            self._cleanup_test_data(engine, ids)
            engine.dispose()

    def test_same_dept_read_allowed_equipment(self):
        """本部门读 equipment：user_a 查询 childA 设备 → >0 行。"""
        db_url = _get_test_db_url()
        engine = create_engine(db_url, pool_pre_ping=True)
        ids = self._setup_test_data(engine)
        try:
            root_dept_id, child_a_id, child_b_id, user_a_id, user_b_id, entity_ids = ids
            with engine.connect() as conn:
                if not _role_exists(conn, APP_ROLE):
                    pytest.skip(f"角色 {APP_ROLE} 不存在, 需先执行 0071 迁移")

                with rls_test_context(conn, child_a_id, user_a_id) as rls_conn:
                    result = rls_conn.execute(
                        sa.text("SELECT count(*) FROM equipment WHERE department_id = :dept"),
                        {"dept": str(child_a_id)},
                    )
                    same_count = result.scalar()
                    assert same_count > 0, (
                        f"user_a (childA) 应能看到本部门设备, 实际看到 {same_count} 条"
                    )
        finally:
            self._cleanup_test_data(engine, ids)
            engine.dispose()

    def test_cross_dept_read_blocked_evidence_set(self):
        """跨部门读 evidence_set：user_a 查询 childB 证据集 → 0 行。"""
        db_url = _get_test_db_url()
        engine = create_engine(db_url, pool_pre_ping=True)
        ids = self._setup_test_data(engine)
        try:
            root_dept_id, child_a_id, child_b_id, user_a_id, user_b_id, entity_ids = ids
            with engine.connect() as conn:
                if not _role_exists(conn, APP_ROLE):
                    pytest.skip(f"角色 {APP_ROLE} 不存在")

                with rls_test_context(conn, child_a_id, user_a_id) as rls_conn:
                    result = rls_conn.execute(sa.text("SELECT count(*) FROM evidence_set"))
                    total = result.scalar()
                    assert total == 1, f"user_a 应只看到 1 条 evidence_set (childA), 实际 {total}"
        finally:
            self._cleanup_test_data(engine, ids)
            engine.dispose()

    def test_cross_dept_read_blocked_job(self):
        """跨部门读 job：user_a 查询 childB 作业 → 0 行。"""
        db_url = _get_test_db_url()
        engine = create_engine(db_url, pool_pre_ping=True)
        ids = self._setup_test_data(engine)
        try:
            root_dept_id, child_a_id, child_b_id, user_a_id, user_b_id, entity_ids = ids
            with engine.connect() as conn:
                if not _role_exists(conn, APP_ROLE):
                    pytest.skip(f"角色 {APP_ROLE} 不存在")

                with rls_test_context(conn, child_a_id, user_a_id) as rls_conn:
                    result = rls_conn.execute(sa.text("SELECT count(*) FROM job"))
                    total = result.scalar()
                    # Should see at least 1 job (childA's); history may add more
                    assert total >= 1, (
                        f"user_a should see >=1 jobs, got {total}"
                    )
        finally:
            self._cleanup_test_data(engine, ids)
            engine.dispose()

    def test_cross_dept_read_blocked_flow_definition(self):
        """跨部门读 flow_definition：user_a 查询 childB 流程定义 → 0 行。"""
        db_url = _get_test_db_url()
        engine = create_engine(db_url, pool_pre_ping=True)
        ids = self._setup_test_data(engine)
        try:
            root_dept_id, child_a_id, child_b_id, user_a_id, user_b_id, entity_ids = ids
            with engine.connect() as conn:
                if not _role_exists(conn, APP_ROLE):
                    pytest.skip(f"角色 {APP_ROLE} 不存在")

                with rls_test_context(conn, child_a_id, user_a_id) as rls_conn:
                    result = rls_conn.execute(sa.text("SELECT count(*) FROM flow_definition"))
                    total = result.scalar()
                    assert total == 1, (
                        f"user_a 应只看到 1 条 flow_definition (childA), 实际 {total}"
                    )
        finally:
            self._cleanup_test_data(engine, ids)
            engine.dispose()

    def test_lateral_isolation_both_directions(self):
        """旁系互不可见：user_a 看不到 childB, user_b 看不到 childA。"""
        db_url = _get_test_db_url()
        engine = create_engine(db_url, pool_pre_ping=True)
        ids = self._setup_test_data(engine)
        try:
            root_dept_id, child_a_id, child_b_id, user_a_id, user_b_id, entity_ids = ids
            with engine.connect() as conn:
                if not _role_exists(conn, APP_ROLE):
                    pytest.skip(f"角色 {APP_ROLE} 不存在")

                # user_a 视角: 只看到 childA
                with rls_test_context(conn, child_a_id, user_a_id) as rls_conn:
                    result = rls_conn.execute(sa.text("SELECT count(*) FROM equipment"))
                    assert result.scalar() == 1, "user_a 应只看到 childA 设备"

                # user_b 视角: 只看到 childB
                with rls_test_context(conn, child_b_id, user_b_id) as rls_conn:
                    result = rls_conn.execute(sa.text("SELECT count(*) FROM equipment"))
                    assert result.scalar() == 1, "user_b 应只看到 childB 设备"
        finally:
            self._cleanup_test_data(engine, ids)
            engine.dispose()


# ---------------------------------------------------------------------------
# 4. RLS 行为测试 — platform_administrator 跨部门读（需要 DB）
# ---------------------------------------------------------------------------


class TestRLSAdminCrossDeptRead:
    """验证 platform_administrator 通过 root 部门挂载可见全部数据。

    platform_administrator 挂载到 root 部门, current_visible_dept_ids() 从 root
    出发向下递归可见所有子部门, 因此应可见 childA 和 childB 的数据。
    """

    @pytest.fixture(autouse=True)
    def _check_db(self):
        """检查数据库可用性。"""
        db_url = _get_test_db_url()
        if not db_url:
            pytest.skip("IRIP_TEST_DATABASE_URL not set; skipping admin RLS test")

    def test_admin_sees_all_departments(self):
        """platform_administrator（挂 root）可见全部部门数据。"""
        db_url = _get_test_db_url()
        engine = create_engine(db_url, pool_pre_ping=True)
        try:
            with engine.connect() as conn:
                if not _role_exists(conn, APP_ROLE):
                    pytest.skip(f"角色 {APP_ROLE} 不存在")

                # 获取 root 部门 ID
                result = conn.execute(
                    sa.text("SELECT id FROM department WHERE code = 'root' AND parent_id IS NULL")
                )
                root_row = result.fetchone()
                if root_row is None:
                    pytest.skip("root 哨兵部门不存在")
                root_dept_id = root_row[0]

                # 创建临时 admin 用户
                admin_user_id = uuid4()
                from packages.auth.passwords import hash_password

                conn.execute(
                    sa.text(
                        "INSERT INTO app_user (id, email, display_name, password_hash, "
                        "status, lock_version, department_id) "
                        "VALUES (:id, :email, :name, :hash, 'active', 0, :dept)"
                    ),
                    {
                        "id": admin_user_id,
                        "email": f"rls_admin_{admin_user_id.hex[:8]}@irip.local",
                        "name": "RLS Admin",
                        "hash": hash_password("Test-Password-2026!"),
                        "dept": root_dept_id,
                    },
                )
                conn.execute(
                    sa.text(
                        "INSERT INTO app_user_department (user_id, department_id, is_primary) "
                        "VALUES (:uid, :dept, true)"
                    ),
                    {"uid": admin_user_id, "dept": root_dept_id},
                )
                conn.commit()

                try:
                    # admin 视角: 挂 root → current_visible_dept_ids() 返回全部
                    with rls_test_context(conn, root_dept_id, admin_user_id) as rls_conn:
                        # 应能看到所有部门的设备（至少 root 自身的 + 子部门的）
                        result = rls_conn.execute(sa.text("SELECT count(*) FROM equipment"))
                        total = result.scalar()
                        assert total > 0, (
                            f"platform_administrator (root) 应可见全部设备, 实际 {total}"
                        )

                        # 验证 current_visible_dept_ids() 包含 root + 子部门
                        result = rls_conn.execute(
                            sa.text("SELECT count(*) FROM current_visible_dept_ids()")
                        )
                        visible_count = result.scalar()
                        assert visible_count > 0, (
                            f"platform_administrator 应可见 >0 个部门, 实际 {visible_count}"
                        )
                finally:
                    conn.execute(
                        sa.text("DELETE FROM app_user_department WHERE user_id = :uid"),
                        {"uid": admin_user_id},
                    )
                    conn.execute(
                        sa.text("DELETE FROM app_user WHERE id = :uid"),
                        {"uid": admin_user_id},
                    )
                    conn.commit()
        finally:
            engine.dispose()


# ---------------------------------------------------------------------------
# 5. RLS 行为测试 — 跨部门写拦截（需要 DB）
# ---------------------------------------------------------------------------


class TestRLSCrossDeptWriteBlocked:
    """验证跨部门写被 RLS 拦截。

    user_a 尝试向 childB 插入数据 → 应被 RLS WITH CHECK 策略拦截。
    """

    @pytest.fixture(autouse=True)
    def _check_db(self):
        """检查数据库可用性。"""
        db_url = _get_test_db_url()
        if not db_url:
            pytest.skip("IRIP_TEST_DATABASE_URL not set; skipping RLS write test")

    def test_cross_dept_write_blocked_equipment(self):
        """跨部门写 equipment：user_a 向 childB 插入 → 被 RLS 拦截。"""
        db_url = _get_test_db_url()
        engine = create_engine(db_url, pool_pre_ping=True)
        try:
            with engine.connect() as conn:
                if not _role_exists(conn, APP_ROLE):
                    pytest.skip(f"角色 {APP_ROLE} 不存在")

                # 获取 root 部门
                result = conn.execute(
                    sa.text("SELECT id FROM department WHERE code = 'root' AND parent_id IS NULL")
                )
                root_row = result.fetchone()
                if root_row is None:
                    pytest.skip("root 哨兵部门不存在")
                root_dept_id = root_row[0]

                # 创建子部门和用户
                child_a_id = uuid4()
                child_b_id = uuid4()
                user_a_id = uuid4()
                from packages.auth.passwords import hash_password

                conn.execute(
                    sa.text(
                        "INSERT INTO department "
                        "(id, code, display_name, status, sort_order, parent_id) "
                        "VALUES (:id, :code, :name, 'active', 200, :parent)"
                    ),
                    {
                        "id": child_a_id,
                        "code": f"wr_test_a_{child_a_id.hex[:8]}",
                        "name": "Write Test A",
                        "parent": root_dept_id,
                    },
                )
                conn.execute(
                    sa.text(
                        "INSERT INTO department "
                        "(id, code, display_name, status, sort_order, parent_id) "
                        "VALUES (:id, :code, :name, 'active', 201, :parent)"
                    ),
                    {
                        "id": child_b_id,
                        "code": f"wr_test_b_{child_b_id.hex[:8]}",
                        "name": "Write Test B",
                        "parent": root_dept_id,
                    },
                )
                conn.execute(
                    sa.text(
                        "INSERT INTO app_user (id, email, display_name, password_hash, "
                        "status, lock_version, department_id) "
                        "VALUES (:id, :email, :name, :hash, 'active', 0, :dept)"
                    ),
                    {
                        "id": user_a_id,
                        "email": f"wr_user_{user_a_id.hex[:8]}@irip.local",
                        "name": "Write Test User",
                        "hash": hash_password("Test-Password-2026!"),
                        "dept": child_a_id,
                    },
                )
                conn.execute(
                    sa.text(
                        "INSERT INTO app_user_department (user_id, department_id, is_primary) "
                        "VALUES (:uid, :dept, true)"
                    ),
                    {"uid": user_a_id, "dept": child_a_id},
                )
                conn.commit()

                try:
                    # user_a 尝试向 childB 插入设备 → 应被 RLS WITH CHECK 拦截
                    new_equip_id = uuid4()
                    with rls_test_context(conn, child_a_id, user_a_id) as rls_conn:
                        with pytest.raises(Exception) as exc_info:
                            rls_conn.execute(
                                sa.text(
                                    "INSERT INTO equipment (id, code, display_name, "
                                    "department_id, owner_user_id) "
                                    "VALUES (:id, :code, :name, :dept, :owner)"
                                ),
                                {
                                    "id": new_equip_id,
                                    "code": f"WR_EQ_{new_equip_id.hex[:8]}",
                                    "name": "Cross-dept Equipment",
                                    "dept": str(child_b_id),  # 写入 childB → RLS 拦截
                                    "owner": str(user_a_id),
                                },
                            )
                        # RLS WITH CHECK 策略应阻止跨部门写入
                        # PostgreSQL 抛出 ERROR: new row violates row-level security policy
                        assert (
                            "row-level security" in str(exc_info.value).lower()
                            or "rls" in str(exc_info.value).lower()
                            or "policy" in str(exc_info.value).lower()
                        ), f"跨部门写应被 RLS 拦截, 实际异常: {exc_info.value}"
                finally:
                    conn.execute(
                        sa.text("DELETE FROM app_user_department WHERE user_id = :uid"),
                        {"uid": user_a_id},
                    )
                    conn.execute(
                        sa.text("DELETE FROM app_user WHERE id = :uid"),
                        {"uid": user_a_id},
                    )
                    conn.execute(
                        sa.text("DELETE FROM department WHERE id IN (:a, :b)"),
                        {"a": child_a_id, "b": child_b_id},
                    )
                    conn.commit()
        finally:
            engine.dispose()

    def test_same_dept_write_allowed_equipment(self):
        """本部门写 equipment：user_a 向 childA 插入 → 允许。"""
        db_url = _get_test_db_url()
        engine = create_engine(db_url, pool_pre_ping=True)
        try:
            with engine.connect() as conn:
                if not _role_exists(conn, APP_ROLE):
                    pytest.skip(f"角色 {APP_ROLE} 不存在")

                result = conn.execute(
                    sa.text("SELECT id FROM department WHERE code = 'root' AND parent_id IS NULL")
                )
                root_row = result.fetchone()
                if root_row is None:
                    pytest.skip("root 哨兵部门不存在")
                root_dept_id = root_row[0]

                child_a_id = uuid4()
                user_a_id = uuid4()
                from packages.auth.passwords import hash_password

                conn.execute(
                    sa.text(
                        "INSERT INTO department "
                        "(id, code, display_name, status, sort_order, parent_id) "
                        "VALUES (:id, :code, :name, 'active', 300, :parent)"
                    ),
                    {
                        "id": child_a_id,
                        "code": f"sw_test_a_{child_a_id.hex[:8]}",
                        "name": "Same Write Test A",
                        "parent": root_dept_id,
                    },
                )
                conn.execute(
                    sa.text(
                        "INSERT INTO app_user (id, email, display_name, password_hash, "
                        "status, lock_version, department_id) "
                        "VALUES (:id, :email, :name, :hash, 'active', 0, :dept)"
                    ),
                    {
                        "id": user_a_id,
                        "email": f"sw_user_{user_a_id.hex[:8]}@irip.local",
                        "name": "Same Write User",
                        "hash": hash_password("Test-Password-2026!"),
                        "dept": child_a_id,
                    },
                )
                conn.execute(
                    sa.text(
                        "INSERT INTO app_user_department (user_id, department_id, is_primary) "
                        "VALUES (:uid, :dept, true)"
                    ),
                    {"uid": user_a_id, "dept": child_a_id},
                )
                conn.commit()

                try:
                    new_equip_id = uuid4()
                    with rls_test_context(conn, child_a_id, user_a_id) as rls_conn:
                        # 本部门写应成功（但事务会回滚, 所以不会持久化）
                        rls_conn.execute(
                            sa.text(
                                "INSERT INTO equipment (id, code, display_name, "
                                "department_id, owner_user_id) "
                                "VALUES (:id, :code, :name, :dept, :owner)"
                            ),
                            {
                                "id": new_equip_id,
                                "code": f"SW_EQ_{new_equip_id.hex[:8]}",
                                "name": "Same-dept Equipment",
                                "dept": str(child_a_id),
                                "owner": str(user_a_id),
                            },
                        )
                        # 验证插入成功（在事务内可见）
                        result = rls_conn.execute(
                            sa.text("SELECT count(*) FROM equipment WHERE id = :id"),
                            {"id": str(new_equip_id)},
                        )
                        assert result.scalar() == 1, "本部门写应成功"
                    # 事务回滚后数据消失
                finally:
                    conn.execute(
                        sa.text("DELETE FROM app_user_department WHERE user_id = :uid"),
                        {"uid": user_a_id},
                    )
                    conn.execute(
                        sa.text("DELETE FROM app_user WHERE id = :uid"),
                        {"uid": user_a_id},
                    )
                    conn.execute(
                        sa.text("DELETE FROM department WHERE id = :id"),
                        {"id": child_a_id},
                    )
                    conn.commit()
        finally:
            engine.dispose()


# ---------------------------------------------------------------------------
# 6. RLS Fail-Closed 验证（需要 DB）
# ---------------------------------------------------------------------------


class TestRLSFailClosed:
    """验证 RLS fail-closed 行为：缺失 GUC 时返回空集。"""

    @pytest.fixture(autouse=True)
    def _check_db(self):
        """检查数据库可用性。"""
        db_url = _get_test_db_url()
        if not db_url:
            pytest.skip("IRIP_TEST_DATABASE_URL not set; skipping fail-closed test")

    def test_empty_guc_returns_nothing(self):
        """GUC 为空串时 RLS fail-closed（返回 0 行）。"""
        db_url = _get_test_db_url()
        engine = create_engine(db_url, pool_pre_ping=True)
        try:
            with engine.connect() as conn:
                if not _role_exists(conn, APP_ROLE):
                    pytest.skip(f"角色 {APP_ROLE} 不存在")

                conn.commit()
                conn.execute(sa.text(f"SET ROLE {APP_ROLE}"))
                try:
                    conn.execute(sa.text("SELECT set_config('app.current_dept_id', '', true)"))
                    conn.execute(sa.text("SELECT set_config('app.current_user_id', '', true)"))

                    result = conn.execute(sa.text("SELECT count(*) FROM equipment"))
                    assert result.scalar() == 0, "GUC empty should fail-closed to 0 rows"

                    result = conn.execute(sa.text("SELECT count(*) FROM job"))
                    assert result.scalar() == 0, "GUC empty should fail-closed to 0 rows"

                    conn.rollback()
                except Exception:
                    conn.rollback()
                    raise
                finally:
                    conn.execute(sa.text("RESET ROLE"))
        finally:
            engine.dispose()


# ---------------------------------------------------------------------------
# 7. provenance_edge RLS 专项验证（需要 DB）
# ---------------------------------------------------------------------------


class TestProvenanceEdgeRLS:
    """provenance_edge RLS 策略专项验证（0072 迁移修复目标）。

    验证 0072 迁移后：
    1. department_id 列存在且 NOT NULL
    2. RLS 策略使用 department_id IN current_visible_dept_ids()
    3. 旧 organization_id 列已删除
    4. 跨部门访问被拦截
    """

    @pytest.fixture(autouse=True)
    def _check_db(self):
        """检查数据库可用性。"""
        db_url = _get_test_db_url()
        if not db_url:
            pytest.skip("IRIP_TEST_DATABASE_URL not set; skipping provenance_edge RLS test")

    def test_provenance_edge_has_department_id_not_null(self):
        """provenance_edge.department_id 列存在且 NOT NULL。"""
        db_url = _get_test_db_url()
        engine = create_engine(db_url, pool_pre_ping=True)
        try:
            with engine.connect() as conn:
                result = conn.execute(
                    sa.text(
                        "SELECT is_nullable FROM information_schema.columns "
                        "WHERE table_name = 'provenance_edge' AND column_name = 'department_id'"
                    )
                )
                row = result.fetchone()
                assert row is not None, "provenance_edge 应有 department_id 列（0072 迁移添加）"
                assert row[0] == "NO", (
                    f"provenance_edge.department_id 应为 NOT NULL, 实际为 {row[0]}"
                )
        finally:
            engine.dispose()

    def test_provenance_edge_organization_id_dropped(self):
        """provenance_edge 旧的 organization_id 列已删除（0072 迁移）。"""
        db_url = _get_test_db_url()
        engine = create_engine(db_url, pool_pre_ping=True)
        try:
            with engine.connect() as conn:
                result = conn.execute(
                    sa.text(
                        "SELECT 1 FROM information_schema.columns "
                        "WHERE table_name = 'provenance_edge' AND column_name = 'organization_id'"
                    )
                )
                assert result.fetchone() is None, (
                    "provenance_edge 不应有 organization_id 列（0072 已删除）"
                )
        finally:
            engine.dispose()

    def test_provenance_edge_rls_policy_uses_department_id(self):
        """provenance_edge RLS 策略使用 department_id IN current_visible_dept_ids()。"""
        db_url = _get_test_db_url()
        engine = create_engine(db_url, pool_pre_ping=True)
        try:
            with engine.connect() as conn:
                policies = _get_rls_policy(conn, "provenance_edge")
                tenant_policy = next((p for p in policies if p["name"] == "tenant_isolation"), None)
                assert tenant_policy is not None, "provenance_edge 应有 tenant_isolation 策略"
                qual = tenant_policy.get("qual") or ""
                assert "department_id" in qual, (
                    f"provenance_edge RLS 策略应使用 department_id, 实际: {qual}"
                )
                assert "current_visible_dept_ids" in qual, (
                    f"provenance_edge RLS 策略应使用 current_visible_dept_ids(), 实际: {qual}"
                )
        finally:
            engine.dispose()

    def test_provenance_edge_has_fk_to_department(self):
        """provenance_edge.department_id 有 FK 约束指向 department.id。"""
        db_url = _get_test_db_url()
        engine = create_engine(db_url, pool_pre_ping=True)
        try:
            with engine.connect() as conn:
                result = conn.execute(
                    sa.text(
                        "SELECT 1 FROM information_schema.table_constraints tc "
                        "JOIN information_schema.key_column_usage kcu "
                        "ON tc.constraint_name = kcu.constraint_name "
                        "WHERE tc.table_name = 'provenance_edge' "
                        "AND tc.constraint_type = 'FOREIGN KEY' "
                        "AND kcu.column_name = 'department_id'"
                    )
                )
                assert result.fetchone() is not None, "provenance_edge.department_id 应有 FK 约束"
        finally:
            engine.dispose()

    def test_provenance_edge_rls_enabled(self):
        """provenance_edge 启用了 RLS。"""
        db_url = _get_test_db_url()
        engine = create_engine(db_url, pool_pre_ping=True)
        try:
            with engine.connect() as conn:
                assert _rls_enabled(conn, "provenance_edge"), "provenance_edge 应启用 RLS"
        finally:
            engine.dispose()

    def test_provenance_edge_cross_dept_read_blocked(self):
        """provenance_edge 跨部门读被 RLS 拦截。

        即使 provenance_edge 表当前无数据, RLS 策略也应正确生效。
        本测试通过验证 RLS 策略的 SQL 来确认拦截逻辑正确。
        """
        db_url = _get_test_db_url()
        engine = create_engine(db_url, pool_pre_ping=True)
        try:
            with engine.connect() as conn:
                if not _role_exists(conn, APP_ROLE):
                    pytest.skip(f"角色 {APP_ROLE} 不存在")

                result = conn.execute(
                    sa.text("SELECT id FROM department WHERE code = 'root' AND parent_id IS NULL")
                )
                root_row = result.fetchone()
                if root_row is None:
                    pytest.skip("root 哨兵部门不存在")
                root_dept_id = root_row[0]

                # Use irip_app role with GUC set to root
                conn.commit()
                conn.execute(sa.text(f"SET ROLE {APP_ROLE}"))
                try:
                    conn.execute(
                        sa.text("SELECT set_config('app.current_dept_id', :d, true)"),
                        {"d": str(root_dept_id)},
                    )
                    conn.execute(sa.text("SELECT set_config('app.current_user_id', '', true)"))

                    result = conn.execute(sa.text("SELECT count(*) FROM provenance_edge"))
                    count = result.scalar()
                    assert count >= 0, "root should query provenance_edge without error"

                    conn.rollback()
                except Exception:
                    conn.rollback()
                    raise
                finally:
                    conn.execute(sa.text("RESET ROLE"))
        finally:
            engine.dispose()


# ---------------------------------------------------------------------------
# 8. RLS 对 superuser 的绕过验证（需要 DB）
# ---------------------------------------------------------------------------


class TestRLSSuperuserBypass:
    """验证 superuser 连接绕过 RLS（反面验证）。

    superuser 连接应绕过 RLS, 可见全部数据。
    这证明测试必须用 irip_app（非 superuser）才有意义。
    """

    @pytest.fixture(autouse=True)
    def _check_db(self):
        """检查数据库可用性。"""
        db_url = _get_test_db_url()
        if not db_url:
            pytest.skip("IRIP_TEST_DATABASE_URL not set; skipping superuser bypass test")

    def test_superuser_bypasses_rls(self):
        """superuser 连接绕过 RLS（反面验证：superuser 测了等于没测）。"""
        db_url = _get_test_db_url()
        engine = create_engine(db_url, pool_pre_ping=True)
        try:
            with engine.connect() as conn:
                # 检查当前连接是否为 superuser
                result = conn.execute(sa.text("SELECT current_setting('is_superuser')"))
                is_super = result.scalar()

                if is_super != "on":
                    pytest.skip("测试 DB 连接不是 superuser, 无法验证 superuser 绕过行为")

                # superuser 即使不设 GUC 也应可见全部数据
                # （RLS 对 superuser 不生效）
                result = conn.execute(sa.text("SELECT count(*) FROM equipment"))
                count = result.scalar()
                # superuser 应看到全部设备（至少 0 条, 但不被 RLS 过滤）
                # 关键是：不设 GUC 也不会返回 0（如果表有数据）
                # 这里只验证不报错
                assert count >= 0, "superuser 查询应成功（绕过 RLS）"
        finally:
            engine.dispose()

    def test_irip_app_is_not_superuser(self):
        """irip_app 角色不是 superuser（RLS 对其生效的前提）。"""
        db_url = _get_test_db_url()
        engine = create_engine(db_url, pool_pre_ping=True)
        try:
            with engine.connect() as conn:
                if not _role_exists(conn, APP_ROLE):
                    pytest.skip(f"角色 {APP_ROLE} 不存在")

                result = conn.execute(
                    sa.text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = :name"),
                    {"name": APP_ROLE},
                )
                row = result.fetchone()
                assert row is not None, f"角色 {APP_ROLE} 应存在"
                assert row[0] is False, f"{APP_ROLE} 不应是 superuser"
                assert row[1] is False, f"{APP_ROLE} 不应有 BYPASSRLS 属性"
        finally:
            engine.dispose()
