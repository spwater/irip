"""安全测试 fixtures。

提供：
- 用户 A / B（同一组织，各有自己的对象级 grant）；
- 对象 X / Y（分别属于 A / B 的私有对象）；
- AuthorizationService / AuditRecorder 实例；
- 清理函数。

使用迁移已种子的角色（不创建新角色）。
用户 A / B 需插入 app_user 表（满足 scope_grant.user_id FK 约束）。

V3-T04 新增 fixtures：
- token_secret / sec_auth_service / sec_api_client / sec_seeded_user：
  安全测试用 FastAPI TestClient + 种子用户，覆盖认证与上传端点。
"""

from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING
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

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

    from packages.auth.service import AuthService


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


@dataclass(frozen=True)
class SecSeededUser:
    """安全测试种子用户（含组织 ID）。"""

    user_id: UUID
    email: str
    password: str
    organization_id: UUID


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
    user_a = SecurityTestUser(user_id=new_id(), email="user-a@irip.local", roles=["researcher"])
    user_b = SecurityTestUser(user_id=new_id(), email="user-b@irip.local", roles=["researcher"])
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


# ---- V3-T04: 安全测试 TestClient fixtures ----


@pytest.fixture
def token_secret() -> str:
    """JWT 签名密钥（测试固定值）。"""
    return "irip-test-jwt-secret-2026"


@pytest.fixture
def sec_auth_service(
    async_session_factory: async_sessionmaker[AsyncSession],
    token_secret: str,
) -> "AuthService":
    """构建 AuthService 实例（LocalAuthBackend + AuthRepository）。"""
    from packages.auth.backends import LocalAuthBackend
    from packages.auth.repository import AuthRepository
    from packages.auth.service import AuthService
    from packages.common.clock import SystemClock

    repository = AuthRepository()
    backend = LocalAuthBackend(repository)
    return AuthService(
        backend=backend,
        repository=repository,
        session_factory=async_session_factory,
        token_secret=token_secret,
        clock=SystemClock(),
    )


@pytest.fixture
def sec_seeded_user(
    sync_engine: Engine,
) -> "Iterator[SecSeededUser]":
    """安全测试种子用户（密码 Correct-Horse-2026!）。"""
    import uuid as uuid_module

    from packages.auth.passwords import hash_password
    from packages.common.ids import new_id

    user_id = new_id()
    org_id = new_id()
    email = f"sec-test-{uuid_module.uuid4().hex[:8]}@irip.local"
    with sync_engine.connect() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO app_user "
                "(id, organization_id, email, display_name, "
                "password_hash, status, lock_version) "
                "VALUES (:id, :org, :email, :name, :hash, 'active', 0)"
            ),
            {
                "id": user_id,
                "org": org_id,
                "email": email,
                "name": "Security Test User",
                "hash": hash_password("Correct-Horse-2026!"),
            },
        )
        conn.commit()

    yield SecSeededUser(
        user_id=user_id,
        email=email,
        password="Correct-Horse-2026!",
        organization_id=org_id,
    )

    with sync_engine.connect() as conn:
        conn.execute(
            sa.text("DELETE FROM refresh_session WHERE user_id = :uid"),
            {"uid": user_id},
        )
        conn.execute(
            sa.text("DELETE FROM app_user WHERE id = :uid"),
            {"uid": user_id},
        )
        conn.commit()


@pytest.fixture
def sec_api_client(
    sec_auth_service: "AuthService",
    token_secret: str,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> "Iterator[TestClient]":
    """安全测试 FastAPI TestClient。

    挂载 auth_router + me_router + uploads_router + artifacts_router +
    health_router，覆盖认证与上传依赖。ArtifactService 使用 Mock 避免
    对 MinIO 的依赖。
    """
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient

    from apps.api.dependencies.auth import get_token_secret
    from apps.api.routers.auth import (
        auth_router,
        get_auth_service,
        get_me_session_factory,
        me_router,
    )
    from apps.api.routers.uploads import (
        artifacts_router,
        get_artifact_service,
        uploads_router,
    )
    from packages.common.errors import AppError

    app = FastAPI(title="IRIP Security Test")
    app.include_router(auth_router)
    app.include_router(me_router)
    app.include_router(uploads_router)
    app.include_router(artifacts_router)

    # 覆盖认证依赖
    app.dependency_overrides[get_auth_service] = lambda: sec_auth_service
    app.dependency_overrides[get_token_secret] = lambda: token_secret
    app.dependency_overrides[get_me_session_factory] = lambda: async_session_factory

    # 覆盖工件服务：使用 Mock 避免 MinIO 依赖
    class _MockArtifactService:
        """Mock 工件服务（仅返回虚拟 URL，不访问 S3）。"""

        def presign_upload_for_key(self, object_key: str, expires: int = 3600) -> str:
            return f"http://mock-s3.local/{object_key}"

        async def presign_download(self, artifact_id, expires: int = 3600) -> str:
            return f"http://mock-s3.local/download/{artifact_id}"

    app.dependency_overrides[get_artifact_service] = lambda: _MockArtifactService()

    # AppError → JSON 统一错误响应
    _STATUS_MAP: dict[str, int] = {
        "invalid_credentials": 401,
        "token_expired": 401,
        "refresh_replayed": 401,
        "forbidden": 403,
        "not_found": 404,
        "conflict": 409,
        "validation_failed": 422,
        "unsupported_media_type": 422,
        "file_too_large": 413,
        "hash_mismatch": 422,
        "size_mismatch": 422,
        "internal_error": 500,
    }

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        status = _STATUS_MAP.get(exc.code, 500)
        return JSONResponse(
            status_code=status,
            content={"error": exc.to_dict()},
        )

    client = TestClient(app)
    yield client
