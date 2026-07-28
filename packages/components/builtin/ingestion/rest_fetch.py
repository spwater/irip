"""REST 数据拉取组件。

从 HTTP/HTTPS API 拉取数据，展平为 ObservationTable。

安全要求（SSRF 防护）：
- 禁止访问内网/环回地址（127.0.0.0/8, 10.0.0.0/8,
  172.16.0.0/12, 192.168.0.0/16, 169.254.0.0/16）；
- 强制 HTTPS（除非 allow_http=True 显式允许）；
- 响应大小限制 50MB；
- 最多 3 次重定向。

参数：
- url: 请求 URL（必填）。
- method: HTTP 方法（可选，默认 GET）。
- headers: 请求头字典（可选）。
- params: 查询参数字典（可选）。
- allow_http: 是否允许 HTTP（可选，默认 False）。
- json_path: JSONPath 式点号路径定位数组（可选）。
- auth_header_secret: context.secrets 中认证头值的键名（可选）。
"""

import asyncio
import ipaddress
import json
import socket
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from packages.common.errors import AppError

from packages.components.builtin.types import ObservationTable
from packages.components.sdk import ComponentContext, ComponentResult

#: 响应大小上限（50 MB）。
_MAX_RESPONSE_BYTES: int = 50 * 1024 * 1024

#: 最大重定向次数。
_MAX_REDIRECTS: int = 3

#: 禁止的 IP 网段。
_FORBIDDEN_NETWORKS: tuple[str, ...] = (
    "127.0.0.0/8",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "169.254.0.0/16",
    "0.0.0.0/8",
    "::1/128",
    "fc00::/7",
    "fe80::/10",
)


def _check_ip_allowed(ip_str: str) -> None:
    """校验 IP 地址不在禁止网段内。

    Args:
        ip_str: IP 地址字符串。

    Raises:
        AppError: code="ssrf_blocked"，当 IP 在禁止网段内。
    """
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        raise AppError(
            code="ssrf_blocked",
            message="无法解析的目标地址",
            retryable=False,
            fields={"ip": "invalid"},
        )

    for net in _FORBIDDEN_NETWORKS:
        if ip in ipaddress.ip_network(net):
            raise AppError(
                code="ssrf_blocked",
                message="目标地址在禁止访问的网段内",
                retryable=False,
                fields={"ip": "private"},
            )


def _resolve_and_check(host: str) -> None:
    """解析主机名并校验所有解析出的 IP 不在禁止网段内。

    Args:
        host: 主机名。

    Raises:
        AppError: code="ssrf_blocked"，当任一 IP 在禁止网段内。
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise AppError(
            code="ssrf_blocked",
            message="无法解析主机名",
            retryable=False,
            fields={"host": host},
        )
    for info in infos:
        ip_str = info[4][0]
        _check_ip_allowed(ip_str)


class RESTFetch:
    """REST API 数据拉取组件（含 SSRF 防护）。"""

    async def execute(
        self,
        context: ComponentContext,
        params: dict[str, Any],
    ) -> ComponentResult:
        """从 REST API 拉取数据并输出 ObservationTable。"""
        url: str = params["url"]
        method: str = params.get("method", "GET")
        headers: dict[str, str] = params.get("headers", {})
        query_params: dict[str, Any] = params.get("params", {})
        allow_http: bool = params.get("allow_http", False)
        json_path: str | None = params.get("json_path")
        auth_header_secret: str | None = params.get("auth_header_secret")

        parsed = urlparse(url)
        scheme = parsed.scheme.lower()

        if scheme not in ("https", "http"):
            raise AppError(
                code="invalid_url",
                message="仅支持 HTTP/HTTPS 协议",
                retryable=False,
                fields={"scheme": scheme},
            )

        if scheme == "http" and not allow_http:
            raise AppError(
                code="https_required",
                message="仅允许 HTTPS（如需 HTTP 请设置 allow_http=true）",
                retryable=False,
                fields={"scheme": "http"},
            )

        # SSRF 防护：解析主机名并检查 IP
        host = parsed.hostname or ""
        _resolve_and_check(host)

        # 注入认证头（从 secrets，不回显）
        if auth_header_secret:
            token = context.secrets.get(auth_header_secret, "")
            headers = {**headers, "Authorization": f"Bearer {token}"}

        data = await self._fetch_with_redirects(
            url, method, headers, query_params
        )

        # 解析 JSON 并展平
        json_data: Any = json.loads(data)

        if json_path:
            for key in json_path.split("."):
                if isinstance(json_data, dict):
                    json_data = json_data.get(key)
                elif isinstance(json_data, list) and key.isdigit():
                    json_data = json_data[int(key)]

        records: list[dict[str, Any]]
        if isinstance(json_data, list):
            records = [
                r if isinstance(r, dict) else {"value": r}
                for r in json_data
            ]
        elif isinstance(json_data, dict):
            records = [json_data]
        else:
            records = [{"value": json_data}]

        col_set: list[str] = []
        for rec in records:
            for k in rec:
                if k not in col_set:
                    col_set.append(k)
        columns: tuple[str, ...] = tuple(col_set)

        table = ObservationTable(
            columns=columns,
            rows=tuple(records),
            source_locations=(
                {"url": parsed._replace(path=parsed.path).geturl()},
            ),
        )
        return ComponentResult(
            outputs={"observations": table},
            summary=f"从 {parsed.hostname} 拉取 {table.row_count()} 行",
            metadata={
                "row_count": table.row_count(),
                "column_count": table.column_count(),
            },
        )

    async def _fetch_with_redirects(
        self,
        url: str,
        method: str,
        headers: dict[str, str],
        query_params: dict[str, Any],
    ) -> str:
        """异步执行 HTTP 请求（含重定向与大小限制）。

        使用 httpx.AsyncClient 替代 urllib.request.urlopen，
        避免在事件循环中阻塞。手动处理重定向以在跳转前校验目标。

        Args:
            url: 请求 URL。
            method: HTTP 方法。
            headers: 请求头。
            query_params: 查询参数。

        Returns:
            str: 响应体文本。

        Raises:
            AppError: code="response_too_large"，当响应超过 50MB。
            AppError: code="too_many_redirects"，当重定向超过 3 次。
            AppError: code="ssrf_blocked"，当重定向目标在禁止网段。
        """
        current_url = url
        for _ in range(_MAX_REDIRECTS + 1):
            try:
                async with httpx.AsyncClient(
                    follow_redirects=False,
                    timeout=httpx.Timeout(30.0),
                    proxy=None,
                ) as client:
                    resp = await client.request(
                        method,
                        current_url,
                        headers=headers,
                        params=query_params if current_url == url else None,
                    )
            except httpx.HTTPError as exc:
                raise AppError(
                    code="http_error",
                    message=f"HTTP 请求失败: {exc}",
                    retryable=False,
                    fields={},
                ) from exc

            # 检查重定向（3xx 状态码）
            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("location")
                if location is None:
                    raise AppError(
                        code="http_error",
                        message=f"重定向响应缺少 Location 头: {resp.status_code}",
                        retryable=False,
                        fields={"status_code": resp.status_code},
                    )
                # 解析重定向目标（处理相对 URL）
                redirect_url = urljoin(current_url, location)

                # 校验重定向目标
                redirect_parsed = urlparse(redirect_url)
                redirect_host = redirect_parsed.hostname or ""
                await asyncio.to_thread(_resolve_and_check, redirect_host)
                if not redirect_parsed.scheme.lower().startswith(
                    "https"
                ) and not params_allow_http(
                    query_params
                ):
                    raise AppError(
                        code="https_required",
                        message="重定向目标非 HTTPS",
                        retryable=False,
                        fields={},
                    )
                current_url = redirect_url
                continue

            # 非 2xx 响应视为错误
            if resp.status_code >= 400:
                raise AppError(
                    code="http_error",
                    message=f"HTTP 请求失败: {resp.status_code}",
                    retryable=False,
                    fields={"status_code": resp.status_code},
                )

            # 读取响应（限制大小）
            chunks: list[bytes] = []
            total = 0
            async for chunk in resp.aiter_bytes(8192):
                total += len(chunk)
                if total > _MAX_RESPONSE_BYTES:
                    raise AppError(
                        code="response_too_large",
                        message="响应超过 50MB 限制",
                        retryable=False,
                        fields={"max_bytes": _MAX_RESPONSE_BYTES},
                    )
                chunks.append(chunk)
            return b"".join(chunks).decode("utf-8")

        raise AppError(
            code="too_many_redirects",
            message=f"重定向超过 {_MAX_REDIRECTS} 次",
            retryable=False,
            fields={"max_redirects": _MAX_REDIRECTS},
        )


def params_allow_http(query_params: dict[str, Any]) -> bool:
    """占位辅助：重定向场景下不允许 HTTP。"""
    return False
