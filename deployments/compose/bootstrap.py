"""IRIP 平台幂等引导脚本。

阶段2 多租户隔离键升级：删除 organization 逻辑，改为幂等创建 root + system 哨兵部门。

功能：
  1. 确保 5 个内置角色存在（INSERT ON CONFLICT DO UPDATE）；
  2. 创建 root 哨兵部门（code='root', parent_id=NULL）+ system 哨兵部门（code='system', parent_id=root.id）；
  3. 创建管理员用户（admin@irip.local，密码从环境变量读，挂 root 部门）；
  4. 确保 MinIO bucket 存在；
  5. 赋予 irip 用户 REPLICATION 权限。

全部操作幂等：重复运行不报错、不重复创建。

用法（Docker Compose）：
  docker compose run --rm bootstrap

用法（本机）：
  IRIP_DATABASE_URL=... IRIP_BOOTSTRAP_ADMIN_PASSWORD=... \
  python -m deployments.compose.bootstrap
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.auth.passwords import hash_password
from packages.auth.permissions import BUILTIN_ROLES
from packages.common.ids import new_id
from packages.common.s3_repository import S3Repository

logger = logging.getLogger(__name__)

#: 引导管理员默认邮箱。
ADMIN_EMAIL: str = "admin@irip.local"

#: 引导管理员默认显示名。
ADMIN_DISPLAY_NAME: str = "平台管理员"

#: 引导管理员默认角色。
ADMIN_ROLE_CODE: str = "platform_administrator"

#: 系统服务用户邮箱（不可登录，仅用于 worker FK 引用）。
SYSTEM_SERVICE_EMAIL: str = "system@irip.local"

#: 系统服务用户显示名。
SYSTEM_SERVICE_DISPLAY_NAME: str = "系统服务"

#: 系统服务用户角色（复用平台管理员角色）。
SYSTEM_SERVICE_ROLE_CODE: str = "platform_administrator"


@dataclass(frozen=True)
class SentinelDepartment:
    """哨兵部门信息。"""

    id: UUID
    code: str
    display_name: str


class SentinelDepartmentRepository:
    """哨兵部门数据访问（幂等）。"""

    @staticmethod
    async def ensure_root_and_system(session: AsyncSession) -> tuple[SentinelDepartment, SentinelDepartment]:
        """幂等创建 root + system 哨兵部门。

        root: code='root', parent_id=NULL, display_name 读 IRIP_ROOT_DEPT_NAME 环境变量
        system: code='system', parent_id=root.id, display_name="系统室"

        唯一约束为 (parent_id, code)，root 的 parent_id 为 NULL，
        PostgreSQL 对 NULL 的处理：ON CONFLICT (parent_id, code) 不匹配 NULL，
        因此使用 DO $$ 块手动检查幂等。

        Returns:
            tuple[SentinelDepartment, SentinelDepartment]: (root, system) 部门信息。
        """
        root_display_name = os.getenv("IRIP_ROOT_DEPT_NAME", "IRIP 研究院")

        # 获取已存在的组织 ID（从任意 department 或 app_user 取）
        org_result = await session.execute(
            sa.text("SELECT id FROM department LIMIT 1")
        )
        org_row = org_result.first()
        org_id: str | None = org_row[0] if org_row else None

        if org_id is None:
            org_result = await session.execute(
                sa.text("SELECT id FROM app_user LIMIT 1")
            )
            org_row = org_result.first()
            org_id = str(org_row[0]) if org_row else "00000000-0000-0000-0000-000000000001"

        # 创建或获取 root 部门
        existing_root = await session.execute(
            sa.text("SELECT id, display_name FROM department WHERE code = 'root' AND parent_id IS NULL LIMIT 1")
        )
        root_row = existing_root.first()
        if root_row:
            root_id = UUID(str(root_row[0]))
        else:
            root_id = new_id()
            await session.execute(
                sa.text(
                    "INSERT INTO department (id, code, display_name, "
                    "description, status, sort_order, lock_version, parent_id) "
                    "VALUES (:id, :org, 'root', :name, "
                    "'系统根部门（哨兵），全组织公共数据归属', 'active', -1, 0, NULL)"
                ),
                {"id": str(root_id), "org": org_id, "name": root_display_name},
            )

        root = SentinelDepartment(id=root_id, code="root", display_name=root_display_name)

        # 创建或获取 system 部门
        existing_system = await session.execute(
            sa.text("SELECT id, display_name FROM department WHERE code = 'system' LIMIT 1")
        )
        system_row = existing_system.first()
        if system_row:
            system_id = UUID(str(system_row[0]))
        else:
            system_id = new_id()
            await session.execute(
                sa.text(
                    "INSERT INTO department (id, code, display_name, "
                    "description, status, sort_order, lock_version, parent_id) "
                    "VALUES (:id, :org, 'system', '系统室', "
                    "'系统级数据归属（密钥/连接器/备份等）', 'active', -2, 0, :root_id)"
                ),
                {"id": str(system_id), "org": org_id, "root_id": str(root_id)},
            )

        system = SentinelDepartment(id=system_id, code="system", display_name="系统室")

        return root, system


class RoleRepository:
    """角色数据访问（幂等）。"""

    @staticmethod
    async def ensure_builtin_roles(session: AsyncSession) -> None:
        """确保 5 个内置角色存在（INSERT ON CONFLICT DO UPDATE）。

        迁移 0003 已种子角色，此方法保证 bootstrap 独立运行时角色也存在。
        """
        for code, info in BUILTIN_ROLES.items():
            display_name = info["display_name"]
            permissions = info["permissions"]
            if not isinstance(permissions, list):
                continue
            await session.execute(
                sa.text(
                    "INSERT INTO role (code, display_name, permissions) "
                    "VALUES (:code, :display_name, "
                    "CAST(:permissions AS jsonb)) "
                    "ON CONFLICT (code) DO UPDATE SET "
                    "display_name = EXCLUDED.display_name, "
                    "permissions = EXCLUDED.permissions"
                ),
                {
                    "code": code,
                    "display_name": display_name,
                    "permissions": json.dumps([str(p) for p in permissions]),
                },
            )


class UserRepository:
    """用户数据访问（幂等）。"""

    @staticmethod
    async def get_or_create_admin(
        session: AsyncSession,
        department_id: UUID,
        email: str,
        password: str,
        display_name: str = ADMIN_DISPLAY_NAME,
    ) -> UUID:
        """幂等获取或创建管理员用户（阶段2：挂 root 部门）。

        若 admin@irip.local 不存在则创建（含 platform_administrator 角色，
        department_id=root.id），已存在则返回其 ID。密码仅在创建时设置。

        幂等角色修复：若管理员已存在但 roles 为空（历史遗留），
        补写 platform_administrator 角色。

        幂等部门修复：若管理员已存在但 department_id 为空，
        补写 root 部门 ID。

        Returns:
            UUID: 管理员用户 ID。
        """
        result = await session.execute(
            sa.text("SELECT id, roles, department_id FROM app_user WHERE email = :email"),
            {"email": email},
        )
        existing = result.first()
        if existing is not None:
            user_id = UUID(str(existing[0]))
            existing_roles = existing[1]
            existing_dept = existing[2]

            if not existing_roles:
                await session.execute(
                    sa.text(
                        "UPDATE app_user SET roles = CAST(:roles AS jsonb) "
                        "WHERE id = :uid"
                    ),
                    {
                        "roles": json.dumps([ADMIN_ROLE_CODE]),
                        "uid": str(user_id),
                    },
                )

            # 阶段2: 补写 department_id（如有历史用户无部门）
            if existing_dept is None:
                await session.execute(
                    sa.text(
                        "UPDATE app_user SET department_id = :dept_id "
                        "WHERE id = :uid"
                    ),
                    {
                        "dept_id": str(department_id),
                        "uid": str(user_id),
                    },
                )

            return user_id

        user_id = new_id()
        password_hash = hash_password(password)
        await session.execute(
            sa.text(
                "INSERT INTO app_user "
                "(id, email, display_name, department_id, "
                "password_hash, status, lock_version, roles) "
                "VALUES (:id, :email, :name, :dept_id, :hash, 'active', 0, "
                "CAST(:roles AS jsonb))"
            ),
            {
                "id": str(user_id),
                "email": email,
                "name": display_name,
                "dept_id": str(department_id),
                "hash": password_hash,
                "roles": json.dumps([ADMIN_ROLE_CODE]),
            },
        )

        # 同时创建 app_user_department 关联（is_primary=true）
        await session.execute(
            sa.text(
                "INSERT INTO app_user_department (user_id, department_id, is_primary) "
                "VALUES (:uid, :dept_id, true) "
                "ON CONFLICT (user_id, department_id) DO NOTHING"
            ),
            {
                "uid": str(user_id),
                "dept_id": str(department_id),
            },
        )

        return user_id

    @staticmethod
    async def get_or_create_system_service(
        session: AsyncSession,
        department_id: UUID,
        email: str = SYSTEM_SERVICE_EMAIL,
        display_name: str = SYSTEM_SERVICE_DISPLAY_NAME,
    ) -> UUID:
        """幂等获取或创建系统服务用户（挂 system 哨兵部门）。

        系统服务用户用于 Celery worker 无用户会话时，作为 actor_id /
        created_by / uploaded_by 的合法 app_user 引用。无密码（password_hash
        为空字符串，不可登录），状态为 active。

        若 system@irip.local 不存在则创建（含 platform_administrator 角色，
        department_id=system 哨兵部门 ID），已存在则返回其 ID。

        幂等角色修复：若系统服务用户已存在但 roles 为空，补写角色。
        幂等部门修复：若系统服务用户已存在但 department_id 为空，补写部门。

        Args:
            session: 异步数据库会话。
            department_id: system 哨兵部门 ID。
            email: 系统服务用户邮箱。
            display_name: 系统服务用户显示名。

        Returns:
            UUID: 系统服务用户 ID。
        """
        result = await session.execute(
            sa.text("SELECT id, roles, department_id FROM app_user WHERE email = :email"),
            {"email": email},
        )
        existing = result.first()
        if existing is not None:
            user_id = UUID(str(existing[0]))
            existing_roles = existing[1]
            existing_dept = existing[2]

            if not existing_roles:
                await session.execute(
                    sa.text(
                        "UPDATE app_user SET roles = CAST(:roles AS jsonb) "
                        "WHERE id = :uid"
                    ),
                    {
                        "roles": json.dumps([SYSTEM_SERVICE_ROLE_CODE]),
                        "uid": str(user_id),
                    },
                )

            if existing_dept is None:
                await session.execute(
                    sa.text(
                        "UPDATE app_user SET department_id = :dept_id "
                        "WHERE id = :uid"
                    ),
                    {
                        "dept_id": str(department_id),
                        "uid": str(user_id),
                    },
                )

            return user_id

        user_id = new_id()
        await session.execute(
            sa.text(
                "INSERT INTO app_user "
                "(id, email, display_name, department_id, "
                "password_hash, status, lock_version, roles) "
                "VALUES (:id, :email, :name, :dept_id, '', 'active', 0, "
                "CAST(:roles AS jsonb))"
            ),
            {
                "id": str(user_id),
                "email": email,
                "name": display_name,
                "dept_id": str(department_id),
                "roles": json.dumps([SYSTEM_SERVICE_ROLE_CODE]),
            },
        )

        # 同时创建 app_user_department 关联（is_primary=true）
        await session.execute(
            sa.text(
                "INSERT INTO app_user_department (user_id, department_id, is_primary) "
                "VALUES (:uid, :dept_id, true) "
                "ON CONFLICT (user_id, department_id) DO NOTHING"
            ),
            {
                "uid": str(user_id),
                "dept_id": str(department_id),
            },
        )

        return user_id

    @staticmethod
    async def count_by_email(session: AsyncSession, email: str) -> int:
        """按邮箱统计用户数（用于幂等性验证）。"""
        result = await session.execute(
            sa.text("SELECT count(*) FROM app_user WHERE email = :email"),
            {"email": email},
        )
        count: int = result.scalar() or 0
        return count


class ArtifactBootstrap:
    """MinIO bucket 引导（幂等）。"""

    def __init__(self, s3_repo: S3Repository) -> None:
        """初始化工件引导。"""
        self._s3 = s3_repo

    def ensure_buckets(self) -> None:
        """确保默认 bucket 存在。"""
        self._s3.ensure_bucket()


class DepartmentSeeder:
    """实验室种子数据引导（幂等，P1）。

    使用 (parent_id, code) 唯一约束（已删除旧 org 依赖）。
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """初始化实验室种子引导。

        Args:
            session_factory: 异步会话工厂。
        """
        self._factory = session_factory

    async def seed_departments(self, root_dept_id: UUID) -> None:
        """幂等创建种子实验室。

        读取 IRIP_SEED_DEPARTMENTS 环境变量（JSON 数组），每项格式：
        ``{"code": "...", "display_name": "...", "description": "...", "sort_order": 0, "parent_id": "..."}``

        使用 ON CONFLICT (parent_id, code) DO NOTHING 保证幂等。
        未设置环境变量时跳过。parent_id 未指定时默认挂 root。

        Args:
            root_dept_id: root 哨兵部门 ID（作为默认 parent_id）。
        """
        raw = os.getenv("IRIP_SEED_DEPARTMENTS", "")
        if not raw:
            logger.info("Bootstrap: IRIP_SEED_DEPARTMENTS not set, skipping department seed")
            return

        try:
            departments = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning("Bootstrap: failed to parse IRIP_SEED_DEPARTMENTS: %s", exc)
            return

        if not isinstance(departments, list):
            logger.warning("Bootstrap: IRIP_SEED_DEPARTMENTS is not a JSON array, skipping")
            return

        # 获取组织 ID
        async with self._factory() as session:
            org_result = await session.execute(
                sa.text("SELECT id FROM department WHERE id = :root_id"),
                {"root_id": str(root_dept_id)},
            )
            org_row = org_result.first()
            org_id: str = str(org_row[0]) if org_row else "00000000-0000-0000-0000-000000000001"

        async with self._factory() as session:
            async with session.begin():
                for dept in departments:
                    if not isinstance(dept, dict):
                        continue
                    code = dept.get("code")
                    display_name = dept.get("display_name")
                    if not code or not display_name:
                        continue
                    description = dept.get("description")
                    sort_order = int(dept.get("sort_order", 0))
                    parent_id_str = dept.get("parent_id")
                    parent_id = str(parent_id_str) if parent_id_str else str(root_dept_id)

                    await session.execute(
                        sa.text(
                            "INSERT INTO department "
                            "(id, code, display_name, description, "
                            "status, sort_order, lock_version, parent_id) "
                            "VALUES (:org, :code, :name, :desc, 'active', :sort, 0, :parent) "
                            "ON CONFLICT (parent_id, code) DO NOTHING"
                        ),
                        {
                            "org": org_id,
                            "code": code,
                            "name": display_name,
                            "desc": description,
                            "sort": sort_order,
                            "parent": parent_id,
                        },
                    )
        logger.info("Bootstrap: seeded %d departments", len(departments))


class ApplicationContainer:
    """应用 DI 容器（bootstrap 用）。

    阶段2：删除 organization 端口，新增 sentinel 端口。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        s3_repo: S3Repository,
    ) -> None:
        """初始化容器。

        Args:
            session_factory: 异步会话工厂。
            s3_repo: S3 客户端。
        """
        self._factory = session_factory
        self._s3 = s3_repo
        self.sentinel = _SentinelPort(session_factory)
        self.roles = _RolesPort(session_factory)
        self.users = _UsersPort(session_factory)
        self.artifacts = ArtifactBootstrap(s3_repo)
        self.departments = DepartmentSeeder(session_factory)


class _SentinelPort:
    """哨兵部门端口。"""

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def ensure_root_and_system(self) -> tuple[SentinelDepartment, SentinelDepartment]:
        """幂等创建 root + system 哨兵部门。"""
        async with self._factory() as session:
            async with session.begin():
                return await SentinelDepartmentRepository.ensure_root_and_system(session)


class _RolesPort:
    """角色端口。"""

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def ensure_builtin_roles(self) -> None:
        """确保 5 个内置角色存在。"""
        async with self._factory() as session:
            async with session.begin():
                await RoleRepository.ensure_builtin_roles(session)


class _UsersPort:
    """用户端口。"""

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def get_or_create_admin(
        self,
        department_id: UUID,
        email: str,
        password_from_env: str,
    ) -> UUID:
        """幂等获取或创建管理员用户（挂 root 部门）。

        Args:
            department_id: root 哨兵部门 ID。
            email: 管理员邮箱。
            password_from_env: 环境变量名（从中读取密码）。

        Returns:
            UUID: 管理员用户 ID。
        """
        password = os.getenv(password_from_env, "")
        if not password:
            raise RuntimeError(
                f"Environment variable {password_from_env} is required for bootstrap"
            )
        async with self._factory() as session:
            async with session.begin():
                return await UserRepository.get_or_create_admin(
                    session,
                    department_id=department_id,
                    email=email,
                    password=password,
                )

    async def get_or_create_system_service(self, department_id: UUID) -> UUID:
        """幂等获取或创建系统服务用户（挂 system 哨兵部门）。

        系统服务用户无密码、不可登录，仅用于 Celery worker 作为
        actor_id / created_by / uploaded_by 的合法 app_user 引用。

        Args:
            department_id: system 哨兵部门 ID。

        Returns:
            UUID: 系统服务用户 ID。
        """
        async with self._factory() as session:
            async with session.begin():
                return await UserRepository.get_or_create_system_service(
                    session,
                    department_id=department_id,
                )


async def bootstrap_platform(container: ApplicationContainer) -> None:
    """幂等引导平台：哨兵部门 → 角色 → 管理员 → 种子部门 → bucket。

    阶段2：删除 organization 创建逻辑，改为创建 root + system 哨兵部门。
    admin 用户挂 root 部门（department_id=root.id）。

    Args:
        container: 应用 DI 容器。
    """
    # F-12: 确保有可用的 master key（envelope encryption）
    master_key = os.getenv("IRIP_MASTER_KEY", "")
    if not master_key:
        from packages.common.crypto import generate_master_key

        generated_key = generate_master_key()
        logger.warning(
            "IRIP_MASTER_KEY not set. Generated random master key for envelope encryption.\n"
            "Please set IRIP_MASTER_KEY=%s in your environment for production use.\n"
            "WARNING: Data encrypted with this key will not be recoverable after restart "
            "unless this key is persisted.",
            generated_key,
        )

    logger.info("Bootstrap: ensuring sentinel departments (root + system) ...")
    root_dept, system_dept = await container.sentinel.ensure_root_and_system()
    logger.info(
        "Bootstrap: sentinel departments ready (root=%s, system=%s)",
        root_dept.id,
        system_dept.id,
    )

    logger.info("Bootstrap: ensuring builtin roles ...")
    await container.roles.ensure_builtin_roles()
    logger.info("Bootstrap: roles ready")

    logger.info("Bootstrap: ensuring admin user %s (department=root) ...", ADMIN_EMAIL)
    await container.users.get_or_create_admin(
        department_id=root_dept.id,
        email=ADMIN_EMAIL,
        password_from_env="IRIP_BOOTSTRAP_ADMIN_PASSWORD",
    )
    logger.info("Bootstrap: admin user ready")

    logger.info("Bootstrap: ensuring system service user (department=system) ...")
    system_user_id = await container.users.get_or_create_system_service(
        department_id=system_dept.id,
    )
    logger.info("Bootstrap: system service user ready (id=%s)", system_user_id)

    logger.info("Bootstrap: seeding departments ...")
    await container.departments.seed_departments(root_dept.id)
    logger.info("Bootstrap: departments ready")

    logger.info("Bootstrap: ensuring MinIO bucket ...")
    container.artifacts.ensure_buckets()
    logger.info("Bootstrap: bucket ready")

    # PITR: 赋予 irip 用户 REPLICATION 权限（pg_basebackup 需要）
    logger.info("Bootstrap: granting REPLICATION permission to irip user ...")
    await _grant_replication_permission(container)
    logger.info("Bootstrap: REPLICATION permission granted")

    # 输出哨兵部门 ID 到环境变量提示（供 Beat worker 使用）
    logger.info(
        "Bootstrap: set these for Beat worker:\n"
        "  IRIP_ROOT_DEPT_ID=%s\n"
        "  IRIP_SYSTEM_DEPT_ID=%s\n"
        "  IRIP_SYSTEM_SERVICE_USER_ID=%s",
        root_dept.id,
        system_dept.id,
        system_user_id,
    )

    logger.info("Bootstrap complete.")


async def _grant_replication_permission(container: ApplicationContainer) -> None:
    """赋予 irip 用户 REPLICATION 权限（pg_basebackup 物理备份需要）。

    幂等操作：ALTER USER 重复执行不会报错。

    Args:
        container: 应用 DI 容器。
    """
    async with container._factory() as session:
        async with session.begin():
            await session.execute(
                sa.text("ALTER USER irip REPLICATION;")
            )


def _to_async_url(url: str) -> str:
    """将同步 psycopg URL 转换为异步 psycopg_async URL。"""
    if url.startswith("postgresql+psycopg://"):
        return url.replace(
            "postgresql+psycopg://", "postgresql+psycopg_async://", 1
        )
    return url


def _build_container() -> ApplicationContainer:
    """从环境变量构建应用容器。"""
    from packages.common.database import build_session_factory
    from packages.common.s3_repository import S3Repository

    db_url = os.getenv("IRIP_DATABASE_URL", "")
    if not db_url:
        raise RuntimeError("IRIP_DATABASE_URL environment variable is required")

    async_url = _to_async_url(db_url)
    session_factory = build_session_factory(async_url)

    endpoint = os.getenv("IRIP_MINIO_ENDPOINT", "http://localhost:9000")
    if not endpoint.startswith("http"):
        endpoint = f"http://{endpoint}"
    s3_repo = S3Repository(
        endpoint_url=endpoint,
        access_key=os.getenv("IRIP_MINIO_ACCESS_KEY", "irip"),
        secret_key=os.getenv("IRIP_MINIO_SECRET_KEY", "irip_dev_password"),
        bucket_name=os.getenv("IRIP_MINIO_BUCKET", "irip-artifacts"),
        region=os.getenv("IRIP_MINIO_REGION", "us-east-1"),
    )

    return ApplicationContainer(
        session_factory=session_factory,
        s3_repo=s3_repo,
    )


def main() -> None:
    """引导脚本入口。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    container = _build_container()
    asyncio.run(bootstrap_platform(container))


if __name__ == "__main__":
    main()
