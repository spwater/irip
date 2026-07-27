"""IRIP 平台幂等引导脚本。

功能（实施计划 Task 9 第 697-706 行）：
  1. 创建/获取组织（IRIP-DEMO）；
  2. 确保 5 个内置角色存在（INSERT ON CONFLICT DO NOTHING）；
  3. 创建管理员用户（admin@irip.local，密码从环境变量读）；
  4. 确保 MinIO bucket 存在。

全部操作幂等：重复运行不报错、不重复创建。

用法（Docker Compose）：
  docker compose run --rm bootstrap

用法（本机）：
  IRIP_DATABASE_URL=... IRIP_BOOTSTRAP_ADMIN_PASSWORD=... \
  python -m deployments.compose.bootstrap

也可作为模块导入：
  from deployments.compose.bootstrap import bootstrap_platform, ApplicationContainer
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

#: 演示组织代码。
DEMO_ORG_CODE: str = "IRIP-DEMO"

#: 演示组织名称。
DEMO_ORG_NAME: str = "IRIP 演示组织"


@dataclass(frozen=True)
class Organization:
    """组织信息。"""

    id: UUID
    code: str
    name: str


class OrganizationRepository:
    """组织数据访问（幂等）。"""

    @staticmethod
    async def ensure_table(session: AsyncSession) -> None:
        """幂等创建 organization 表（V0 无迁移，bootstrap 自行创建）。"""
        await session.execute(
            sa.text(
                "CREATE TABLE IF NOT EXISTS organization ("
                "  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),"
                "  code TEXT UNIQUE NOT NULL,"
                "  name TEXT NOT NULL,"
                "  created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()"
                ")"
            )
        )

    @staticmethod
    async def get_or_create(
        session: AsyncSession,
        code: str,
        name: str,
    ) -> Organization:
        """幂等获取或创建组织。

        使用 INSERT ... ON CONFLICT DO NOTHING，然后 SELECT 已有行。
        """
        await session.execute(
            sa.text(
                "INSERT INTO organization (id, code, name) "
                "VALUES (gen_random_uuid(), :code, :name) "
                "ON CONFLICT (code) DO NOTHING"
            ),
            {"code": code, "name": name},
        )
        result = await session.execute(
            sa.text(
                "SELECT id, code, name FROM organization WHERE code = :code"
            ),
            {"code": code},
        )
        row = result.fetchone()
        if row is None:
            raise RuntimeError(f"Failed to create or find organization: {code}")
        return Organization(id=UUID(str(row[0])), code=str(row[1]), name=str(row[2]))


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
        organization_id: UUID,
        email: str,
        password: str,
        display_name: str = ADMIN_DISPLAY_NAME,
    ) -> UUID:
        """幂等获取或创建管理员用户。

        若 admin@irip.local 不存在则创建（含 platform_administrator 角色），
        已存在则返回其 ID。密码仅在创建时设置，已存在用户密码不更新。

        幂等角色修复：若管理员已存在但 roles 为空（历史遗留），
        补写 platform_administrator 角色，保证修复可重复执行。

        Returns:
            UUID: 管理员用户 ID。
        """
        result = await session.execute(
            sa.text("SELECT id, roles FROM app_user WHERE email = :email"),
            {"email": email},
        )
        existing = result.first()
        if existing is not None:
            user_id = UUID(str(existing[0]))
            existing_roles = existing[1]
            if not existing_roles:
                await session.execute(
                    sa.text(
                        "UPDATE app_user SET roles = CAST(:roles AS jsonb) "
                        "WHERE id = :uid"
                    ),
                    {
                        "roles": json.dumps([ADMIN_ROLE_CODE]),
                        "uid": user_id,
                    },
                )
            return user_id

        user_id = new_id()
        password_hash = hash_password(password)
        await session.execute(
            sa.text(
                "INSERT INTO app_user "
                "(id, organization_id, email, display_name, "
                "password_hash, status, lock_version, roles) "
                "VALUES (:id, :org, :email, :name, :hash, 'active', 0, "
                "CAST(:roles AS jsonb))"
            ),
            {
                "id": user_id,
                "org": organization_id,
                "email": email,
                "name": display_name,
                "hash": password_hash,
                "roles": json.dumps([ADMIN_ROLE_CODE]),
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
    """实验室种子数据引导（幂等，P1）。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """初始化实验室种子引导。

        Args:
            session_factory: 异步会话工厂。
        """
        self._factory = session_factory

    async def seed_departments(self, organization_id: UUID) -> None:
        """幂等创建种子实验室。

        读取 IRIP_SEED_DEPARTMENTS 环境变量（JSON 数组），每项格式：
        ``{"code": "...", "display_name": "...", "description": "...", "sort_order": 0}``

        使用 ON CONFLICT (organization_id, code) DO NOTHING 保证幂等。
        未设置环境变量时跳过。解析失败时 warning 并跳过。

        Args:
            organization_id: 所属组织 ID。
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
                    await session.execute(
                        sa.text(
                            "INSERT INTO department "
                            "(organization_id, code, display_name, description, "
                            "status, sort_order, lock_version) "
                            "VALUES (:org, :code, :name, :desc, 'active', :sort, 0) "
                            "ON CONFLICT (organization_id, code) DO NOTHING"
                        ),
                        {
                            "org": organization_id,
                            "code": code,
                            "name": display_name,
                            "desc": description,
                            "sort": sort_order,
                        },
                    )
        logger.info("Bootstrap: seeded %d departments", len(departments))


class ApplicationContainer:
    """应用 DI 容器（bootstrap 用）。

    封装引导所需全部依赖：数据库会话工厂、S3 客户端。
    提供 organizations / roles / users / artifacts 四个子仓库。
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
        self.organizations = _OrganizationsPort(session_factory)
        self.roles = _RolesPort(session_factory)
        self.users = _UsersPort(session_factory)
        self.artifacts = ArtifactBootstrap(s3_repo)
        self.departments = DepartmentSeeder(session_factory)


class _OrganizationsPort:
    """组织端口。"""

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def get_or_create(self, code: str, name: str) -> Organization:
        """幂等获取或创建组织。"""
        async with self._factory() as session:
            async with session.begin():
                await OrganizationRepository.ensure_table(session)
                return await OrganizationRepository.get_or_create(
                    session, code, name
                )


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
        organization_id: UUID,
        email: str,
        password_from_env: str,
    ) -> UUID:
        """幂等获取或创建管理员用户。

        Args:
            organization_id: 所属组织 ID。
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
                    organization_id=organization_id,
                    email=email,
                    password=password,
                )


async def bootstrap_platform(container: ApplicationContainer) -> None:
    """幂等引导平台：组织 → 角色 → 管理员 → bucket。

    全部操作幂等，可安全重复运行（实施计划第 697-706 行）。

    F-12: 如果 IRIP_MASTER_KEY 未设置，生成随机 master key 并打印到 stderr
    供运维人员记录到环境变量中。

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

    logger.info("Bootstrap: ensuring organization IRIP-DEMO ...")
    organization = await container.organizations.get_or_create(
        code=DEMO_ORG_CODE,
        name=DEMO_ORG_NAME,
    )
    logger.info("Bootstrap: organization ready (id=%s)", organization.id)

    logger.info("Bootstrap: ensuring builtin roles ...")
    await container.roles.ensure_builtin_roles()
    logger.info("Bootstrap: roles ready")

    logger.info("Bootstrap: ensuring admin user %s ...", ADMIN_EMAIL)
    await container.users.get_or_create_admin(
        organization_id=organization.id,
        email=ADMIN_EMAIL,
        password_from_env="IRIP_BOOTSTRAP_ADMIN_PASSWORD",
    )
    logger.info("Bootstrap: admin user ready")

    logger.info("Bootstrap: seeding departments ...")
    await container.departments.seed_departments(organization.id)
    logger.info("Bootstrap: departments ready")

    logger.info("Bootstrap: ensuring MinIO bucket ...")
    container.artifacts.ensure_buckets()
    logger.info("Bootstrap: bucket ready")

    logger.info("Bootstrap complete.")


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
