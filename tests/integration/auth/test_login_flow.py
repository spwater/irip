"""登录刷新全链路集成测试（实施计划 Task 4 Step 1）。

覆盖场景（计划第 330-341 行 + 验收标准）：
- 登录成功 → access_token + cookie
- 密码错误 → 401 invalid_credentials
- 禁用用户 → 401 invalid_credentials
- 刷新令牌单用途旋转 → 重放即整族撤销
- 登出 → 后续刷新失败
- /me 端点鉴权

前置：测试数据库已启动并已执行 alembic upgrade head。
"""


def test_login_success(api_client, seeded_user) -> None:
    """登录成功：返回 access_token + expires_in，设置 irip_refresh cookie。"""
    response = api_client.post(
        "/api/v1/auth/login",
        json={"email": seeded_user.email, "password": seeded_user.password},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["expires_in"] == 900
    assert "refresh_token" not in data
    assert "irip_refresh" in response.cookies


def test_login_invalid_password(api_client, seeded_user) -> None:
    """密码错误：401 invalid_credentials。"""
    response = api_client.post(
        "/api/v1/auth/login",
        json={"email": seeded_user.email, "password": "Wrong-Password!"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"


def test_login_nonexistent_user(api_client) -> None:
    """用户不存在：401 invalid_credentials（不泄露用户是否存在）。"""
    response = api_client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@irip.local", "password": "Any-Password!"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"


def test_login_disabled_user(api_client, seeded_disabled_user) -> None:
    """禁用用户：401 invalid_credentials。"""
    response = api_client.post(
        "/api/v1/auth/login",
        json={
            "email": seeded_disabled_user.email,
            "password": seeded_disabled_user.password,
        },
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"


def test_refresh_token_is_single_use(api_client, seeded_user) -> None:
    """刷新令牌单用途：首次旋转成功，重用旧令牌触发整族撤销（计划第 330-341 行）。"""
    login = api_client.post(
        "/api/v1/auth/login",
        json={"email": seeded_user.email, "password": "Correct-Horse-2026!"},
    )
    first_cookie = login.cookies["irip_refresh"]

    rotated = api_client.post(
        "/api/v1/auth/refresh", cookies={"irip_refresh": first_cookie}
    )
    replay = api_client.post(
        "/api/v1/auth/refresh", cookies={"irip_refresh": first_cookie}
    )

    assert "refresh_token" not in login.json()
    assert rotated.status_code == 200
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "refresh_replayed"


def test_refresh_rotated_token_works(api_client, seeded_user) -> None:
    """旋转后的新令牌可用于下一次刷新。"""
    login = api_client.post(
        "/api/v1/auth/login",
        json={"email": seeded_user.email, "password": "Correct-Horse-2026!"},
    )
    first_cookie = login.cookies["irip_refresh"]

    rotated = api_client.post(
        "/api/v1/auth/refresh", cookies={"irip_refresh": first_cookie}
    )
    assert rotated.status_code == 200
    new_cookie = rotated.cookies["irip_refresh"]
    assert new_cookie != first_cookie

    # 新令牌应可再次旋转
    second_rotate = api_client.post(
        "/api/v1/auth/refresh", cookies={"irip_refresh": new_cookie}
    )
    assert second_rotate.status_code == 200


def test_refresh_invalid_token(api_client) -> None:
    """无效刷新令牌：401 invalid_credentials。"""
    response = api_client.post(
        "/api/v1/auth/refresh",
        cookies={"irip_refresh": "invalid-token-string"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"


def test_refresh_missing_cookie(api_client) -> None:
    """缺少刷新 cookie：401 invalid_credentials。"""
    response = api_client.post("/api/v1/auth/refresh")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"


def test_logout_invalidates_session(api_client, seeded_user) -> None:
    """登出后刷新令牌失效：401 invalid_credentials。"""
    login = api_client.post(
        "/api/v1/auth/login",
        json={"email": seeded_user.email, "password": "Correct-Horse-2026!"},
    )
    cookie = login.cookies["irip_refresh"]

    logout = api_client.post(
        "/api/v1/auth/logout", cookies={"irip_refresh": cookie}
    )
    assert logout.status_code == 200
    assert logout.json()["ok"] is True

    # 登出后刷新应失败
    refresh = api_client.post(
        "/api/v1/auth/refresh", cookies={"irip_refresh": cookie}
    )
    assert refresh.status_code == 401
    assert refresh.json()["error"]["code"] == "invalid_credentials"


def test_me_with_access_token(api_client, seeded_user) -> None:
    """有效 access token 访问 /me：返回当前用户信息。"""
    login = api_client.post(
        "/api/v1/auth/login",
        json={"email": seeded_user.email, "password": "Correct-Horse-2026!"},
    )
    access_token = login.json()["access_token"]

    me = api_client.get(
        "/api/v1/me", headers={"Authorization": f"Bearer {access_token}"}
    )
    assert me.status_code == 200
    assert me.json()["email"] == seeded_user.email
    assert me.json()["id"] == str(seeded_user.id)
    assert me.json()["display_name"] == seeded_user.display_name
    assert me.json()["roles"] == []
    assert "permissions" in me.json()


def test_me_without_token(api_client) -> None:
    """无 Authorization header 访问 /me：401 invalid_credentials。"""
    me = api_client.get("/api/v1/me")
    assert me.status_code == 401
    assert me.json()["error"]["code"] == "invalid_credentials"


def test_me_with_invalid_token(api_client) -> None:
    """无效 JWT 访问 /me：401 invalid_credentials。"""
    me = api_client.get(
        "/api/v1/me", headers={"Authorization": "Bearer invalid.jwt.token"}
    )
    assert me.status_code == 401
    assert me.json()["error"]["code"] == "invalid_credentials"
