"""模型评估组件：调用 ModelService.validate 验证模型版本。

参数：
- model_service: ModelService 实例（通过 params 注入）；
- model_id: 模型 ID（必填）；
- version_id: 版本 ID（必填）；
- dataset_artifact_id: 验证数据集工件 ID（可选）；
- metrics: 验证指标字典（可选，如 R²、RMSE）；
- applicability_domain: 适用域字典（可选）。
"""

from typing import Any
from uuid import UUID

from packages.common.errors import AppError
from packages.components.sdk import ComponentContext, ComponentResult


class ModelEvaluateComponent:
    """模型评估组件：验证模型版本。"""

    async def execute(
        self,
        context: ComponentContext,
        params: dict[str, Any],
    ) -> ComponentResult:
        """执行模型版本验证。

        Args:
            context: 组件执行上下文。
            params: 组件参数。

        Returns:
            ComponentResult: 执行结果，outputs 含 version_id 与 status。

        Raises:
            AppError: code="validation_failed"，当缺少必填参数时。
        """
        model_service = params.get("model_service")
        model_id_raw = params.get("model_id")
        version_id_raw = params.get("version_id")
        dataset_artifact_id_raw = params.get("dataset_artifact_id")
        metrics: dict[str, Any] | None = params.get("metrics")
        applicability_domain: dict[str, Any] | None = params.get(
            "applicability_domain"
        )

        if model_id_raw is None:
            raise AppError(
                code="validation_failed",
                message="缺少必填参数: model_id",
                retryable=False,
                fields={},
            )
        if version_id_raw is None:
            raise AppError(
                code="validation_failed",
                message="缺少必填参数: version_id",
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
        version_id = UUID(str(version_id_raw))
        dataset_artifact_id: UUID | None = None
        if dataset_artifact_id_raw is not None:
            dataset_artifact_id = UUID(str(dataset_artifact_id_raw))

        version = await model_service.validate(
            model_id=model_id,
            version_id=version_id,
            dataset_artifact_id=dataset_artifact_id,
            metrics=metrics,
            applicability_domain=applicability_domain,
        )

        return ComponentResult(
            outputs={
                "version_id": str(version.id),
                "status": version.status,
                "metrics": version.metrics_json,
            },
            summary=f"模型版本 {version.version} 验证完成（状态: {version.status}）",
            metadata={
                "version_id": str(version.id),
                "status": version.status,
                "metrics": version.metrics_json,
            },
        )
