"""安全测试：上传限制（大小 + MIME 白名单）。

覆盖（docs/arch-v0.md §4.3 工件上传 + §7.4 输入校验）：
- 100 MiB 上传限制（超过上限 → 413 file_too_large）；
- MIME 白名单（CSV/JSON/PDF/XLSX 等允许，非法类型拒绝）；
- 超大文件拒绝（200 MiB → 413）；
- 非法 MIME 类型拒绝（application/x-msdownload → 422）。

使用 FastAPI TestClient 通过 /api/v1/uploads 端点验证。
"""

import pytest

from packages.common.artifacts import (
    ALLOWED_MEDIA_TYPES,
    MAX_UPLOAD_SIZE_BYTES,
)

# ============================================================
# 辅助函数
# ============================================================


def _login_and_get_token(client, email: str, password: str) -> str:
    """登录并返回 access_token。"""
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    return resp.json()["access_token"]


def _auth_headers(token: str) -> dict[str, str]:
    """构造认证请求头。"""
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _presign_upload(
    client,
    token: str,
    filename: str,
    media_type: str,
    size_bytes: int,
) -> tuple[int, dict]:
    """调用预签名上传端点，返回 (status_code, body)。"""
    resp = client.post(
        "/api/v1/uploads",
        json={
            "filename": filename,
            "media_type": media_type,
            "size_bytes": size_bytes,
        },
        headers=_auth_headers(token),
    )
    is_json = resp.headers.get("content-type", "").startswith("application/json")
    body = resp.json() if is_json else {}
    return resp.status_code, body


# ============================================================
# 1. 100 MiB 上传限制
# ============================================================


class TestUploadSizeLimit:
    """100 MiB 上传大小限制。"""

    def test_max_size_constant_is_100_mib(self) -> None:
        """MAX_UPLOAD_SIZE_BYTES 恰好为 100 MiB。"""
        assert MAX_UPLOAD_SIZE_BYTES == 100 * 1024 * 1024
        assert MAX_UPLOAD_SIZE_BYTES == 104_857_600

    def test_just_under_limit_accepted(
        self,
        sec_api_client,
        sec_seeded_user,
    ) -> None:
        """恰好在限制以下（100 MiB - 1 字节）→ 接受。"""
        token = _login_and_get_token(
            sec_api_client, sec_seeded_user.email, sec_seeded_user.password
        )
        status, body = _presign_upload(
            sec_api_client,
            token,
            "data.csv",
            "text/csv",
            MAX_UPLOAD_SIZE_BYTES - 1,
        )
        assert status == 200, f"Upload within limit should succeed: {body}"
        assert "upload_url" in body

    def test_exactly_at_limit_accepted(
        self,
        sec_api_client,
        sec_seeded_user,
    ) -> None:
        """恰好等于限制（100 MiB）→ 接受。"""
        token = _login_and_get_token(
            sec_api_client, sec_seeded_user.email, sec_seeded_user.password
        )
        status, body = _presign_upload(
            sec_api_client,
            token,
            "data.json",
            "application/json",
            MAX_UPLOAD_SIZE_BYTES,
        )
        assert status == 200, f"Upload at limit should succeed: {body}"


# ============================================================
# 2. 超大文件拒绝
# ============================================================


class TestOversizedFileRejection:
    """超过 100 MiB 的文件被拒绝。"""

    def test_oversized_file_rejected(
        self,
        sec_api_client,
        sec_seeded_user,
    ) -> None:
        """200 MiB 文件 → 413 file_too_large。"""
        token = _login_and_get_token(
            sec_api_client, sec_seeded_user.email, sec_seeded_user.password
        )
        status, body = _presign_upload(
            sec_api_client,
            token,
            "huge.bin",
            "application/json",
            200 * 1024 * 1024,
        )
        assert status == 413, f"Oversized file should be rejected: {body}"
        assert body["error"]["code"] == "file_too_large"
        assert body["error"]["fields"]["size_bytes"] == 200 * 1024 * 1024

    def test_one_byte_over_limit_rejected(
        self,
        sec_api_client,
        sec_seeded_user,
    ) -> None:
        """超过限制 1 字节 → 413。"""
        token = _login_and_get_token(
            sec_api_client, sec_seeded_user.email, sec_seeded_user.password
        )
        status, body = _presign_upload(
            sec_api_client,
            token,
            "data.pdf",
            "application/pdf",
            MAX_UPLOAD_SIZE_BYTES + 1,
        )
        assert status == 413
        assert body["error"]["code"] == "file_too_large"

    def test_zero_size_accepted(
        self,
        sec_api_client,
        sec_seeded_user,
    ) -> None:
        """0 字节文件 → 接受（边界值）。"""
        token = _login_and_get_token(
            sec_api_client, sec_seeded_user.email, sec_seeded_user.password
        )
        status, body = _presign_upload(
            sec_api_client,
            token,
            "empty.json",
            "application/json",
            0,
        )
        assert status == 200, f"Zero-size upload should succeed: {body}"


# ============================================================
# 3. MIME 白名单
# ============================================================


class TestMIMEWhitelist:
    """MIME 类型白名单校验。"""

    @pytest.mark.parametrize(
        "media_type",
        [
            "text/csv",
            "application/json",
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "text/plain",
            "image/png",
            "image/jpeg",
        ],
    )
    def test_whitelisted_mime_accepted(
        self,
        sec_api_client,
        sec_seeded_user,
        media_type: str,
    ) -> None:
        """白名单内 MIME 类型 → 200。"""
        token = _login_and_get_token(
            sec_api_client, sec_seeded_user.email, sec_seeded_user.password
        )
        status, body = _presign_upload(
            sec_api_client,
            token,
            "test.file",
            media_type,
            1024,
        )
        assert status == 200, f"Whitelisted MIME {media_type} should be accepted: {body}"

    @pytest.mark.parametrize(
        "media_type",
        [
            "application/x-msdownload",
            "application/x-executable",
            "text/html",
            "application/x-sh",
            "image/svg+xml",
        ],
    )
    def test_non_whitelisted_mime_rejected(
        self,
        sec_api_client,
        sec_seeded_user,
        media_type: str,
    ) -> None:
        """非白名单 MIME 类型 → 422 unsupported_media_type。"""
        token = _login_and_get_token(
            sec_api_client, sec_seeded_user.email, sec_seeded_user.password
        )
        status, body = _presign_upload(
            sec_api_client,
            token,
            "malicious.file",
            media_type,
            1024,
        )
        assert status == 422, f"Non-whitelisted MIME {media_type} should be rejected: {body}"
        assert body["error"]["code"] == "unsupported_media_type"

    def test_csv_in_whitelist(self) -> None:
        """text/csv 在白名单中。"""
        assert "text/csv" in ALLOWED_MEDIA_TYPES

    def test_json_in_whitelist(self) -> None:
        """application/json 在白名单中。"""
        assert "application/json" in ALLOWED_MEDIA_TYPES

    def test_pdf_in_whitelist(self) -> None:
        """application/pdf 在白名单中。"""
        assert "application/pdf" in ALLOWED_MEDIA_TYPES

    def test_xlsx_in_whitelist(self) -> None:
        """XLSX MIME 在白名单中。"""
        assert (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            in ALLOWED_MEDIA_TYPES
        )

    def test_executable_not_in_whitelist(self) -> None:
        """可执行文件 MIME 不在白名单中。"""
        assert "application/x-msdownload" not in ALLOWED_MEDIA_TYPES
        assert "application/x-executable" not in ALLOWED_MEDIA_TYPES


# ============================================================
# 4. 未认证上传拒绝
# ============================================================


class TestUnauthenticatedUpload:
    """未认证的上传请求被拒绝。"""

    def test_no_token_rejected(self, sec_api_client) -> None:
        """缺少认证令牌 → 401。"""
        status, body = _presign_upload(
            sec_api_client,
            "",
            "test.csv",
            "text/csv",
            1024,
        )
        assert status == 401
        assert body["error"]["code"] == "invalid_credentials"
