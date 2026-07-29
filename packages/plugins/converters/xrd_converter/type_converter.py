"""
类型转换模块 —— 将 RAS_RAW 文件中的原始字符串值转换为合适的 JSON 类型。

转换规则：
  - 空字符串 → None (null)
  - 纯整数（含负数） → int
  - 纯小数（含负数） → float
  - 其他 → 保持字符串原样（"None"/"N/A"/"ON"/"OFF" 等原样保留）
"""

import re

# 纯整数正则：可选负号 + 纯数字
_INT_PATTERN = re.compile(r"^-?\d+$")

# 纯小数正则：可选负号 + 数字 + 小数点 + 数字
_FLOAT_PATTERN = re.compile(r"^-?\d+\.\d+$")


def convert_value(raw: str) -> int | float | str | None:
    """将原始字符串值转换为合适的 JSON 类型。

    Args:
        raw: 从文件中读取的原始字符串值（已去除引号等外层包裹）。

    Returns:
        - None：当 raw 为空字符串时
        - int：当 raw 为纯整数时
        - float：当 raw 为纯小数时
        - str：其他情况，保留原样
    """
    # 空字符串 → None
    if raw == "":
        return None

    # 纯整数 → int
    if _INT_PATTERN.match(raw):
        return int(raw)

    # 纯小数 → float
    if _FLOAT_PATTERN.match(raw):
        return float(raw)

    # 其他 → 保持字符串
    return raw
