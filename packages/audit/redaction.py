"""审计载荷脱敏：敏感字段替换为 ``[REDACTED]``。

脱敏字段名（不区分大小写匹配）：
  password, token, secret, api_key, refresh_token, access_token

匹配规则：字段名转为小写后与脱敏字段集合比较。
非脱敏字段原样保留。嵌套字典递归脱敏。
"""

from typing import Any

#: 需要脱敏的字段名集合（小写）。
_REDACTED_FIELDS: frozenset[str] = frozenset(
    {
        "password",
        "token",
        "secret",
        "api_key",
        "refresh_token",
        "access_token",
    }
)

#: 脱敏替换值。
_REDACTED_VALUE: str = "[REDACTED]"


def redact(payload: dict[str, Any]) -> dict[str, Any]:
    """对字典中的敏感字段进行脱敏。

    递归处理嵌套字典。列表中的字典元素也会递归脱敏。
    非字典值原样保留。

    Args:
        payload: 原始载荷字典。

    Returns:
        dict[str, Any]: 脱敏后的载荷字典（新对象，不修改原始字典）。

    Examples:
        >>> redact({"password": "secret", "value": 3})
        {'password': '[REDACTED]', 'value': 3}
        >>> redact({"API_KEY": "abc", "name": "test"})
        {'API_KEY': '[REDACTED]', 'name': 'test'}
    """
    result: dict[str, Any] = {}
    for key, value in payload.items():
        if key.lower() in _REDACTED_FIELDS:
            result[key] = _REDACTED_VALUE
        elif isinstance(value, dict):
            result[key] = redact(value)
        elif isinstance(value, list):
            result[key] = [redact(item) if isinstance(item, dict) else item for item in value]
        else:
            result[key] = value
    return result
