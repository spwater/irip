"""研究域包初始化。

研究域（Research Domain）负责科研分析工作空间的创建、证据引用管理、
证据快照冻结等功能。通过 CoreFactProvider 只读适配接口访问核心事实数据，
不直接导入 packages/facts 的内部模块。

拆包说明（H-02 / packages.research 拆分）：
原 34 个平铺 .py 文件按子域拆分为 5 个子包——
``planning``（分析计划）、``execution``（可信执行）、``products``（研究产物）、
``publication``（成果发布）、``lineage``（统一溯源）；
共享/基础设施模块（models / entities / repository / service /
conversation_service / memory_service / snapshots）保留在根目录。

向后兼容：下列 ``_MOVED_MODULES`` 中的旧路径在运行时通过 sys.modules 别名
重定向到新路径，使 ``from packages.research.validation import X`` 等
旧式导入仍可工作。碰撞模块（products / lineage / publication）由各自子包
``__init__.py`` 的 re-export 处理，不在此列。
"""

from __future__ import annotations

import importlib
import sys

# 旧模块路径 → 新子包路径（碰撞模块 products/lineage/publication 不在此列，
# 它们由子包 __init__.py re-export 处理）。
_MOVED_MODULES: dict[str, str] = {
    # ---- planning ----
    "packages.research.plan_service": "packages.research.planning.plan_service",
    "packages.research.context_router": "packages.research.planning.context_router",
    "packages.research.model_gateway": "packages.research.planning.model_gateway",
    # ---- execution ----
    "packages.research.orchestrator": "packages.research.execution.orchestrator",
    "packages.research.run_service": "packages.research.execution.run_service",
    "packages.research.sandbox": "packages.research.execution.sandbox",
    "packages.research.scheduler": "packages.research.execution.scheduler",
    "packages.research.repository_trusted": "packages.research.execution.repository_trusted",
    "packages.research.entities_trusted": "packages.research.execution.entities_trusted",
    "packages.research.models_trusted": "packages.research.execution.models_trusted",
    "packages.research.validation": "packages.research.execution.validation",
    "packages.research.envelope": "packages.research.execution.envelope",
    # ---- products（不含 products 自身——碰撞）----
    "packages.research.candidates": "packages.research.products.candidates",
    "packages.research.insight_extractor": "packages.research.products.insight_extractor",
    "packages.research.artifact_service": "packages.research.products.artifact_service",
    "packages.research.catalog": "packages.research.products.catalog",
    # ---- publication（不含 publication 自身——碰撞）----
    "packages.research.search": "packages.research.publication.search",
    "packages.research.knowledge_reference": "packages.research.publication.knowledge_reference",
    "packages.research.knowledge_provider": "packages.research.publication.knowledge_provider",
    # ---- lineage（不含 lineage 自身——碰撞）----
    "packages.research.provenance": "packages.research.lineage.provenance",
    "packages.research.lineage_writer": "packages.research.lineage.lineage_writer",
    "packages.research.labels": "packages.research.lineage.labels",
    "packages.research.core_adapter": "packages.research.lineage.core_adapter",
}

# 预导入所有被移动的模块并注册旧路径别名到 sys.modules，
# 使旧式导入 ``from packages.research.validation import X`` 仍可工作。
# 这必须在包初始化时完成，以避免 SQLAlchemy ORM 类被重复定义。
for _old_name, _new_name in _MOVED_MODULES.items():
    if _old_name not in sys.modules:
        sys.modules[_old_name] = importlib.import_module(_new_name)
