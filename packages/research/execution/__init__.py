"""研究域 — 可信执行子包。

包含可信执行引擎、运行服务、沙箱、调度器与可信仓库：
- ResearchOrchestrator: 研究编排器
- AnalysisRunService: 分析运行服务
- DockerSandboxRuntime / WarmPoolManager: 沙箱运行时
- ResearchScheduler: 研究调度器
- ResearchRepositoryTrusted: 可信仓库
- ThreeSegmentValidator: 三段式校验器
- PermissionEnvelopeCalculator: 权限信封计算器
"""

# 向后兼容：re-export ResearchOrchestrator，
# 使 ``from packages.research.execution import ResearchOrchestrator`` 仍可工作。
from packages.research.execution.orchestrator import ResearchOrchestrator  # noqa: F401

__all__ = ["ResearchOrchestrator"]
