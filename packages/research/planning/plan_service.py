"""计划服务：向后兼容 re-export。

原 plan_service.py（1534 行）已按功能域拆分为：
- plan_base.py: PlanServiceBase（依赖注入 + 共享基础设施）；
- plan_generator.py: generate_plan 及辅助方法；
- plan_confirmer.py: confirm_plan；
- plan_reviser.py: revise_plan；
- plan_analyzer.py: analyze_data / extract_insight；
- plan_core.py: PlanService 装配 + list_plans / get_plan。

本文件仅 re-export ``PlanService``，保持旧式导入路径
``from packages.research.planning.plan_service import PlanService`` 与
``from packages.research.plan_service import PlanService``（经 sys.modules 别名）
仍可工作。业务逻辑见上述子模块。
"""

from packages.research.planning.plan_core import PlanService

__all__ = ["PlanService"]
