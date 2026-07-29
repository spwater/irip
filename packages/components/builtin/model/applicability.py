"""模型适用域检查组件：调用 ApplicabilityChecker。

参数：
- inputs: 输入参数字典（必填）；
- applicability_domain: 适用域字典（必填）。

输出：
- valid: 校验是否通过；
- errors: 错误信息列表。
"""

from typing import Any

from packages.common.errors import AppError
from packages.components.sdk import ComponentContext, ComponentResult
from packages.models.applicability import ApplicabilityChecker


class ModelApplicabilityComponent:
    """模型适用域检查组件。"""

    async def execute(
        self,
        context: ComponentContext,
        params: dict[str, Any],
    ) -> ComponentResult:
        """执行适用域检查。

        Args:
            context: 组件执行上下文。
            params: 组件参数。

        Returns:
            ComponentResult: 执行结果，outputs 含 valid 与 errors。

        Raises:
            AppError: code="validation_failed"，当缺少必填参数时。
        """
        inputs: dict[str, Any] | None = params.get("inputs")
        applicability_domain: dict[str, Any] | None = params.get("applicability_domain")

        if inputs is None:
            raise AppError(
                code="validation_failed",
                message="缺少必填参数: inputs",
                retryable=False,
                fields={},
            )
        if applicability_domain is None:
            raise AppError(
                code="validation_failed",
                message="缺少必填参数: applicability_domain",
                retryable=False,
                fields={},
            )

        checker = ApplicabilityChecker()
        result = checker.check(inputs, applicability_domain)

        return ComponentResult(
            outputs={
                "valid": result.valid,
                "errors": list(result.errors),
            },
            summary=("适用域检查通过" if result.valid else "适用域检查未通过"),
            metadata={
                "valid": result.valid,
                "error_count": len(result.errors),
            },
            diagnostics=({"errors": list(result.errors)} if result.errors else None),
        )
