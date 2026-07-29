"""模型训练组件：创建模型 + 提交验证。

调用 ModelService.create_model 创建模型主记录，
调用 ModelService.submit_for_validation 将版本状态置为 pending_validation。

参数：
- model_service: ModelService 实例（通过 params 注入）；
- code: 模型代码（必填）；
- display_name: 模型显示名称（必填）；
- version_id: 待提交验证的版本 ID（必填）。
"""

from typing import Any
from uuid import UUID

from packages.common.errors import AppError
from packages.components.sdk import ComponentContext, ComponentResult


class ModelTrainComponent:
    """模型训练组件：创建模型并提交验证。"""

    async def execute(
        self,
        context: ComponentContext,
        params: dict[str, Any],
    ) -> ComponentResult:
        """执行模型创建与验证提交。

        Args:
            context: 组件执行上下文。
            params: 组件参数。

        Returns:
            ComponentResult: 执行结果，outputs 含 model_id 与 version_id。

        Raises:
            AppError: code="validation_failed"，当缺少必填参数时。
        """
        model_service = params.get("model_service")
        code: str = params.get("code", "")
        display_name: str = params.get("display_name", "")
        version_id_raw = params.get("version_id")

        if not code:
            raise AppError(
                code="validation_failed",
                message="缺少必填参数: code",
                retryable=False,
                fields={"code": code},
            )
        if not display_name:
            raise AppError(
                code="validation_failed",
                message="缺少必填参数: display_name",
                retryable=False,
                fields={"display_name": display_name},
            )
        if version_id_raw is None:
            raise AppError(
                code="validation_failed",
                message="缺少必填参数: version_id",
                retryable=False,
                fields={"version_id": version_id_raw},
            )
        if model_service is None:
            raise AppError(
                code="validation_failed",
                message="缺少必填参数: model_service",
                retryable=False,
                fields={},
            )

        version_id = UUID(str(version_id_raw))

        # 创建模型（若已存在则复用）
        try:
            model = await model_service.create_model(code, display_name)
            model_id = model.id
        except AppError as exc:
            if exc.code == "conflict":
                # 模型已存在，获取已有模型
                models = await model_service.list_models()
                existing = next((m for m in models if m.code == code), None)
                if existing is None:
                    raise
                model_id = existing.id
            else:
                raise

        # 提交验证
        version = await model_service.submit_for_validation(model_id, version_id)

        return ComponentResult(
            outputs={
                "model_id": str(model_id),
                "version_id": str(version.id),
                "version": version.version,
                "status": version.status,
            },
            summary=f"模型 {code} 已创建并提交验证（版本 {version.version}）",
            metadata={
                "model_id": str(model_id),
                "version_id": str(version.id),
                "status": version.status,
            },
        )
