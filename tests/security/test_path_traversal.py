"""安全测试：路径穿越防护。

覆盖（docs/arch-v0.md §7.4 输入校验 + §4.3 工件存储安全）：
- ``../secret`` 路径被拒绝（UUID 参数校验）；
- ``%2e%2e/secret`` URL 编码穿越被拒绝；
- ``..\\\\secret`` Windows 风格穿越被拒绝；
- 规范化后仍在命名空间内（object_key 由 SHA-256 构造，无用户输入）。

安全设计：
- artifact_id 路径参数为 UUID 类型，FastAPI 自动校验非 UUID 字符串；
- S3 object_key 由 SHA-256 摘要构造（``sha256/<前2位>/<digest>``），不含用户输入；
- 临时上传 key 格式为 ``uploads/{artifact_id}``，artifact_id 为 UUID。
"""

import urllib.parse

from packages.common.artifacts import _build_object_key

# ============================================================
# 辅助函数
# ============================================================


def _login_and_get_token(client, email: str, password: str) -> str:
    """登录并返回 access_token。"""
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 200
    return resp.json()["access_token"]


def _auth_headers(token: str) -> dict[str, str]:
    """构造认证请求头。"""
    return {"Authorization": f"Bearer {token}"}


# ============================================================
# 1. ../secret 路径被拒绝
# ============================================================


class TestDotDotTraversal:
    """``../secret`` 风格路径穿越被拒绝。"""

    def test_dot_dot_in_artifact_id_rejected(
        self,
        sec_api_client,
        sec_seeded_user,
    ) -> None:
        """``../secret`` 作为 artifact_id → 422（UUID 校验失败）。"""
        token = _login_and_get_token(
            sec_api_client, sec_seeded_user.email, sec_seeded_user.password
        )
        resp = sec_api_client.get(
            "/api/v1/artifacts/../secret/download",
            headers=_auth_headers(token),
        )
        # FastAPI 路由匹配：``../secret`` 不匹配 ``{artifact_id: UUID}`` 模式
        # 结果为 404（路由不匹配）或 422（UUID 校验失败）
        assert resp.status_code in (404, 422), (
            f"Path traversal should be rejected: {resp.status_code} {resp.text}"
        )

    def test_dot_dot_as_uuid_rejected(
        self,
        sec_api_client,
        sec_seeded_user,
    ) -> None:
        """``../secret`` 直接作为 UUID 参数 → 422。"""
        token = _login_and_get_token(
            sec_api_client, sec_seeded_user.email, sec_seeded_user.password
        )
        resp = sec_api_client.get(
            "/api/v1/artifacts/..%2Fsecret/download",
            headers=_auth_headers(token),
        )
        assert resp.status_code in (404, 422)

    def test_filename_with_traversal_stored_safely(
        self,
        sec_api_client,
        sec_seeded_user,
    ) -> None:
        """filename 含 ``../`` 不影响 object_key（object_key 由 SHA-256 构造）。"""
        token = _login_and_get_token(
            sec_api_client, sec_seeded_user.email, sec_seeded_user.password
        )
        resp = sec_api_client.post(
            "/api/v1/uploads",
            json={
                "filename": "../../../etc/passwd",
                "media_type": "text/csv",
                "size_bytes": 100,
            },
            headers={**_auth_headers(token), "Content-Type": "application/json"},
        )
        # 上传请求应成功（filename 仅存储到 DB，不影响 S3 key）
        assert resp.status_code == 200, (
            f"Upload with traversal filename should be safe: {resp.text}"
        )
        body = resp.json()
        # object_key 格式为 uploads/{artifact_id}，不含 ../
        assert "../" not in body["object_key"]
        assert body["object_key"].startswith("uploads/")


# ============================================================
# 2. %2e%2e/secret URL 编码穿越被拒绝
# ============================================================


class TestURLEncodedTraversal:
    """URL 编码的路径穿越被拒绝。"""

    def test_url_encoded_dot_dot_rejected(
        self,
        sec_api_client,
        sec_seeded_user,
    ) -> None:
        """``%2e%2e%2fsecret`` 作为路径 → 422/404。"""
        token = _login_and_get_token(
            sec_api_client, sec_seeded_user.email, sec_seeded_user.password
        )
        encoded_path = urllib.parse.quote("../../../etc/passwd", safe="")
        resp = sec_api_client.get(
            f"/api/v1/artifacts/{encoded_path}/download",
            headers=_auth_headers(token),
        )
        assert resp.status_code in (404, 422), (
            f"URL-encoded traversal should be rejected: {resp.status_code}"
        )

    def test_double_encoded_dot_dot_rejected(
        self,
        sec_api_client,
        sec_seeded_user,
    ) -> None:
        """双重 URL 编码 ``%252e%252e`` 作为路径 → 422/404。"""
        token = _login_and_get_token(
            sec_api_client, sec_seeded_user.email, sec_seeded_user.password
        )
        # 双重编码：%252e = %2e = .
        resp = sec_api_client.get(
            "/api/v1/artifacts/%252e%252e%252fsecret/download",
            headers=_auth_headers(token),
        )
        assert resp.status_code in (404, 422)

    def test_url_encoded_in_upload_filename_safe(
        self,
        sec_api_client,
        sec_seeded_user,
    ) -> None:
        """URL 编码的穿越字符在 filename 中不影响 object_key。"""
        token = _login_and_get_token(
            sec_api_client, sec_seeded_user.email, sec_seeded_user.password
        )
        resp = sec_api_client.post(
            "/api/v1/uploads",
            json={
                "filename": "%2e%2e/%2e%2e/secret",
                "media_type": "application/json",
                "size_bytes": 50,
            },
            headers={**_auth_headers(token), "Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "../" not in body["object_key"]
        assert ".." not in body["object_key"].split("/")[-1]


# ============================================================
# 3. ..\\secret Windows 风格穿越被拒绝
# ============================================================


class TestWindowsStyleTraversal:
    """Windows 风格 ``..\\secret`` 路径穿越被拒绝。"""

    def test_backslash_traversal_rejected(
        self,
        sec_api_client,
        sec_seeded_user,
    ) -> None:
        """``..\\secret`` 作为路径 → 422/404。"""
        token = _login_and_get_token(
            sec_api_client, sec_seeded_user.email, sec_seeded_user.password
        )
        resp = sec_api_client.get(
            "/api/v1/artifacts/..\\secret/download",
            headers=_auth_headers(token),
        )
        assert resp.status_code in (404, 422), (
            f"Windows-style traversal should be rejected: {resp.status_code}"
        )

    def test_encoded_backslash_traversal_rejected(
        self,
        sec_api_client,
        sec_seeded_user,
    ) -> None:
        """URL 编码的 ``%5c`` 反斜杠穿越 → 422/404。"""
        token = _login_and_get_token(
            sec_api_client, sec_seeded_user.email, sec_seeded_user.password
        )
        resp = sec_api_client.get(
            "/api/v1/artifacts/..%5Csecret/download",
            headers=_auth_headers(token),
        )
        assert resp.status_code in (404, 422)

    def test_windows_path_in_filename_safe(
        self,
        sec_api_client,
        sec_seeded_user,
    ) -> None:
        """Windows 路径在 filename 中不影响 object_key。"""
        token = _login_and_get_token(
            sec_api_client, sec_seeded_user.email, sec_seeded_user.password
        )
        resp = sec_api_client.post(
            "/api/v1/uploads",
            json={
                "filename": "..\\..\\windows\\system32\\config",
                "media_type": "text/csv",
                "size_bytes": 200,
            },
            headers={**_auth_headers(token), "Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "\\" not in body["object_key"]
        assert ".." not in body["object_key"]


# ============================================================
# 4. 规范化后仍在命名空间内
# ============================================================


class TestNamespaceConfinement:
    """object_key 规范化后仍在安全命名空间内。"""

    def test_object_key_from_sha256_is_safe(self) -> None:
        """_build_object_key 输出格式为 sha256/<前2位>/<digest>。"""
        digest = "a" * 64
        key = _build_object_key(digest)
        assert key == f"sha256/aa/{digest}"
        assert key.startswith("sha256/")
        assert ".." not in key
        assert "\\" not in key

    def test_object_key_no_traversal_characters(self) -> None:
        """object_key 不含路径穿越字符。"""
        for i in range(10):
            digest = f"{i:064x}"[:64]
            key = _build_object_key(digest)
            assert ".." not in key
            assert "~" not in key
            assert "\\" not in key
            assert "|" not in key
            assert "\x00" not in key

    def test_upload_key_uses_uuid_only(self) -> None:
        """临时上传 key 格式为 uploads/{UUID}，不含穿越字符。"""
        from packages.common.ids import new_id

        artifact_id = new_id()
        key = f"uploads/{artifact_id}"
        assert key.startswith("uploads/")
        # UUID 不含路径分隔符或穿越字符
        suffix = key[len("uploads/"):]
        assert "/" not in suffix
        assert ".." not in suffix
        assert "\\" not in suffix

    def test_presign_response_key_within_namespace(
        self,
        sec_api_client,
        sec_seeded_user,
    ) -> None:
        """预签名上传响应中的 object_key 在安全命名空间内。"""
        token = _login_and_get_token(
            sec_api_client, sec_seeded_user.email, sec_seeded_user.password
        )
        resp = sec_api_client.post(
            "/api/v1/uploads",
            json={
                "filename": "normal.csv",
                "media_type": "text/csv",
                "size_bytes": 1024,
            },
            headers={**_auth_headers(token), "Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        key = resp.json()["object_key"]
        assert key.startswith("uploads/")
        assert ".." not in key
        assert "\\" not in key
        # UUID 部分是合法 UUID 格式
        uuid_part = key[len("uploads/"):]
        assert len(uuid_part) == 36  # UUID 字符串长度
