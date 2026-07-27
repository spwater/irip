"""REST API 连接器：按 secret_id 解析 base URL + 令牌，预览与流式读取 JSON 响应。

安全约定：
- base URL 与认证令牌通过 SecretStore 按 secret_id 解析，绝不返回、绝不记录日志；
- 令牌仅用于请求头 Authorization: Bearer <token>，不出现在响应中。

secret value 格式（JSON 字符串）：
    {"base_url": "https://api.example.com", "token": "xxx", "headers": {...}}

实现 Connector 协议：
- preview(source, limit): GET/POST 端点，解析 JSON，返回前 limit 行；
- read(source): 流式 yield SourceRecord。
"""

import json
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING
from uuid import UUID

import httpx

from packages.common.errors import AppError
from packages.common.safe_http import SafeHTTPClient
from packages.connectors.contracts import (
    ConnectorSource,
    PreviewTable,
    SourceRecord,
)

if TYPE_CHECKING:
    from packages.connectors.mapping import SecretStore


class RestConnector:
    """REST API 数据源连接器。

    Attributes:
        _secret_store: 密钥存储，用于解析 secret_id → base URL + token。
    """

    def __init__(self, secret_store: "SecretStore") -> None:
        """初始化 REST 连接器。

        Args:
            secret_store: 密钥存储实例。
        """
        self._secret_store = secret_store

    async def preview(
        self, source: ConnectorSource, limit: int = 100
    ) -> PreviewTable:
        """预览 REST 端点响应前 limit 行。

        Args:
            source: rest 数据源，config 须含 secret_id / path / method。
            limit: 预览行数上限。

        Returns:
            PreviewTable: 列名 + 行 + 总行数。

        Raises:
            AppError: code="validation_failed"，当缺少必要字段时。
            AppError: code="secret_not_found"，当 secret 不存在时。
            AppError: code="connector_error"，当请求失败时。
        """
        secret_id, path, method = self._extract(source)
        secret_value = await self._secret_store.get(secret_id)
        base_url, headers = self._parse_secret(secret_value)
        columns, rows = await self._fetch(base_url, path, method, headers, limit)
        return PreviewTable(
            columns=columns,
            rows=tuple(tuple(r) for r in rows),
            row_count=len(rows),
        )

    async def read(
        self, source: ConnectorSource
    ) -> AsyncIterator[SourceRecord]:
        """流式读取 REST 端点全部响应记录。

        Args:
            source: rest 数据源。

        Yields:
            SourceRecord: 每行一条记录。
        """
        secret_id, path, method = self._extract(source)
        secret_value = await self._secret_store.get(secret_id)
        base_url, headers = self._parse_secret(secret_value)
        columns, rows = await self._fetch(base_url, path, method, headers, 10**9)
        for row in rows:
            fields: dict[str, str | None] = {}
            for idx, col in enumerate(columns):
                value = row[idx] if idx < len(row) else None
                fields[col] = None if value is None else str(value)
            yield SourceRecord(fields=fields)

    # ---- 内部辅助 ----

    @staticmethod
    def _extract(source: ConnectorSource) -> tuple[UUID, str, str]:
        """从 source.config 提取 secret_id / path / method。"""
        raw_secret_id = source.config.get("secret_id")
        path = source.config.get("path")
        method = str(source.config.get("method", "GET")).upper()
        if not raw_secret_id:
            raise AppError(
                code="validation_failed",
                message="rest 数据源缺少 secret_id",
                retryable=False,
                fields={"field": "secret_id"},
            )
        if not path:
            raise AppError(
                code="validation_failed",
                message="rest 数据源缺少 path",
                retryable=False,
                fields={"field": "path"},
            )
        if method not in ("GET", "POST"):
            raise AppError(
                code="validation_failed",
                message="rest 数据源 method 仅支持 GET / POST",
                retryable=False,
                fields={"method": method},
            )
        try:
            secret_id = UUID(str(raw_secret_id))
        except (ValueError, TypeError) as exc:
            raise AppError(
                code="validation_failed",
                message="secret_id 不是合法 UUID",
                retryable=False,
                fields={"secret_id": raw_secret_id},
            ) from exc
        return secret_id, str(path), method

    @staticmethod
    def _parse_secret(secret_value: str) -> tuple[str, dict[str, str]]:
        """解析 secret value JSON，返回 (base_url, headers)。

        Raises:
            AppError: code="validation_failed"，当 secret 格式不合法时。
        """
        try:
            payload = json.loads(secret_value)
        except json.JSONDecodeError as exc:
            raise AppError(
                code="validation_failed",
                message="rest secret 不是合法 JSON",
                retryable=False,
                fields={},
            ) from exc
        if not isinstance(payload, dict) or "base_url" not in payload:
            raise AppError(
                code="validation_failed",
                message="rest secret 缺少 base_url",
                retryable=False,
                fields={},
            )
        base_url = str(payload["base_url"])
        headers: dict[str, str] = {}
        extra_headers = payload.get("headers")
        if isinstance(extra_headers, dict):
            headers.update({str(k): str(v) for k, v in extra_headers.items()})
        token = payload.get("token")
        if token:
            headers.setdefault("Authorization", f"Bearer {token}")
        return base_url, headers

    @staticmethod
    async def _fetch(
        base_url: str,
        path: str,
        method: str,
        headers: dict[str, str],
        limit: int,
    ) -> tuple[tuple[str, ...], list[list]]:
        """发起 HTTP 请求并解析 JSON 数组为 (列名, 行列表)。

        安全约定（技术设计文档 F-13）：
        - 使用 SafeHTTPClient 发起请求（SSRF 防护）；
        - DNS 解析后校验目标 IP，拒绝私网/保留地址。
        """
        url = base_url.rstrip("/") + "/" + path.lstrip("/")
        try:
            async with SafeHTTPClient(timeout=30.0, max_size=10 * 1024 * 1024) as client:
                if method == "GET":
                    response = await client.get(url, headers=headers)
                else:
                    response = await client.post(url, headers=headers)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise AppError(
                code="connector_error",
                message=f"REST 请求失败：{exc}",
                retryable=True,
                fields={},
            ) from exc
        except ValueError as exc:
            raise AppError(
                code="ssrf_blocked",
                message=f"REST 请求被 SSRF 防护阻断：{exc}",
                retryable=False,
                fields={},
            ) from exc

        if not isinstance(data, list):
            raise AppError(
                code="validation_failed",
                message="REST 响应必须是 JSON 对象数组",
                retryable=False,
                fields={},
            )

        columns: list[str] = []
        seen: set[str] = set()
        for obj in data[:limit]:
            if not isinstance(obj, dict):
                continue
            for key in obj:
                if key not in seen:
                    seen.add(key)
                    columns.append(key)

        col_index = {col: idx for idx, col in enumerate(columns)}
        rows: list[list] = []
        for obj in data[:limit]:
            if not isinstance(obj, dict):
                continue
            row = [None] * len(columns)
            for key, value in obj.items():
                idx = col_index.get(key)
                if idx is not None:
                    row[idx] = value
            rows.append(row)
        return tuple(columns), rows
