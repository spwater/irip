"""IRIP 分页游标（keyset pagination）。

格式（与 docs/arch-v0.md §7.4 对齐）：
    base64url( JSON {"v": <稳定排序值>, "id": "<UUID>"} )

- 服务端仅信任 decode() 的产物；任何畸形输入抛 AppError(code="invalid_cursor")；
- 默认页大小 20，最大 100（常量见下方）。
"""

import base64
import binascii
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from packages.common.errors import AppError

DEFAULT_PAGE_SIZE: int = 20
MAX_PAGE_SIZE: int = 100


def _invalid_cursor(detail: str) -> AppError:
    """构造统一的分页游标错误。"""
    return AppError(
        code="invalid_cursor",
        message=f"分页游标无效：{detail}",
        retryable=False,
        fields={"cursor": detail},
    )


@dataclass(frozen=True)
class PageCursor:
    """keyset 分页游标：稳定排序值 + 唯一 ID 作为决胜键。

    Attributes:
        sort_value: 稳定排序列的值（如 RFC 3339 时间字符串），需可 JSON 序列化。
        id: 同排序值下的决胜键（实体 UUID）。
    """

    sort_value: Any
    id: UUID

    def encode(self) -> str:
        """编码为 base64url 字符串（可安全放入 URL query）。"""
        payload = json.dumps(
            {"v": self.sort_value, "id": str(self.id)},
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return base64.urlsafe_b64encode(payload).decode("ascii")

    @classmethod
    def decode(cls, cursor: str) -> "PageCursor":
        """解码 base64url 游标。

        Args:
            cursor: encode() 产出的字符串。

        Returns:
            PageCursor: 解析结果。

        Raises:
            AppError: code="invalid_cursor"，当 base64/JSON/字段/UUID 任一不合法。
        """
        try:
            raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        except (binascii.Error, UnicodeEncodeError, ValueError) as exc:
            raise _invalid_cursor("base64url 解码失败") from exc

        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise _invalid_cursor("JSON 解析失败") from exc

        if not isinstance(payload, dict) or "v" not in payload or "id" not in payload:
            raise _invalid_cursor("缺少必要字段 v / id")

        try:
            cursor_id = UUID(str(payload["id"]))
        except (ValueError, AttributeError, TypeError) as exc:
            raise _invalid_cursor("id 字段不是合法 UUID") from exc

        return cls(sort_value=payload["v"], id=cursor_id)
