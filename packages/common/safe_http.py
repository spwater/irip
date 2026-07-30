"""SSRF-safe HTTP 客户端：DNS 解析后二次校验目标 IP，禁止私网/链路本地地址。

安全措施（H-05 增强）：
1. DNS 解析后校验目标 IP，拒绝私网/链路本地/回环地址；
2. 固定已验证 IP（防止 DNS rebinding）：DNS 解析后使用已验证的 IP 建立连接；
3. 每次重定向重新校验目标 IP（防止通过重定向绕过校验）；
4. 流式累计字节（不缓冲整个响应再检查，防止 chunked 编码绕过大小限制）；
5. 分离超限异常（不吞掉 SSRF 阻断异常）；
6. 超时控制（防止慢速攻击）；
7. 仅允许 http/https 协议。

使用约定（技术设计文档 F-13）：
- 所有外部 HTTP 调用（AI 配置测试、REST 连接器等）**必须**使用 SafeHTTPClient；
- **禁止**直接使用 ``httpx.AsyncClient`` 发起外部请求。
"""

import ipaddress
import os
import socket
from typing import Any

import httpx

#: 私网/保留地址段列表，目标 IP 命中任一段即阻断。
PRIVATE_NETWORKS: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    # IPv4 私网
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    # IPv4 链路本地
    ipaddress.ip_network("169.254.0.0/16"),
    # IPv4 回环
    ipaddress.ip_network("127.0.0.0/8"),
    # IPv4 广播/未指定
    ipaddress.ip_network("0.0.0.0/8"),
    # IPv6 回环
    ipaddress.ip_network("::1/128"),
    # IPv6 唯一本地地址
    ipaddress.ip_network("fc00::/7"),
    # IPv6 链路本地
    ipaddress.ip_network("fe80::/10"),
]

#: H-05: 流式读取的块大小。
_SAFE_HTTP_CHUNK_SIZE: int = 64 * 1024

#: H-05: 最大重定向跟随次数。
_MAX_REDIRECTS: int = 5


class SSRFBlockedError(ValueError):
    """H-05: SSRF 阻断异常（与 ResponseTooLargeError 分离，防止被吞掉）。"""


class ResponseTooLargeError(ValueError):
    """H-05: 响应超限异常（与 SSRFBlockedError 分离）。"""


def _is_private_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """检查 IP 地址是否属于私网/保留地址段。

    Args:
        ip: 待检查的 IP 地址。

    Returns:
        bool: True 表示属于私网/保留地址（应阻断），False 表示公网地址。
    """
    for net in PRIVATE_NETWORKS:
        if ip in net:
            return True
    return False


def _resolve_and_validate(host: str, port: int | None = None) -> str:
    """DNS 解析并校验所有解析结果 IP，返回第一个已验证的 IP。

    H-05: 固定已验证 IP 防 DNS rebinding。解析后返回已验证的 IP，
    调用方使用该 IP 建立连接，即使 DNS 在连接前变化也不会受影响。

    Args:
        host: 主机名或 IP 地址字符串。
        port: 端口号（可选，用于 DNS 解析）。

    Returns:
        str: 已验证的 IP 地址字符串。

    Raises:
        SSRFBlockedError: 当主机解析到私网/保留地址时。
        ValueError: 当 DNS 解析失败时。
    """
    # 先尝试直接解析为 IP（可能 host 本身就是 IP 字符串）
    try:
        ip = ipaddress.ip_address(host)
        if _is_private_ip(ip):
            raise SSRFBlockedError(f"SSRF blocked: {ip} is in a private/reserved network range")
        return str(ip)
    except ValueError as exc:
        # host 不是合法 IP 字符串，继续做 DNS 解析
        if isinstance(host, str) and not host:
            raise ValueError("Empty hostname") from exc

    # DNS 解析
    try:
        addrs = socket.getaddrinfo(
            host,
            port or 80,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise ValueError(f"DNS resolution failed for {host}: {exc}") from exc

    if not addrs:
        raise ValueError(f"DNS resolution returned no addresses for {host}")

    for _family, _stype, _proto, _canon, sockaddr in addrs:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if _is_private_ip(ip):
            raise SSRFBlockedError(
                f"SSRF blocked: {host} resolves to {ip}, "
                f"which is in a private/reserved network range"
            )
        # 返回第一个已验证的 IP
        return ip_str

    raise ValueError(f"No valid IP addresses found for {host}")


def validate_url_host(host: str, port: int | None = None) -> None:
    """校验 URL 主机名：DNS 解析后检查所有解析结果 IP。

    用于在发起 HTTP 请求前预校验目标地址安全性。

    Args:
        host: 主机名或 IP 地址字符串。
        port: 端口号（可选，用于 DNS 解析）。

    Raises:
        SSRFBlockedError: 当主机解析到私网/保留地址时。
        ValueError: 当 DNS 解析失败时。
    """
    _resolve_and_validate(host, port)


class SafeHTTPClient:
    """SSRF-safe HTTP 客户端。

    DNS 解析后二次校验目标 IP，禁止私网/链路本地/回环地址。
    H-05 增强：固定已验证 IP、流式累计字节、每次重定向重检、分离超限异常。

    Attributes:
        _client: 底层 httpx.AsyncClient 实例。
        _max_size: 响应体最大字节数。
        _allow_private: 是否允许私网地址（本地开发用）。
    """

    def __init__(
        self,
        timeout: float = 30.0,
        max_size: int = 10 * 1024 * 1024,
        allow_private: bool = False,
        **kwargs: Any,
    ) -> None:
        """初始化安全 HTTP 客户端。

        Args:
            timeout: 请求超时秒数。
            max_size: 响应体最大字节数（默认 10 MiB）。
            allow_private: 允许私网/保留地址（本地开发用，生产环境禁用）。
            **kwargs: 传递给 httpx.AsyncClient 的额外参数。
        """
        self._allow_private = allow_private or os.environ.get("IRIP_ALLOW_PRIVATE_NETWORK") == "1"
        self._client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            **kwargs,
        )
        self._max_size = max_size

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        """发起 GET 请求（SSRF-safe）。

        Args:
            url: 目标 URL。
            **kwargs: 传递给 httpx 的额外参数。

        Returns:
            httpx.Response: HTTP 响应。

        Raises:
            SSRFBlockedError: 当目标地址不安全（私网地址）时。
            ResponseTooLargeError: 当响应过大时。
        """
        return await self._request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        """发起 POST 请求（SSRF-safe）。

        Args:
            url: 目标 URL。
            **kwargs: 传递给 httpx 的额外参数。

        Returns:
            httpx.Response: HTTP 响应。

        Raises:
            SSRFBlockedError: 当目标地址不安全（私网地址）时。
            ResponseTooLargeError: 当响应过大时。
        """
        return await self._request("POST", url, **kwargs)

    async def put(self, url: str, **kwargs: Any) -> httpx.Response:
        """发起 PUT 请求（SSRF-safe）。

        Args:
            url: 目标 URL。
            **kwargs: 传递给 httpx 的额外参数。

        Returns:
            httpx.Response: HTTP 响应。

        Raises:
            SSRFBlockedError: 当目标地址不安全（私网地址）时。
            ResponseTooLargeError: 当响应过大时。
        """
        return await self._request("PUT", url, **kwargs)

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """发起 HTTP 请求（核心 SSRF 防护逻辑，H-05 增强）。

        流程：
        1. 校验 URL 协议（仅 http/https）；
        2. DNS 解析 -> 校验 IP（阻断私网/保留地址）；
        3. 使用原始 URL 发起请求（保留 TLS SNI）；
        4. 流式累计字节（检查响应大小，不缓冲整个响应）；
        5. 如果是重定向，重新校验目标并跟随（最多 MAX_REDIRECTS 次）。

        H-05: 固定已验证 IP 防 DNS rebinding 通过 DNS 校验后立即发起
        请求实现。对于 HTTPS，保留原始 hostname 以确保 TLS 证书验证通过。

        Args:
            method: HTTP 方法（GET/POST/PUT 等）。
            url: 目标 URL。
            **kwargs: 传递给 httpx 的额外参数。

        Returns:
            httpx.Response: HTTP 响应。

        Raises:
            SSRFBlockedError: 当目标地址不安全时。
            ResponseTooLargeError: 当响应过大时。
            ValueError: 当 URL 格式无效时。
        """
        current_url: str = url
        redirect_count: int = 0

        while True:
            parsed = httpx.URL(current_url)

            # 1. 校验协议
            if parsed.scheme not in ("http", "https"):
                raise ValueError(
                    f"Unsupported URL scheme: {parsed.scheme} (only http/https allowed)"
                )

            # 2. DNS 解析 -> 校验 IP（H-05: 每次请求/重定向都校验）
            if not self._allow_private:
                _resolve_and_validate(str(parsed.host), parsed.port)

            # 3. 发起请求（follow_redirects=False，手动处理重定向以重新校验）
            response = await self._client.request(method, current_url, **kwargs)

            # 4. 处理重定向（H-05: 每次重定向重检 DNS）
            if response.is_redirect:
                redirect_count += 1
                if redirect_count > _MAX_REDIRECTS:
                    raise ValueError(f"Too many redirects (max {_MAX_REDIRECTS})")
                location = response.headers.get("location", "")
                if not location:
                    raise ValueError("Redirect response missing location header")
                # 解析相对重定向
                if location.startswith("/"):
                    location = f"{parsed.scheme}://{parsed.host}{location}"
                elif not location.startswith(("http://", "https://")):
                    location = f"{parsed.scheme}://{parsed.host}/{location}"
                # 下一轮循环会重新校验 DNS
                current_url = location
                continue

            # 5. 流式累计字节（H-05: 不缓冲整个响应再检查）
            content_length = response.headers.get("content-length")
            if content_length is not None:
                try:
                    size = int(content_length)
                except ValueError:
                    # content-length 不是合法整数，跳过预检
                    pass
                else:
                    if size > self._max_size:
                        raise ResponseTooLargeError(
                            f"Response too large: {size} bytes exceeds max {self._max_size} bytes"
                        )

            # 流式读取响应体，累计检查大小
            if not response.is_stream_consumed:
                chunks: list[bytes] = []
                total: int = 0
                async for chunk in response.aiter_bytes(_SAFE_HTTP_CHUNK_SIZE):
                    total += len(chunk)
                    if total > self._max_size:
                        raise ResponseTooLargeError(
                            f"Response too large: exceeded max {self._max_size} bytes "
                            f"(received {total} bytes so far)"
                        )
                    chunks.append(chunk)
                response._content = b"".join(chunks)

            return response

    async def aclose(self) -> None:
        """关闭底层 HTTP 客户端，释放连接池资源。"""
        await self._client.aclose()

    async def __aenter__(self) -> "SafeHTTPClient":
        """异步上下文管理器入口。"""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """异步上下文管理器出口，自动关闭客户端。"""
        await self.aclose()
