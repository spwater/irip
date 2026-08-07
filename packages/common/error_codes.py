"""封闭错误码枚举 — 单一真相来源（F-14/F-24）。

每个 ErrorCode 成员携带：
  - ``code``: 稳定英文错误码字符串（如 ``"conflict"``），与 API 响应体一致；
  - ``http_status``: 对应 HTTP 状态码。

使用约定（技术设计文档 §8.4）：
  1. ``AppError`` 的 ``code`` 字段必须是 ``ErrorCode`` 枚举值（或兼容的字符串）；
  2. ``apps/api/main.py`` 的 ``_STATUS_MAP`` 由 ``ErrorCode.to_status_map()`` 自动生成；
  3. 新增错误码必须在此枚举中注册，CI 检查"所有被抛出的错误码都有映射"。

向后兼容：现有代码中 ``AppError(code="conflict", ...)`` 的字符串用法仍然有效，
``ErrorCode.from_string("conflict")`` 可将字符串解析为枚举成员。
"""

import enum


class ErrorCode(enum.Enum):
    """封闭错误码枚举，每个码携带 HTTP 状态。

    Attributes:
        code: 稳定英文错误码字符串。
        http_status: 对应的 HTTP 状态码。
    """

    # ---- 401 认证类 ----
    INVALID_CREDENTIALS = ("invalid_credentials", 401)
    TOKEN_EXPIRED = ("token_expired", 401)
    REFRESH_REPLAYED = ("refresh_replayed", 401)

    # ---- 429 限流 ----
    RATE_LIMITED = ("rate_limited", 429)

    # ---- 403 禁止类 ----
    FORBIDDEN = ("forbidden", 403)
    SELF_APPROVAL_FORBIDDEN = ("self_approval_forbidden", 403)
    SSRF_BLOCKED = ("ssrf_blocked", 403)
    PATH_TRAVERSAL_BLOCKED = ("path_traversal_blocked", 403)
    TENANT_MISMATCH = ("tenant_mismatch", 403)

    # ---- 404 未找到 ----
    NOT_FOUND = ("not_found", 404)
    SECRET_NOT_FOUND = ("secret_not_found", 404)

    # ---- 409 冲突类 ----
    CONFLICT = ("conflict", 409)
    INVALID_TRANSITION = ("invalid_transition", 409)
    PUBLISHED_VERSION_IMMUTABLE = ("published_version_immutable", 409)
    OBJECT_CYCLE = ("object_cycle", 409)
    CANDIDATE_NOT_PENDING = ("candidate_not_pending", 409)
    IMMUTABLE_VIOLATION = ("immutable_violation", 409)

    # ---- 413 请求体过大 ----
    FILE_TOO_LARGE = ("file_too_large", 413)

    # ---- 415 不支持的媒体类型 ----
    UNSUPPORTED_MEDIA_TYPE = ("unsupported_media_type", 415)

    # ---- 422 语义错误 ----
    HASH_MISMATCH = ("hash_mismatch", 422)
    SIZE_MISMATCH = ("size_mismatch", 422)
    VALIDATION_FAILED = ("validation_failed", 422)
    INCOMPATIBLE_DIMENSIONS = ("incompatible_dimensions", 422)
    UNKNOWN_UNIT = ("unknown_unit", 422)
    INVALID_CURSOR = ("invalid_cursor", 422)
    SELF_RELATION = ("self_relation", 422)
    REFERENCE_NOT_PUBLISHED = ("reference_not_published", 422)
    MISSING_OBSERVATION = ("missing_observation", 422)
    DUPLICATE_OBSERVATION = ("duplicate_observation", 422)
    MISSING_UNIT = ("missing_unit", 422)
    INVALID_OBSERVATION = ("invalid_observation", 422)
    NORMALIZED_WITHOUT_RAW = ("normalized_without_raw", 422)
    TEMPLATE_NOT_PUBLISHED = ("template_not_published", 422)
    METHOD_NOT_PUBLISHED = ("method_not_published", 422)
    QUALITY_BLOCKED = ("quality_blocked", 422)
    COMPONENT_UNAVAILABLE = ("component_unavailable", 422)
    EVIDENCE_NOT_FROZEN = ("evidence_not_frozen", 422)
    RECIPE_NOT_PUBLISHED = ("recipe_not_published", 422)
    DERIVATION_NOT_SUCCEEDED = ("derivation_not_succeeded", 422)
    INVALID_MANIFEST = ("invalid_manifest", 422)
    UNKNOWN_TOOL = ("unknown_tool", 422)
    CONFIRMATION_REQUIRED = ("confirmation_required", 422)
    UNKNOWN_JOB_KIND = ("unknown_job_kind", 422)

    # ---- 422 补充：组件/模型/AI 运行时 ----
    AI_CANCELLED = ("ai_cancelled", 422)
    AI_EMPTY_RESPONSE = ("ai_empty_response", 422)
    AI_NOT_CONFIGURED = ("ai_not_configured", 422)
    AI_PARSE_FAILED = ("ai_parse_failed", 422)
    AI_REQUEST_FAILED = ("ai_request_failed", 502)
    AI_TIMEOUT = ("ai_timeout", 504)
    MISSING_DEPENDENCY = ("missing_dependency", 422)
    UNSUPPORTED_FORMAT = ("unsupported_format", 422)
    FORBIDDEN_QUERY = ("forbidden_query", 403)
    HTTP_ERROR = ("http_error", 502)
    HTTPS_REQUIRED = ("https_required", 422)
    INVALID_URL = ("invalid_url", 422)
    RESPONSE_TOO_LARGE = ("response_too_large", 413)
    TOO_MANY_REDIRECTS = ("too_many_redirects", 422)
    COMPONENT_CANCELLED = ("component_cancelled", 422)
    COMPONENT_FAILED = ("component_failed", 500)
    COMPONENT_NOT_FOUND = ("component_not_found", 404)
    INVALID_OUTPUT = ("invalid_output", 422)
    INVALID_MODEL_ARTIFACT = ("invalid_model_artifact", 422)
    MODEL_FAILED = ("model_failed", 500)
    MODEL_NOT_LOADED = ("model_not_loaded", 422)
    MODEL_TIMEOUT = ("model_timeout", 504)
    INPUT_VALIDATION_FAILED = ("input_validation_failed", 422)
    INVALID_STATE = ("invalid_state", 409)
    OUTSIDE_APPLICABILITY_DOMAIN = ("outside_applicability_domain", 422)

    # ---- 422/413/500 补充：AI 数值计算工具 ----
    NUMERIC_EXPRESSION_REJECTED = ("numeric_expression_rejected", 422)
    NUMERIC_INVALID_SOURCE = ("numeric_invalid_source", 422)
    NUMERIC_FIELD_NOT_FOUND = ("numeric_field_not_found", 422)
    NUMERIC_NON_NUMERIC = ("numeric_non_numeric", 422)
    NUMERIC_DOMAIN_ERROR = ("numeric_domain_error", 422)
    NUMERIC_DIVIDE_BY_ZERO = ("numeric_divide_by_zero", 422)
    NUMERIC_UNIT_CONFLICT = ("numeric_unit_conflict", 422)
    NUMERIC_SIZE_LIMIT = ("numeric_size_limit", 413)
    NUMERIC_NON_FINITE_RESULT = ("numeric_non_finite_result", 422)
    NUMERIC_TIMEOUT = ("numeric_timeout", 422)
    NUMERIC_INTERNAL_ERROR = ("numeric_internal_error", 500)

    # ---- 403 补充：研究发布权限 ----
    ACL_EXCEEDS_ENVELOPE = ("acl_exceeds_envelope", 403)

    # ---- 500 服务器内部错误 ----
    INTERNAL_ERROR = ("internal_error", 500)
    INGESTION_ERROR = ("ingestion_error", 500)
    MAX_RETRIES_EXCEEDED = ("max_retries_exceeded", 500)

    # ---- 502 上游网关错误 ----
    CONNECTOR_ERROR = ("connector_error", 502)
    AI_PROVIDER_ERROR = ("ai_provider_error", 502)

    # ---- 504 网关超时 ----
    COMPONENT_TIMEOUT = ("component_timeout", 504)

    def __init__(self, code: str, http_status: int) -> None:
        self.code = code
        self.http_status = http_status

    @classmethod
    def from_string(cls, code: str) -> "ErrorCode | None":
        """将错误码字符串解析为 ErrorCode 枚举成员。

        向后兼容入口：现有代码中使用 ``AppError(code="conflict")`` 时，
        通过此方法查找对应的枚举成员。

        Args:
            code: 错误码字符串（如 ``"conflict"``）。

        Returns:
            ErrorCode | None: 匹配的枚举成员，未注册时返回 None。
        """
        for member in cls:
            if member.code == code:
                return member
        return None

    @classmethod
    def to_status_map(cls) -> dict[str, int]:
        """自动生成 code→http_status 映射表。

        用于替代 ``apps/api/main.py`` 中的手工 ``_STATUS_MAP``。

        Returns:
            dict[str, int]: 所有注册错误码到 HTTP 状态码的映射。
        """
        return {member.code: member.http_status for member in cls}

    @classmethod
    def all_codes(cls) -> set[str]:
        """返回所有已注册的错误码字符串集合。

        用于 CI 穷尽性检查：确认代码中所有被抛出的错误码都已在枚举中注册。

        Returns:
            set[str]: 所有已注册错误码字符串。
        """
        return {member.code for member in cls}
