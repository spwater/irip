"""模型预测组件：调用 ModelService.predict，写 model_execution 事实。

参数：
- model_service: ModelService 实例（通过 params 注入）；
- model_id: 模型 ID（必填）；
- inputs: 输入参数字典（必填）。

输出：
- predictions: 预测结果字典；
- model_version_id: 使用的模型版本 ID；
- fact_id: 写入的 model_execution 事实 ID（可空）。
"""

from typing import Any
from uuid import UUID

from packages.common.errors import AppError
from packages.components.sdk import ComponentContext, ComponentResult


class ModelPredictComponent:
    """模型预测组件。"""

    async def execute(
        self,
        context: ComponentContext,
        params: dict[str, Any],
    ) -> ComponentResult:
        """执行模型预测。

        Args:
            context: 组件执行上下文。
            params: 组件参数。

        Returns:
            ComponentResult: 执行结果，outputs 含 predictions 与 fact_id。

        Raises:
            AppError: code="validation_failed"，当缺少必填参数时。
        """
        model_service = params.get("model_service")
        model_id_raw = params.get("model_id")
        inputs: dict[str, Any] | None = params.get("inputs")

        if model_id_raw is None:
            raise AppError(
                code="validation_failed",
                message="缺少必填参数: model_id",
                retryable=False,
                fields={},
            )
        if inputs is None:
            raise AppError(
                code="validation_failed",
                message="缺少必填参数: inputs",
                retryable=False,
                fields={},
            )
        if model_service is None:
            raise AppError(
                code="validation_failed",
                message="缺少必填参数: model_service",
                retryable=False,
                fields={},
            )

        model_id = UUID(str(model_id_raw))

        result = await model_service.predict(model_id, inputs)

        return ComponentResult(
            outputs={
                "predictions": dict(result.predictions),
                "model_version_id": str(result.model_version_id),
                "version": result.version,
                "fact_id": (str(result.fact_id) if result.fact_id else None),
            },
            summary=(f"模型 {model_id} 预测完成（版本 {result.version}）"),
            metadata={
                "model_id": str(model_id),
                "model_version_id": str(result.model_version_id),
                "version": result.version,
                "fact_id": (str(result.fact_id) if result.fact_id else None),
            },
        )
