"""研究域 — 分析计划子包。

包含分析计划的编排、上下文路由与模型网关：
- PlanService: 分析计划服务
- ContextRouter: 上下文路由
- ModelGateway: 模型网关
"""

# 向后兼容：re-export PlanService，
# 使 ``from packages.research.planning import PlanService`` 仍可工作。
from packages.research.planning.plan_service import PlanService

__all__ = ["PlanService"]
