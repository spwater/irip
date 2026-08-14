"""研究域 — 可信执行子包。

包含可信执行引擎、运行服务、调度器与可信仓库：
- ResearchOrchestrator: 研究编排器
- AnalysisRunService: 分析运行服务
- ResearchScheduler: 研究调度器
- ResearchRepositoryTrusted: 可信仓库
- ThreeSegmentValidator: 三段式校验器
"""

# 向后兼容：re-export ResearchOrchestrator，
# 使 ``from packages.research.execution import ResearchOrchestrator`` 仍可工作。
from packages.research.execution.orchestrator import ResearchOrchestrator  # noqa: F401

__all__ = ["ResearchOrchestrator"]
