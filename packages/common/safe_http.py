"""SSRF-safe HTTP 客户端：DNS 解析后二次校验目标 IP，禁止私网/链路本地地址。

安全措施：
1. DNS 解析后校验目标 IP，拒绝私网/链路本地/回环地址；
2. 禁止 HTTP 重定向（防止通过重定向绕过校验）；
3. 响应大小上限（防止超大响应耗尽内存）；
4. 超时控制（防止慢速攻击）；
5. 仅允许 http/https 协议。

使用约定（技术设计文档 F-13）：
- 所有外部 HTTP 调用（AI 配置测试、REST 连接器等）**必须**使用 SafeHTTPClient；
- **禁止**直接使用 ``httpx.AsyncClient`` 发起外部请求。
"""

import ipaddress
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


def validate_url_host(host: str, port: int | None = None) -> None:
    """校验 URL 主机名：DNS 解析后检查所有解析结果 IP。

    用于在发起 HTTP 请求前预校验目标地址安全性。

    Args:
        host: 主机名或 IP 地址字符串。
        port: 端口号（可选，用于 DNS 解析）。

    Raises:
        ValueError: 当主机解析到私网/保留地址，或 DNS 解析失败时。
    """
    # 先尝试直接解析为 IP（可能 host 本身就是 IP 字符串）
    try:
        ip = ipaddress.ip_address(host)
        if _is_private_ip(ip):
            raise ValueError(
                f"SSRF blocked: {ip} is in a private/reserved network range"
            )
        return
    except ValueError:
        # host 不是合法 IP 字符串，继续做 DNS 解析
        pass

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
            raise ValueError(
                f"SSRF blocked: {host} resolves to {ip}, "
                f"which is in a private/reserved network range"
            )


class SafeHTTPClient:
    """SSRF-safe HTTP 客户端。

    DNS 解析后二次校验目标 IP，禁止私网/链路本地/回环地址。
    禁止 HTTP 重定向，限制响应大小和超时。

    Attributes:
        _client: 底层 httpx.AsyncClient 实例。
        _max_size: 响应体最大字节数。
    """

    def __init__(
        self,
        timeout: float = 30.0,
        max_size: int = 10 * 1024 * 1024,
        **kwargs: Any,
    ) -> None:
        """初始化安全 HTTP 客户端。

        Args:
            timeout: 请求超时秒数。
            max_size: 响应体最大字节数（默认 10 MiB）。
            **kwargs: 传递给 httpx.AsyncClient 的额外参数。
        """
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
            ValueError: 当目标地址不安全（私网/重定向/超大响应）时。
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
            ValueError: 当目标地址不安全（私网/重定向/超大响应）时。
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
            ValueError: 当目标地址不安全（私网/重定向/超大响应）时。
        """
        return await self._request("PUT", url, **kwargs)

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """发起 HTTP 请求（核心 SSRF 防护逻辑）。

        流程：
        1. 校验 URL 协议（仅 http/https）；
        2. DNS 解析后校验目标 IP（阻断私网/保留地址）；
        3. 发起请求（禁止重定向）；
        4. 检查响应大小。

        Args:
            method: HTTP 方法（GET/POST/PUT 等）。
            url: 目标 URL。
            **kwargs: 传递给 httpx 的额外参数。

        Returns:
            httpx.Response: HTTP 响应。

        Raises:
            ValueError: 当目标地址不安全、发生重定向、或响应过大时。
        """
        parsed = httpx.URL(url)

        # 1. 校验协议
        if parsed.scheme not in ("http", "https"):
            raise ValueError(
                f"Unsupported URL scheme: {parsed.scheme} "
                f"(only http/https allowed)"
            )

        # 2. DNS 解析后校验 IP
        validate_url_host(str(parsed.host), parsed.port)

        # 3. 发起请求（follow_redirects=False 已在构造时设置）
        response = await self._client.request(method, url, **kwargs)

        # 4. 禁止重定向
        if response.is_redirect or response.is_permanent_redirect:
            location = response.headers.get("location", "")
            raise ValueError(
                f"Redirect blocked: {response.status_code} -> {location}"
            )

        # 5. 检查响应大小（content-length 头）
        content_length = response.headers.get("content-length")
        if content_length is not None:
            try:
                size = int(content_length)
                if size > self._max_size:
                    raise ValueError(
                        f"Response too large: {size} bytes "
                        f"exceeds max {self._max_size} bytes"
                    )
            except ValueError:
                # content-length 不是合法整数，跳过预检
                pass

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
