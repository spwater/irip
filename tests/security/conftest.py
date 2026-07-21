"""安全测试 fixtures。

提供：
- 用户 A / B（同一组织，各有自己的对象级 grant）；
- 对象 X / Y（分别属于 A / B 的私有对象）；
- AuthorizationService / AuditRecorder 实例；
- 清理函数。

使用迁移已种子的角色（不创建新角色）。
用户 A / B 需插入 app_user 表（满足 scope_grant.user_id FK 约束）。
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.audit.repository import AuditRecorder
from packages.auth.scope_grants import AuthorizationService, ResourceRef, ScopeGrant
from packages.common.clock import SystemClock
from packages.common.database import session_scope
from packages.common.ids import new_id


@dataclass(frozen=True)
class SecurityTestUser:
    """安全测试用户。"""

    user_id: UUID
    email: str
    roles: list[str]


@dataclass(frozen=True)
class SecurityTestSetup:
    """安全测试环境（用户 A/B + 对象 X/Y + 授权服务）。"""

    user_a: SecurityTestUser
    user_b: SecurityTestUser
    object_x: ResourceRef
    object_y: ResourceRef
    authz: AuthorizationService
    org_id: UUID


def _insert_user_sync(engine: Engine, user_id: UUID, email: str) -> None:
    """同步插入测试用户到 app_user。"""
    from packages.auth.passwords import hash_password

    with engine.connect() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO app_user "
                "(id, email, display_name, password_hash, status, lock_version) "
                "VALUES (:id, :email, :name, :hash, 'active', 0)"
            ),
            {
                "id": user_id,
                "email": email,
                "name": "Security Test User",
                "hash": hash_password("Test-Password-2026!"),
            },
        )
        conn.commit()


def _cleanup_user_sync(engine: Engine, user_id: UUID) -> None:
    """同步清理测试用户。"""
    with engine.connect() as conn:
        conn.execute(
            sa.text("DELETE FROM scope_grant WHERE user_id = :uid"),
            {"uid": user_id},
        )
        conn.execute(
            sa.text("DELETE FROM app_user WHERE id = :uid"),
            {"uid": user_id},
        )
        conn.commit()


def _get_role_id_sync(engine: Engine, code: str) -> UUID:
    """同步查找已有角色 ID（迁移种子数据）。"""
    with engine.connect() as conn:
        result = conn.execute(
            sa.text("SELECT id FROM role WHERE code = :code"),
            {"code": code},
        )
        row = result.fetchone()
        if row is None:
            raise ValueError(f"Role '{code}' not found; run alembic upgrade head")
        return UUID(str(row[0]))


@pytest.fixture
async def security_setup(
    async_session_factory: async_sessionmaker[AsyncSession],
    sync_engine: Engine,
) -> AsyncIterator[SecurityTestSetup]:
    """安全测试环境：同一组织内两个用户，各有私有对象。

    授权设置：
    - 用户 A 有 object X 的 fact:read grant（user 直连）；
    - 用户 B 有 object Y 的 fact:read grant（user 直连）；
    - A 不能访问 Y，B 不能访问 X（直接 ID 访问拒绝）。
    """
    org = new_id()
    user_a = SecurityTestUser(
        user_id=new_id(), email="user-a@irip.local", roles=["researcher"]
    )
    user_b = SecurityTestUser(
        user_id=new_id(), email="user-b@irip.local", roles=["researcher"]
    )
    obj_x = new_id()
    obj_y = new_id()

    # 插入测试用户到 app_user
    _insert_user_sync(sync_engine, user_a.user_id, user_a.email)
    _insert_user_sync(sync_engine, user_b.user_id, user_b.email)

    async with session_scope(async_session_factory) as session:
        # A → X
        session.add(
            ScopeGrant(
                id=new_id(),
                user_id=user_a.user_id,
                role_id=None,
                organization_id=org,
                object_root_id=obj_x,
                resource_type="fact",
                action="fact:read",
            )
        )
        # B → Y
        session.add(
            ScopeGrant(
                id=new_id(),
                user_id=user_b.user_id,
                role_id=None,
                organization_id=org,
                object_root_id=obj_y,
                resource_type="fact",
                action="fact:read",
            )
        )
        await session.flush()

    session = async_session_factory()
    await session.begin()
    authz = AuthorizationService(session=session, clock=SystemClock())

    yield SecurityTestSetup(
        user_a=user_a,
        user_b=user_b,
        object_x=ResourceRef(organization_id=org, object_id=obj_x, resource_type="fact"),
        object_y=ResourceRef(organization_id=org, object_id=obj_y, resource_type="fact"),
        authz=authz,
        org_id=org,
    )

    await session.rollback()
    await session.close()

    # 清理
    _cleanup_user_sync(sync_engine, user_a.user_id)
    _cleanup_user_sync(sync_engine, user_b.user_id)


@pytest.fixture
def audit_recorder() -> AuditRecorder:
    """审计记录器实例。"""
    return AuditRecorder()
