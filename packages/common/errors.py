"""IRIP 统一应用错误契约。

全平台所有可预期业务错误必须使用 AppError 表达；
API 层将其映射为统一响应体：
    {"error": {"code", "message", "retryable", "fields"}}
HTTP 状态与 code 对照见 docs/arch-v0.md §7.2。
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AppError(Exception):
    """应用层业务错误（可序列化、可映射 HTTP）。

    Attributes:
        code: 稳定英文错误码（如 conflict / invalid_cursor / forbidden）。
        message: 面向用户的中文描述。
        retryable: 客户端是否可安全重试。
        fields: 字段级错误明细（用于表单回显），无错字段时为 {}。
    """

    code: str
    message: str
    retryable: bool = False
    fields: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Exception 基类需要 message 作为 args，保证 str(err) 可读
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        """序列化为 API 错误响应体（且仅返回这四个键）。"""
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "fields": self.fields,
        }
