"""0071: RLS 通电 — system_service 挂 root + irip_app 启用 + irip 去 BYPASSRLS

操作：
1. 将 system_service 用户（system@irip.local）添加到 root 哨兵部门
   （app_user_department, is_primary=false），使其通过 current_visible_dept_ids()
   可见全部部门（root 的向下递归覆盖所有子部门）。
   这解决 Worker/Beat 无用户上下文场景的 RLS 可见性问题：
   - reaper / retry_wait_jobs 需要跨部门扫描 job 表
   - JobExecutor 需要先读 job 才能拿到 department_id（鸡生蛋问题）
   - retention_cleanup 需要可见 system 哨兵部门的 backup_record

2. 启用 irip_app 角色登录 + 授予 DML 权限。
   irip 是 PostgreSQL bootstrap superuser（initdb 创建），其 SUPERUSER
   属性不可移除（PG 安全机制）。因此运行时连接改用 irip_app（非 superuser，
   rolsuper=f, rolbypassrls=f），RLS 对其生效。
   irip 保留为迁移角色（superuser，可 DDL + 绕过 RLS）。

3. ALTER ROLE irip NOBYPASSRLS — 去掉 BYPASSRLS 属性。
   虽然 irip 仍为 superuser（RLS 对 superuser 无效），但此属性变更
   为未来可能的 NOSUPERUSER 迁移做准备，且语义上更清晰。

前置条件（已在 0065/0069 完成）：
- 19 张表已 ENABLE + FORCE ROW LEVEL SECURITY
- 18 个 tenant_isolation 策略已创建（A 类 10 表 + B 类 6 表 + experiment_project + AI 会话 2 表）
- current_visible_dept_ids() DB 函数已创建（SECURITY DEFINER, 读 app.current_user_id）
- forbid_reprivatize() / protect_sentinel_dept() 触发器已挂载

回退（downgrade）：
- 恢复 BYPASSRLS
- 不删除 app_user_department 关联（无副作用）
- 不撤销 irip_app 权限（无副作用）

Revision ID: 0071
Revises: 0070
Create Date: 2026-09-01
"""

from alembic import op

revision = "0071"
down_revision = "0070"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """RLS 通电：system_service 挂 root + irip_app 启用 + irip 去 BYPASSRLS。"""

    # === 1. system_service 用户挂 root 部门（全部门可见） ===
    op.execute(
        """
        INSERT INTO app_user_department (user_id, department_id, is_primary, created_at)
        SELECT au.id, d.id, false, now()
        FROM app_user au, department d
        WHERE au.email = 'system@irip.local'
          AND d.code = 'root'
          AND d.parent_id IS NULL
          AND NOT EXISTS (
              SELECT 1 FROM app_user_department aud
              WHERE aud.user_id = au.id AND aud.department_id = d.id
          )
        """
    )

    # === 2. 启用 irip_app 角色登录 + 授予 DML 权限 ===
    # irip_app 原为 NOLOGIN 角色（rolcanlogin=f），启用登录后作为运行时连接角色。
    # 密码与 irip 相同（开发环境），生产环境应使用独立密码。
    op.execute("ALTER ROLE irip_app LOGIN PASSWORD 'irip_dev_password'")
    op.execute("GRANT USAGE ON SCHEMA public TO irip_app")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO irip_app"
    )
    op.execute(
        "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO irip_app"
    )
    # 未来由 irip 创建的表自动授予 irip_app DML 权限
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE irip IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO irip_app"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE irip IN SCHEMA public "
        "GRANT USAGE, SELECT ON SEQUENCES TO irip_app"
    )

    # === 3. 去掉 irip 角色的 BYPASSRLS ===
    # irip 仍为 superuser（bootstrap 限制不可移除），但去掉 BYPASSRLS
    # 为未来迁移做准备。运行时连接改用 irip_app，RLS 对 irip_app 生效。
    op.execute("ALTER ROLE irip NOBYPASSRLS")


def downgrade() -> None:
    """紧急回退：恢复 BYPASSRLS（RLS 策略仍在但不生效）。"""

    # 恢复 BYPASSRLS
    op.execute("ALTER ROLE irip BYPASSRLS")

    # 不撤销 irip_app 权限（无副作用，保留也无害）
    # 不删除 app_user_department 关联（无副作用，保留也无害）
