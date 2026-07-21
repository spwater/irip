"""认证单元测试 fixtures（权限矩阵 + 授权服务 + 脱敏）。

提供：
- redact: 脱敏函数 fixture；
- db_helper: 数据库辅助工具（查找已有角色、插入/清理测试用户）；
- authz / researcher / kiln / cooler: 授权服务与测试资源 fixtures
  （需数据库，依赖 tests/conftest.py 的 sync_engine / async_session_factory）。
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.audit.redaction import redact as _redact_fn
from packages.auth.scope_grants import AuthorizationService, ResourceRef, ScopeGrant
from packages.common.clock import SystemClock
from packages.common.database import session_scope
from packages.common.ids import new_id

# ---- 脱敏 fixture ----


@pytest.fixture
def redact():
    """脱敏函数 fixture。"""
    return _redact_fn


# ---- 通用辅助 fixtures ----


@pytest.fixture
def org_id() -> UUID:
    """测试组织 ID（每个测试唯一）。"""
    return new_id()


@pytest.fixture
def now_utc() -> datetime:
    """当前 UTC 时刻（测试固定时间）。"""
    return datetime.now(UTC)


# ---- 数据库辅助工具 ----


@dataclass(frozen=True)
class DbHelper:
    """数据库辅助工具。

    提供：
    - get_role_id: 查找迁移已种子的角色 ID（按 code）；
    - insert_user: 插入测试用户到 app_user（含密码哈希）；
    - cleanup_user: 删除测试用户；
    - cleanup_grants: 按组织 ID 清理 scope_grant。
    """

    sync_engine: Engine
    async_session_factory: async_sessionmaker[AsyncSession]

    def get_role_id_sync(self, code: str) -> UUID:
        """同步查找已有角色 ID（迁移种子数据）。"""
        with self.sync_engine.connect() as conn:
            result = conn.execute(
                sa.text("SELECT id FROM role WHERE code = :code"),
                {"code": code},
            )
            row = result.fetchone()
            if row is None:
                raise ValueError(f"Role '{code}' not found; run alembic upgrade head")
            return UUID(str(row[0]))

    def insert_user_sync(self, user_id: UUID, email: str) -> None:
        """同步插入测试用户到 app_user。"""
        from packages.auth.passwords import hash_password

        with self.sync_engine.connect() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO app_user "
                    "(id, email, display_name, password_hash, status, lock_version) "
                    "VALUES (:id, :email, :name, :hash, 'active', 0)"
                ),
                {
                    "id": user_id,
                    "email": email,
                    "name": "Test User",
                    "hash": hash_password("Test-Password-2026!"),
                },
            )
            conn.commit()

    def cleanup_user_sync(self, user_id: UUID) -> None:
        """同步清理测试用户。"""
        with self.sync_engine.connect() as conn:
            conn.execute(
                sa.text("DELETE FROM scope_grant WHERE user_id = :uid"),
                {"uid": user_id},
            )
            conn.execute(
                sa.text("DELETE FROM app_user WHERE id = :uid"),
                {"uid": user_id},
            )
            conn.commit()

    def cleanup_grants_by_org_sync(self, org_id: UUID) -> None:
        """同步按组织 ID 清理 scope_grant。"""
        with self.sync_engine.connect() as conn:
            conn.execute(
                sa.text("DELETE FROM scope_grant WHERE organization_id = :oid"),
                {"oid": org_id},
            )
            conn.commit()


@pytest.fixture
def db_helper(
    sync_engine: Engine,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> DbHelper:
    """数据库辅助工具 fixture。"""
    return DbHelper(
        sync_engine=sync_engine,
        async_session_factory=async_session_factory,
    )


# ---- 授权服务 fixtures ----


@dataclass(frozen=True)
class AuthTestUser:
    """测试用户（满足 _AuthorizedUser 协议）。"""

    user_id: UUID
    email: str
    roles: list[str]


@pytest.fixture
def researcher() -> AuthTestUser:
    """研究员用户（roles=["researcher"]）。"""
    return AuthTestUser(
        user_id=new_id(),
        email="researcher@irip.local",
        roles=["researcher"],
    )


@pytest.fixture
def data_steward() -> AuthTestUser:
    """数据管家用户。"""
    return AuthTestUser(
        user_id=new_id(),
        email="steward@irip.local",
        roles=["data_steward"],
    )


@pytest.fixture
def platform_admin() -> AuthTestUser:
    """平台管理员用户。"""
    return AuthTestUser(
        user_id=new_id(),
        email="admin@irip.local",
        roles=["platform_administrator"],
    )


@dataclass(frozen=True)
class KilnResource:
    """测试资源：窑炉（含子测量点）。"""

    child_measurement_point: ResourceRef
    object_root_id: UUID


@pytest.fixture
def kiln(org_id: UUID) -> KilnResource:
    """窑炉资源（子测量点的 object_id = object_root_id，V0 简化匹配）。"""
    child_id = new_id()
    return KilnResource(
        child_measurement_point=ResourceRef(
            organization_id=org_id,
            object_id=child_id,
            resource_type="fact",
        ),
        object_root_id=child_id,
    )


@pytest.fixture
def cooler(org_id: UUID) -> ResourceRef:
    """冷却器资源（兄弟对象，无授权）。"""
    return ResourceRef(
        organization_id=org_id,
        object_id=new_id(),
        resource_type="fact",
    )


@pytest.fixture
async def authz(
    async_session_factory: async_sessionmaker[AsyncSession],
    db_helper: DbHelper,
    researcher: AuthTestUser,
    kiln: KilnResource,
    org_id: UUID,
) -> AsyncIterator[AuthorizationService]:
    """授权服务（预置对 kiln 子测量点的 fact:read 角色 grant）。

    使用迁移已种子的 researcher 角色（不创建新角色）。
    清理：测试后删除 scope_grant 记录。
    """
    role_id = db_helper.get_role_id_sync("researcher")

    async with session_scope(async_session_factory) as session:
        grant = ScopeGrant(
            id=new_id(),
            user_id=None,
            role_id=role_id,
            organization_id=org_id,
            object_root_id=kiln.object_root_id,
            resource_type="fact",
            action="fact:read",
            effective_from=None,
            effective_to=None,
        )
        session.add(grant)
        await session.flush()

    # 创建服务
    session = async_session_factory()
    await session.begin()
    service = AuthorizationService(session=session, clock=SystemClock())
    yield service
    await session.rollback()
    await session.close()

    # 清理
    db_helper.cleanup_grants_by_org_sync(org_id)
