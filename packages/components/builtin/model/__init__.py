"""IRIP 内置模型组件包（V2-T04）。

提供模型生命周期相关的四个组件：
- model_train: 创建模型 + 提交验证；
- model_evaluate: 验证模型版本；
- model_applicability: 适用域检查；
- model_predict: 模型预测，写 model_execution 事实。

这些组件通过 ComponentContext 注入 ModelService（由管线编排器
在 context.artifact_service 同级的依赖中提供）。
"""

from packages.components.builtin.model.applicability import (
    ModelApplicabilityComponent,
)
from packages.components.builtin.model.evaluate import (
    ModelEvaluateComponent,
)
from packages.components.builtin.model.predict import (
    ModelPredictComponent,
)
from packages.components.builtin.model.train import ModelTrainComponent

__all__ = [
    "ModelApplicabilityComponent",
    "ModelEvaluateComponent",
    "ModelPredictComponent",
    "ModelTrainComponent",
]
