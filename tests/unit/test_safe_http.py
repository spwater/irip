"""H-05 SSRF 防护 SafeHTTPClient 单元测试。

覆盖 ``packages/common/safe_http.py``：
- 私网 IP 被阻断（10.x / 172.16-31.x / 192.168.x）；
- 链路本地地址被阻断（169.254.x）；
- 回环地址被阻断（127.x / ::1）；
- IPv6 私网/链路本地被阻断（fc00::/7 / fe80::/10）；
- 响应超过大小限制被阻断（content-length 预检 + 流式累计）；
- 重定向重新 DNS 校验（防止通过重定向绕过 SSRF 校验）；
- 非法协议被拒绝。

本测试为纯单元测试，不依赖数据库或外部服务。
HTTP 响应通过 mock httpx.AsyncClient 实现。
"""

import ipaddress
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from packages.common.safe_http import (
    PRIVATE_NETWORKS,
    ResponseTooLargeError,
    SafeHTTPClient,
    SSRFBlockedError,
    _is_private_ip,
    _resolve_and_validate,
    validate_url_host,
)


class TestIsPrivateIp:
    """_is_private_ip 函数测试。"""

    @pytest.mark.parametrize(
        "ip_str",
        [
            "10.0.0.1",
            "10.255.255.255",
            "172.16.0.1",
            "172.31.255.255",
            "192.168.0.1",
            "192.168.255.255",
            "127.0.0.1",
            "127.255.255.255",
            "0.0.0.1",
            "169.254.1.1",
        ],
    )
    def test_private_ipv4_blocked(self, ip_str: str) -> None:
        """私网/保留 IPv4 地址被识别为私网。"""
        assert _is_private_ip(ipaddress.ip_address(ip_str)) is True

    @pytest.mark.parametrize("ip_str", ["8.8.8.8", "1.1.1.1", "93.184.216.34", "172.15.0.1", "172.32.0.1"])
    def test_public_ipv4_allowed(self, ip_str: str) -> None:
        """公网 IPv4 地址不被识别为私网。"""
        assert _is_private_ip(ipaddress.ip_address(ip_str)) is False

    def test_ipv6_loopback_blocked(self) -> None:
        """IPv6 回环地址被阻断。"""
        assert _is_private_ip(ipaddress.ip_address("::1")) is True

    def test_ipv6_ula_blocked(self) -> None:
        """IPv6 唯一本地地址被阻断。"""
        assert _is_private_ip(ipaddress.ip_address("fc00::1")) is True
        assert _is_private_ip(ipaddress.ip_address("fd00::1")) is True

    def test_ipv6_link_local_blocked(self) -> None:
        """IPv6 链路本地地址被阻断。"""
        assert _is_private_ip(ipaddress.ip_address("fe80::1")) is True

    def test_ipv6_public_allowed(self) -> None:
        """IPv6 公网地址不被阻断。"""
        assert _is_private_ip(ipaddress.ip_address("2606:4700::1")) is False


class TestResolveAndValidate:
    """_resolve_and_validate 函数测试（直接传入 IP 字符串）。"""

    @pytest.mark.parametrize(
        "ip_str",
        ["192.168.1.1", "10.0.0.1", "172.16.0.1", "169.254.1.1", "127.0.0.1", "0.0.0.1"],
    )
    def test_private_ipv4_string_raises_ssrf(self, ip_str: str) -> None:
        """私网 IPv4 字符串触发 SSRFBlockedError。"""
        with pytest.raises(SSRFBlockedError, match="SSRF blocked"):
            _resolve_and_validate(ip_str)

    def test_public_ipv4_string_returns_ip(self) -> None:
        """公网 IPv4 字符串返回已验证的 IP。"""
        result = _resolve_and_validate("8.8.8.8")
        assert result == "8.8.8.8"

    def test_ipv6_loopback_string_raises(self) -> None:
        """IPv6 回环字符串触发 SSRFBlockedError。"""
        with pytest.raises(SSRFBlockedError):
            _resolve_and_validate("::1")

    def test_empty_hostname_raises(self) -> None:
        """空主机名抛 ValueError。"""
        with pytest.raises(ValueError, match="Empty hostname"):
            _resolve_and_validate("")


class TestValidateUrlHost:
    """validate_url_host 函数测试。"""

    def test_validate_private_ip_raises(self) -> None:
        """校验私网 IP 主机抛 SSRFBlockedError。"""
        with pytest.raises(SSRFBlockedError):
            validate_url_host("10.0.0.1")

    def test_validate_public_ip_passes(self) -> None:
        """校验公网 IP 主机通过。"""
        validate_url_host("8.8.8.8")  # 不抛异常即通过


class TestSafeHTTPClientSSRF:
    """SafeHTTPClient SSRF 阻断。"""

    @pytest.mark.parametrize(
        "url",
        [
            "http://192.168.1.1/secret",
            "http://10.0.0.1/admin",
            "http://172.16.0.1/internal",
            "http://169.254.169.254/latest/meta-data/",  # AWS metadata
            "http://127.0.0.1:8080/local",
            "http://0.0.0.0/",
        ],
    )
    async def test_client_blocks_private_ip(self, url: str) -> None:
        """SafeHTTPClient 对私网 IP URL 抛 SSRFBlockedError。"""
        client = SafeHTTPClient()
        try:
            with pytest.raises(SSRFBlockedError):
                await client.get(url)
        finally:
            await client.aclose()

    async def test_client_blocks_ipv6_loopback(self) -> None:
        """SafeHTTPClient 对 IPv6 回环地址抛 SSRFBlockedError。"""
        client = SafeHTTPClient()
        try:
            with pytest.raises(SSRFBlockedError):
                await client.get("http://[::1]/")
        finally:
            await client.aclose()

    async def test_client_blocks_ipv6_ula(self) -> None:
        """SafeHTTPClient 对 IPv6 ULA 地址抛 SSRFBlockedError。"""
        client = SafeHTTPClient()
        try:
            with pytest.raises(SSRFBlockedError):
                await client.get("http://[fc00::1]/")
        finally:
            await client.aclose()

    async def test_client_blocks_invalid_scheme(self) -> None:
        """非 http/https 协议被拒绝。"""
        client = SafeHTTPClient(allow_private=True)
        try:
            with pytest.raises(ValueError, match="Unsupported URL scheme"):
                await client.get("ftp://example.com/file")
        finally:
            await client.aclose()

    async def test_allow_private_bypasses_ssrf_check(self) -> None:
        """allow_private=True 时跳过 SSRF 校验（本地开发用）。"""
        client = SafeHTTPClient(allow_private=True)
        try:
            # mock httpx 返回正常响应，验证 SSRF 校验被跳过
            mock_response = MagicMock()
            mock_response.is_redirect = False
            mock_response.is_stream_consumed = True
            mock_response.headers = {}
            mock_response.status_code = 200
            with patch.object(
                client._client, "request", new_callable=AsyncMock, return_value=mock_response
            ):
                resp = await client.get("http://192.168.1.1/local")
                assert resp.status_code == 200
        finally:
            await client.aclose()


class TestResponseSizeLimit:
    """响应超过大小限制被阻断。"""

    async def test_content_length_exceeds_limit(self) -> None:
        """content-length 预检超限被阻断。"""
        client = SafeHTTPClient(max_size=100, allow_private=True)
        try:
            mock_response = MagicMock()
            mock_response.is_redirect = False
            mock_response.is_stream_consumed = True
            mock_response.headers = {"content-length": "200"}

            with patch.object(
                client._client, "request", new_callable=AsyncMock, return_value=mock_response
            ):
                with pytest.raises(ResponseTooLargeError, match="exceeds max 100"):
                    await client.get("http://example.com/data")
        finally:
            await client.aclose()

    async def test_streaming_exceeds_limit(self) -> None:
        """流式累计字节超限被阻断（无 content-length）。"""

        async def mock_aiter_bytes(chunk_size: int = 64 * 1024):
            yield b"x" * 60
            yield b"x" * 60  # total 120 > 100

        client = SafeHTTPClient(max_size=100, allow_private=True)
        try:
            mock_response = MagicMock()
            mock_response.is_redirect = False
            mock_response.is_stream_consumed = False
            mock_response.headers = {}
            mock_response.aiter_bytes = mock_aiter_bytes

            with patch.object(
                client._client, "request", new_callable=AsyncMock, return_value=mock_response
            ):
                with pytest.raises(ResponseTooLargeError, match="exceeded max 100"):
                    await client.get("http://example.com/stream")
        finally:
            await client.aclose()

    async def test_streaming_within_limit_succeeds(self) -> None:
        """流式累计字节在限制内正常返回。"""

        async def mock_aiter_bytes(chunk_size: int = 64 * 1024):
            yield b"x" * 50
            yield b"y" * 30  # total 80 < 100

        client = SafeHTTPClient(max_size=100, allow_private=True)
        try:
            mock_response = MagicMock()
            mock_response.is_redirect = False
            mock_response.is_stream_consumed = False
            mock_response.headers = {}
            mock_response.aiter_bytes = mock_aiter_bytes

            with patch.object(
                client._client, "request", new_callable=AsyncMock, return_value=mock_response
            ):
                resp = await client.get("http://example.com/small")
                assert resp is not None
        finally:
            await client.aclose()

    async def test_content_length_within_limit_passes(self) -> None:
        """content-length 在限制内通过预检。"""
        client = SafeHTTPClient(max_size=500, allow_private=True)
        try:
            mock_response = MagicMock()
            mock_response.is_redirect = False
            mock_response.is_stream_consumed = True
            mock_response.headers = {"content-length": "200"}

            with patch.object(
                client._client, "request", new_callable=AsyncMock, return_value=mock_response
            ):
                resp = await client.get("http://example.com/ok")
                assert resp is not None
        finally:
            await client.aclose()


class TestRedirectRevalidation:
    """重定向重新 DNS 校验。"""

    async def test_redirect_to_private_ip_blocked(self) -> None:
        """重定向到私网 IP 被 DNS 重新校验阻断。"""
        client = SafeHTTPClient()
        try:
            # 第一次请求到公网 IP，返回重定向到私网 IP
            mock_response = MagicMock()
            mock_response.is_redirect = True
            mock_response.headers = {"location": "http://192.168.1.1/secret"}

            with patch.object(
                client._client, "request", new_callable=AsyncMock, return_value=mock_response
            ):
                with pytest.raises(SSRFBlockedError, match="SSRF blocked"):
                    await client.get("http://8.8.8.8/redirect")
        finally:
            await client.aclose()

    async def test_redirect_to_loopback_blocked(self) -> None:
        """重定向到回环地址被阻断。"""
        client = SafeHTTPClient()
        try:
            mock_response = MagicMock()
            mock_response.is_redirect = True
            mock_response.headers = {"location": "http://127.0.0.1:9090/admin"}

            with patch.object(
                client._client, "request", new_callable=AsyncMock, return_value=mock_response
            ):
                with pytest.raises(SSRFBlockedError):
                    await client.get("http://8.8.8.8/redirect-to-loopback")
        finally:
            await client.aclose()

    async def test_redirect_to_metadata_service_blocked(self) -> None:
        """重定向到云元数据服务（169.254.169.254）被阻断。"""
        client = SafeHTTPClient()
        try:
            mock_response = MagicMock()
            mock_response.is_redirect = True
            mock_response.headers = {"location": "http://169.254.169.254/latest/meta-data/"}

            with patch.object(
                client._client, "request", new_callable=AsyncMock, return_value=mock_response
            ):
                with pytest.raises(SSRFBlockedError):
                    await client.get("http://8.8.8.8/meta-redirect")
        finally:
            await client.aclose()

    async def test_redirect_to_public_ip_followed(self) -> None:
        """重定向到公网 IP 被允许跟随。"""
        client = SafeHTTPClient()
        try:
            # 第一次请求返回重定向
            redirect_response = MagicMock()
            redirect_response.is_redirect = True
            redirect_response.headers = {"location": "http://1.1.1.1/final"}

            # 第二次请求（跟随重定向后）返回正常响应
            final_response = MagicMock()
            final_response.is_redirect = False
            final_response.is_stream_consumed = True
            final_response.headers = {}

            call_count = 0

            async def mock_request(method, url, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    return redirect_response
                return final_response

            with patch.object(client._client, "request", side_effect=mock_request):
                resp = await client.get("http://8.8.8.8/redirect-to-public")
                assert resp is not None
                assert call_count == 2
        finally:
            await client.aclose()

    async def test_too_many_redirects_raises(self) -> None:
        """超过最大重定向次数抛异常。"""
        client = SafeHTTPClient()
        try:
            mock_response = MagicMock()
            mock_response.is_redirect = True
            mock_response.headers = {"location": "http://1.1.1.1/loop"}

            with patch.object(
                client._client, "request", new_callable=AsyncMock, return_value=mock_response
            ):
                with pytest.raises(ValueError, match="Too many redirects"):
                    await client.get("http://8.8.8.8/infinite-redirect")
        finally:
            await client.aclose()


class TestExceptionSeparation:
    """SSRF 与超限异常分离（不被吞掉）。"""

    async def test_ssrf_error_is_ssrf_blocked_not_too_large(self) -> None:
        """SSRF 阻断抛 SSRFBlockedError 而非 ResponseTooLargeError。"""
        client = SafeHTTPClient()
        try:
            with pytest.raises(SSRFBlockedError) as exc_info:
                await client.get("http://10.0.0.1/")
            assert not isinstance(exc_info.value, ResponseTooLargeError)
        finally:
            await client.aclose()

    async def test_too_large_error_is_not_ssrf(self) -> None:
        """超限抛 ResponseTooLargeError 而非 SSRFBlockedError。"""
        client = SafeHTTPClient(max_size=10, allow_private=True)
        try:
            mock_response = MagicMock()
            mock_response.is_redirect = False
            mock_response.is_stream_consumed = True
            mock_response.headers = {"content-length": "100"}

            with patch.object(
                client._client, "request", new_callable=AsyncMock, return_value=mock_response
            ):
                with pytest.raises(ResponseTooLargeError) as exc_info:
                    await client.get("http://example.com/big")
                assert not isinstance(exc_info.value, SSRFBlockedError)
        finally:
            await client.aclose()


class TestPrivateNetworksConfig:
    """私网地址段配置完整性。"""

    def test_private_networks_contains_expected_ranges(self) -> None:
        """PRIVATE_NETWORKS 包含核心私网/保留地址段。"""
        expected = [
            "10.0.0.0/8",
            "172.16.0.0/12",
            "192.168.0.0/16",
            "169.254.0.0/16",
            "127.0.0.0/8",
            "0.0.0.0/8",
            "::1/128",
            "fc00::/7",
            "fe80::/10",
        ]
        actual = [str(net) for net in PRIVATE_NETWORKS]
        for exp in expected:
            assert exp in actual, f"缺少地址段: {exp}"
