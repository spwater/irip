"""IRIP 统一应用错误契约。

全平台所有可预期业务错误必须使用 AppError 表达；
API 层将其映射为统一响应体：
    {"error": {"code", "message", "retryable", "fields"}}
HTTP 状态与 code 对照由 ``packages/common/error_codes.py`` 中的
``ErrorCode`` 封闭枚举自动生成（技术设计文档 F-14/F-24 §8.4）。
"""

from dataclasses import dataclass, field
from typing import Any

from packages.common.error_codes import ErrorCode


@dataclass
class AppError(Exception):
    """应用层业务错误（可序列化、可映射 HTTP）。

    Attributes:
        code: 稳定英文错误码（如 conflict / invalid_cursor / forbidden）。
            支持传入 ``ErrorCode`` 枚举成员或字符串（向后兼容）。
        message: 面向用户的中文描述。
        retryable: 客户端是否可安全重试。
        fields: 字段级错误明细（用于表单回显），无错字段时为 {}。
    """

    code: str | ErrorCode
    message: str
    retryable: bool = False
    fields: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Exception 基类需要 message 作为 args，保证 str(err) 可读
        super().__init__(self.message)
        # 如果传入的是 ErrorCode 枚举，自动转换为字符串 code
        if isinstance(self.code, ErrorCode):
            self._code_enum: ErrorCode | None = self.code
            self.code = self.code.code
        else:
            # 字符串 code：尝试解析为 ErrorCode 枚举成员（可能为 None）
            self._code_enum = ErrorCode.from_string(self.code)

    @property
    def code_enum(self) -> ErrorCode | None:
        """返回与此错误关联的 ErrorCode 枚举成员（若已注册）。

        未注册的错误码返回 None，调用方应处理 None 情况。
        """
        return self._code_enum

    @property
    def http_status(self) -> int:
        """返回此错误对应的 HTTP 状态码。

        若错误码已注册在 ErrorCode 枚举中，返回枚举的 http_status；
        否则返回 500 作为安全默认值。
        """
        if self._code_enum is not None:
            return self._code_enum.http_status
        return 500

    def to_dict(self) -> dict[str, Any]:
        """序列化为 API 错误响应体（且仅返回这四个键）。"""
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "fields": self.fields,
        }
