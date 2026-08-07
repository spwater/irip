"""账户管理路由集成测试（irip-ai-collab）。

覆盖 P0-05 个人设置：
- 改密码：旧密码验证 + 新密码 hash + token_version+1；
- 旧密码错误返回 invalid_credentials；
- 改头像/显示名（update_profile）；
- get_profile 返回 avatar_url / department_id / roles。

使用 FastAPI TestClient + dependency_overrides 注入测试 session_factory。
依赖测试数据库（IRIP_TEST_DATABASE_URL）。
"""

from uuid import uuid4

import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from apps.api.dependencies.auth import CurrentUser, get_current_user
from apps.api.routers.account import account_router, get_account_session_factory
from packages.auth.passwords import hash_password, verify_password
from packages.common.error_codes import ErrorCode
from packages.common.errors import AppError


def _insert_user(
    sync_engine, email: str, org_id=None, display_name="用户", roles=None, password="Old-Pass-2026!"
):
    """插入测试用户并返回 (user_id, org_id, password_hash)。"""
    import json

    from packages.common.ids import new_id as _new_id

    user_id = _new_id()
    final_org = org_id if org_id is not None else _new_id()
    pwd_hash = hash_password(password)
    with sync_engine.connect() as conn:
        # 先创建 department（满足 FK 约束）
        if org_id is None:
            conn.execute(
                sa.text("INSERT INTO department (id, name, code) VALUES (:id, :name, :code)"),
                {
                    "id": final_org,
                    "name": f"Test Dept {final_org.hex[:8]}",
                    "code": f"dept-{final_org.hex[:8]}",
                },
            )
        conn.execute(
            sa.text(
                "INSERT INTO app_user "
                "(id, department_id, email, display_name, "
                "password_hash, status, roles, lock_version, token_version) "
                "VALUES (:id, :org, :email, :name, :hash, :status, :roles, 0, 0)"
            ),
            {
                "id": user_id,
                "org": final_org,
                "email": email,
                "name": display_name,
                "hash": pwd_hash,
                "status": "active",
                "roles": json.dumps(roles or ["lab_member"]),
            },
        )
        conn.commit()
    return user_id, final_org, pwd_hash


def _cleanup_user(sync_engine, user_id):
    with sync_engine.connect() as conn:
        # 先查出 department_id
        row = conn.execute(
            sa.text("SELECT department_id FROM app_user WHERE id = :uid"),
            {"uid": user_id},
        ).fetchone()
        conn.execute(sa.text("DELETE FROM app_user WHERE id = :uid"), {"uid": user_id})
        if row:
            conn.execute(
                sa.text("DELETE FROM department WHERE id = :did"),
                {"did": row[0]},
            )
        conn.commit()


def _build_app(async_session_factory, current_user):
    """构建挂载 account_router 的 FastAPI 测试应用（含 AppError 处理器）。"""
    app = FastAPI()
    app.include_router(account_router)

    # 注册 AppError 异常处理器（与 apps/api/main.py 保持一致）
    _status_map = ErrorCode.to_status_map()

    @app.exception_handler(AppError)
    async def handle_app_error(request, exc: AppError):  # type: ignore[no-untyped-def]
        status = _status_map.get(exc.code, 500)
        return JSONResponse(status_code=status, content={"error": exc.to_dict()})

    async def _override_session_factory():
        return async_session_factory

    def _override_current_user():
        return current_user

    app.dependency_overrides[get_account_session_factory] = _override_session_factory
    app.dependency_overrides[get_current_user] = _override_current_user
    return app


class TestGetProfile:
    """P0-05: 查询个人信息。"""

    def test_get_profile_returns_avatar_roles_org(self, async_session_factory, sync_engine):
        user_id, org_id, _ = _insert_user(
            sync_engine,
            f"prof-{uuid4().hex[:8]}@irip.local",
            display_name="研究员甲",
            roles=["lab_member"],
        )
        try:
            # 设置 avatar_url
            with sync_engine.connect() as conn:
                conn.execute(
                    sa.text("UPDATE app_user SET avatar_url = :url WHERE id = :uid"),
                    {"url": "http://example.com/a.png", "uid": user_id},
                )
                conn.commit()
            current_user = CurrentUser(
                user_id=user_id,
                email=f"prof-{user_id}@irip.local",
                roles=["lab_member"],
                department_id=org_id,
            )
            app = _build_app(async_session_factory, current_user)
            client = TestClient(app)
            resp = client.get("/api/v1/account/profile")
            assert resp.status_code == 200
            data = resp.json()
            assert data["id"] == str(user_id)
            assert data["display_name"] == "研究员甲"
            assert data["avatar_url"] == "http://example.com/a.png"
            assert data["roles"] == ["lab_member"]
            assert data["department_id"] == str(org_id)
        finally:
            _cleanup_user(sync_engine, user_id)


class TestUpdateProfile:
    """P0-05: 修改显示名/头像。"""

    def test_update_display_name(self, async_session_factory, sync_engine):
        user_id, org_id, _ = _insert_user(
            sync_engine, f"upd-{uuid4().hex[:8]}@irip.local", display_name="旧名字"
        )
        try:
            current_user = CurrentUser(
                user_id=user_id,
                email=f"upd-{user_id}@irip.local",
                roles=["lab_member"],
                department_id=org_id,
            )
            app = _build_app(async_session_factory, current_user)
            client = TestClient(app)
            resp = client.patch(
                "/api/v1/account/profile",
                json={"display_name": "新名字"},
            )
            assert resp.status_code == 200
            assert resp.json()["display_name"] == "新名字"
            # 校验数据库已更新
            with sync_engine.connect() as conn:
                row = conn.execute(
                    sa.text("SELECT display_name FROM app_user WHERE id = :uid"),
                    {"uid": user_id},
                ).fetchone()
            assert row[0] == "新名字"
        finally:
            _cleanup_user(sync_engine, user_id)

    def test_update_avatar_url(self, async_session_factory, sync_engine):
        user_id, org_id, _ = _insert_user(sync_engine, f"ava-{uuid4().hex[:8]}@irip.local")
        try:
            current_user = CurrentUser(
                user_id=user_id,
                email=f"ava-{user_id}@irip.local",
                roles=["lab_member"],
                department_id=org_id,
            )
            app = _build_app(async_session_factory, current_user)
            client = TestClient(app)
            resp = client.patch(
                "/api/v1/account/profile",
                json={"avatar_url": "http://cdn.irip/avatar.png"},
            )
            assert resp.status_code == 200
            assert resp.json()["avatar_url"] == "http://cdn.irip/avatar.png"
        finally:
            _cleanup_user(sync_engine, user_id)


class TestChangePassword:
    """P0-05: 修改密码 — 旧密码验证 + token_version+1。"""

    def test_change_password_success_token_version_increments(
        self, async_session_factory, sync_engine
    ):
        user_id, org_id, old_hash = _insert_user(
            sync_engine, f"pwd-{uuid4().hex[:8]}@irip.local", password="Old-Pass-2026!"
        )
        try:
            current_user = CurrentUser(
                user_id=user_id,
                email=f"pwd-{user_id}@irip.local",
                roles=["lab_member"],
                department_id=org_id,
            )
            app = _build_app(async_session_factory, current_user)
            client = TestClient(app)
            resp = client.post(
                "/api/v1/account/password",
                json={"old_password": "Old-Pass-2026!", "new_password": "New-Secret-2026!"},
            )
            assert resp.status_code == 204
            # 校验密码已更新 + token_version +1
            with sync_engine.connect() as conn:
                row = conn.execute(
                    sa.text("SELECT password_hash, token_version FROM app_user WHERE id = :uid"),
                    {"uid": user_id},
                ).fetchone()
            assert row[1] == 1  # token_version 从 0 → 1
            assert row[0] != old_hash
            # 新密码可验证通过
            assert verify_password(row[0], "New-Secret-2026!") is True
            # 旧密码不再匹配
            assert verify_password(row[0], "Old-Pass-2026!") is False
        finally:
            _cleanup_user(sync_engine, user_id)

    def test_change_password_wrong_old_returns_invalid_credentials(
        self, async_session_factory, sync_engine
    ):
        user_id, org_id, _ = _insert_user(
            sync_engine, f"badpwd-{uuid4().hex[:8]}@irip.local", password="Old-Pass-2026!"
        )
        try:
            current_user = CurrentUser(
                user_id=user_id,
                email=f"badpwd-{user_id}@irip.local",
                roles=["lab_member"],
                department_id=org_id,
            )
            app = _build_app(async_session_factory, current_user)
            client = TestClient(app)
            resp = client.post(
                "/api/v1/account/password",
                json={"old_password": "Wrong-Password!", "new_password": "New-Secret-2026!"},
            )
            # invalid_credentials 映射为 401（ErrorCode.to_status_map）
            assert resp.status_code == 401
            body = resp.json()
            assert body["error"]["code"] == "invalid_credentials"
            # token_version 未变
            with sync_engine.connect() as conn:
                row = conn.execute(
                    sa.text("SELECT token_version FROM app_user WHERE id = :uid"),
                    {"uid": user_id},
                ).fetchone()
            assert row[0] == 0
        finally:
            _cleanup_user(sync_engine, user_id)

    def test_change_password_too_short_rejected_by_schema(self, async_session_factory, sync_engine):
        user_id, org_id, _ = _insert_user(sync_engine, f"shortpwd-{uuid4().hex[:8]}@irip.local")
        try:
            current_user = CurrentUser(
                user_id=user_id,
                email=f"shortpwd-{user_id}@irip.local",
                roles=["lab_member"],
                department_id=org_id,
            )
            app = _build_app(async_session_factory, current_user)
            client = TestClient(app)
            resp = client.post(
                "/api/v1/account/password",
                json={"old_password": "Old-Pass-2026!", "new_password": "123"},
            )
            # Pydantic min_length=6 → 422
            assert resp.status_code == 422
        finally:
            _cleanup_user(sync_engine, user_id)
