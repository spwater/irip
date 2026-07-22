"""标准状态机：定义标准变量的合法状态转换。

状态流转（docs IRIP Task 10）：
    draft → in_review   (submit，提交审核)
    in_review → published (approve，审核通过并发布)
    in_review → rejected  (reject，审核拒绝)
    published → deprecated (deprecate，弃用)
    rejected → draft       (resubmit，重新编辑)

任何不在上述合法转换集合内的状态跳转均抛出 ``AppError(code="invalid_transition")``，
由 API 层映射为 HTTP 409 Conflict。
"""

from packages.common.errors import AppError


class StandardStatus:
    """标准状态常量（字符串值，与数据库 status 列对齐）。

    使用字符串常量而非 StrEnum，以便在 ORM 列与状态机之间统一引用，
    避免枚举序列化/反序列化的额外开销。
    """

    DRAFT: str = "draft"
    IN_REVIEW: str = "in_review"
    PUBLISHED: str = "published"
    REJECTED: str = "rejected"
    DEPRECATED: str = "deprecated"


#: 合法状态转换集合：(from_status, to_status)。
_VALID_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {
        (StandardStatus.DRAFT, StandardStatus.IN_REVIEW),
        (StandardStatus.IN_REVIEW, StandardStatus.PUBLISHED),
        (StandardStatus.IN_REVIEW, StandardStatus.REJECTED),
        (StandardStatus.PUBLISHED, StandardStatus.DEPRECATED),
        (StandardStatus.REJECTED, StandardStatus.DRAFT),
    }
)


def assert_transition(from_status: str, to_status: str) -> None:
    """断言状态转换合法，否则抛出 AppError(code="invalid_transition")。

    Args:
        from_status: 当前状态（如 ``"draft"``）。
        to_status: 目标状态（如 ``"in_review"``）。

    Raises:
        AppError: code="invalid_transition"，当转换不在合法集合内时。
    """
    if (from_status, to_status) not in _VALID_TRANSITIONS:
        raise AppError(
            code="invalid_transition",
            message=f"不允许从「{from_status}」转换到「{to_status}」",
            retryable=False,
            fields={"from_status": from_status, "to_status": to_status},
        )
