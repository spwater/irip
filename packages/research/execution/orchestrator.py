"""研究编排器：向后兼容 re-export。

原 orchestrator.py（1473 行）已按功能域拆分为：
- orchestrator_base.py: ResearchOrchestratorBase（依赖注入 + 共享常量 + 跨 Mixin 接口）；
- context_builder.py: 范围检测 + 输入包 + 快照数据 + 研究上下文；
- step_executor.py: 步骤执行（python/llm/mixed）+ Insight 候选提取 + 回退脚本；
- result_assembler.py: 拓扑排序 + 事件 + 状态 + 覆盖率聚合；
- orchestrator_core.py: ResearchOrchestrator 装配 + execute_run / cancel_run。

本文件仅 re-export ``ResearchOrchestrator`` 及模块级常量，保持旧式导入路径
``from packages.research.execution.orchestrator import ResearchOrchestrator`` 与
``from packages.research.orchestrator import ResearchOrchestrator``（经 sys.modules 别名）
仍可工作。业务逻辑见上述子模块。
"""

from packages.research.execution.orchestrator_base import (
    DEFAULT_RESOURCE_LIMITS,
    DEFAULT_WARM_DURATION,
    MAX_RETRY_ATTEMPTS,
    SANDBOX_IMAGE_DIGEST,
)
from packages.research.execution.orchestrator_core import ResearchOrchestrator

__all__ = [
    "ResearchOrchestrator",
    "MAX_RETRY_ATTEMPTS",
    "DEFAULT_RESOURCE_LIMITS",
    "DEFAULT_WARM_DURATION",
    "SANDBOX_IMAGE_DIGEST",
]
