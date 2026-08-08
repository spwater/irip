"""keyset 分页游标编码/解码工具。

供各子仓库模块共享使用，避免重复实现。
"""

import base64
import binascii
import json
from datetime import datetime
from uuid import UUID


def _encode_cursor(created_at: datetime, entity_id: UUID) -> str:
    """编码 keyset 分页游标。

    Args:
        created_at: 排序时间戳。
        entity_id: 唯一决胜键。

    Returns:
        str: base64url 编码的游标字符串。
    """
    payload = json.dumps(
        {"v": created_at.isoformat(), "id": str(entity_id)},
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    """解码 keyset 分页游标。

    Args:
        cursor: base64url 编码的游标字符串。

    Returns:
        tuple[datetime, UUID]: (排序时间戳, 实体 ID)。

    Raises:
        ValueError: 当游标格式不合法时。
    """
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
    except (binascii.Error, UnicodeEncodeError, ValueError) as exc:
        raise ValueError(f"无效的游标编码: {cursor}") from exc

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"无效的游标 JSON: {cursor}") from exc

    if not isinstance(payload, dict) or "v" not in payload or "id" not in payload:
        raise ValueError(f"游标缺少必要字段 v / id: {cursor}")

    try:
        created_at = datetime.fromisoformat(str(payload["v"]))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"游标 v 字段不是合法 ISO 时间: {payload['v']}") from exc

    try:
        entity_id = UUID(str(payload["id"]))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"游标 id 字段不是合法 UUID: {payload['id']}") from exc

    return created_at, entity_id
