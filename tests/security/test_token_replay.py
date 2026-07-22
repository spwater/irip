"""安全测试：刷新令牌轮换、过期与重放防护。

覆盖（docs/arch-v0.md §1.2 刷新令牌安全 + §4.1 时序图）：
- 刷新令牌轮换后旧令牌失效（replaced_by 非空 → 旧令牌不可再用）；
- 令牌过期后拒绝（exp < now → 401 token_expired）；
- 同一 refresh token 不能重复使用（重放 → 整族撤销 → 401 refresh_replayed）。

使用 FastAPI TestClient 通过 /api/v1/auth/login 和 /api/v1/auth/refresh
端点验证完整 HTTP 流程。
"""

import time

import jwt

from packages.auth.tokens import JWT_ALGORITHM

# ============================================================
# 辅助函数
# ============================================================


def _login(client, email: str, password: str) -> dict[str, str]:
    """登录并返回 access_token + refresh_cookie。"""
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    body = resp.json()
    refresh_cookie = resp.cookies.get("irip_refresh")
    assert refresh_cookie is not None, "Refresh cookie not set"
    return {
        "access_token": body["access_token"],
        "refresh_cookie": refresh_cookie,
    }


def _refresh(client, refresh_cookie: str) -> tuple[int, dict]:
    """使用 refresh cookie 刷新令牌，返回 (status_code, body)。"""
    resp = client.post(
        "/api/v1/auth/refresh",
        cookies={"irip_refresh": refresh_cookie},
    )
    is_json = resp.headers.get("content-type", "").startswith(
        "application/json"
    )
    body = resp.json() if is_json else {}
    return resp.status_code, body


# ============================================================
# 1. 刷新令牌轮换后旧令牌失效
# ============================================================


class TestRefreshTokenRotation:
    """刷新令牌轮换：旋转后旧令牌不可再用。"""

    def test_old_refresh_token_invalid_after_rotation(
        self,
        sec_api_client,
        sec_seeded_user,
    ) -> None:
        """旋转后使用旧 refresh token → 401 refresh_replayed。"""
        tokens = _login(sec_api_client, sec_seeded_user.email, sec_seeded_user.password)
        old_refresh = tokens["refresh_cookie"]

        # 第一次刷新：成功
        status, body = _refresh(sec_api_client, old_refresh)
        assert status == 200, f"First refresh failed: {body}"
        new_access = body.get("access_token")
        assert new_access is not None
        new_refresh = sec_api_client.cookies.get("irip_refresh")
        assert new_refresh is not None
        assert new_refresh != old_refresh, "Refresh token should rotate"

        # 用旧 refresh token 再次刷新：应被拒绝（重放检测）
        status, body = _refresh(sec_api_client, old_refresh)
        assert status == 401, f"Old token should be rejected: {body}"
        assert body["error"]["code"] == "refresh_replayed"

    def test_new_refresh_token_works_after_rotation(
        self,
        sec_api_client,
        sec_seeded_user,
    ) -> None:
        """旋转后的新 refresh token 可以正常使用。"""
        tokens = _login(sec_api_client, sec_seeded_user.email, sec_seeded_user.password)

        # 第一次刷新
        status, body = _refresh(sec_api_client, tokens["refresh_cookie"])
        assert status == 200
        new_refresh = sec_api_client.cookies.get("irip_refresh")
        assert new_refresh is not None

        # 第二次刷新（用新 token）：也应成功
        status, body = _refresh(sec_api_client, new_refresh)
        assert status == 200, f"Second refresh failed: {body}"
        assert "access_token" in body


# ============================================================
# 2. 令牌过期后拒绝
# ============================================================


class TestTokenExpiry:
    """访问令牌过期后请求被拒绝。"""

    def test_expired_access_token_rejected(
        self,
        sec_api_client,
        sec_seeded_user,
        token_secret: str,
    ) -> None:
        """过期的 JWT access token → 401 token_expired。"""
        # 构造一个已过期的 JWT
        expired_payload = {
            "sub": str(sec_seeded_user.user_id),
            "email": sec_seeded_user.email,
            "roles": ["researcher"],
            "iat": int(time.time()) - 3600,
            "exp": int(time.time()) - 1800,
        }
        expired_token = jwt.encode(
            expired_payload, token_secret, algorithm=JWT_ALGORITHM
        )

        resp = sec_api_client.get(
            "/api/v1/me",
            headers={"Authorization": f"Bearer {expired_token}"},
        )
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "token_expired"

    def test_valid_access_token_accepted(
        self,
        sec_api_client,
        sec_seeded_user,
    ) -> None:
        """有效的 JWT access token → 200（对照组）。"""
        tokens = _login(sec_api_client, sec_seeded_user.email, sec_seeded_user.password)
        resp = sec_api_client.get(
            "/api/v1/me",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        assert resp.status_code == 200
        assert resp.json()["email"] == sec_seeded_user.email

    def test_missing_token_rejected(self, sec_api_client) -> None:
        """缺少 Authorization header → 401 invalid_credentials。"""
        resp = sec_api_client.get("/api/v1/me")
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "invalid_credentials"

    def test_malformed_token_rejected(self, sec_api_client) -> None:
        """格式错误的 JWT → 401 invalid_credentials。"""
        resp = sec_api_client.get(
            "/api/v1/me",
            headers={"Authorization": "Bearer not.a.valid.jwt"},
        )
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "invalid_credentials"


# ============================================================
# 3. 同一 refresh token 不能重复使用（重放 → 整族撤销）
# ============================================================


class TestRefreshTokenReplay:
    """同一 refresh token 重复使用 → 重放检测 → 整族撤销。"""

    def test_replay_revokes_entire_family(
        self,
        sec_api_client,
        sec_seeded_user,
    ) -> None:
        """重放旧 token 后，新 token 也被撤销（整族撤销）。"""
        tokens = _login(sec_api_client, sec_seeded_user.email, sec_seeded_user.password)
        old_refresh = tokens["refresh_cookie"]

        # 第一次刷新：成功，获得新 token
        status, body = _refresh(sec_api_client, old_refresh)
        assert status == 200
        new_refresh = sec_api_client.cookies.get("irip_refresh")
        assert new_refresh is not None

        # 重放旧 token：触发整族撤销
        status, body = _refresh(sec_api_client, old_refresh)
        assert status == 401
        assert body["error"]["code"] == "refresh_replayed"

        # 整族撤销后，新 token 也不可用
        status, body = _refresh(sec_api_client, new_refresh)
        assert status == 401, "New token should be revoked after family revocation"

    def test_replay_then_logout_is_idempotent(
        self,
        sec_api_client,
        sec_seeded_user,
    ) -> None:
        """重放触发撤销后，logout 仍幂等返回 200。"""
        tokens = _login(sec_api_client, sec_seeded_user.email, sec_seeded_user.password)

        # 刷新
        status, _ = _refresh(sec_api_client, tokens["refresh_cookie"])
        assert status == 200

        # 重放
        status, _ = _refresh(sec_api_client, tokens["refresh_cookie"])
        assert status == 401

        # logout 幂等
        resp = sec_api_client.post(
            "/api/v1/auth/logout",
            cookies={"irip_refresh": tokens["refresh_cookie"]},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_refresh_without_cookie_rejected(self, sec_api_client) -> None:
        """缺少 refresh cookie → 401 invalid_credentials。"""
        status, body = _refresh(sec_api_client, "")
        assert status == 401
        assert body["error"]["code"] == "invalid_credentials"
