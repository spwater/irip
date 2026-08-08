"""研究产物业务编排服务：向后兼容 re-export。

原 products.py（1526 行）已按功能域拆分为：
- product_base.py: ProductServiceBase（依赖注入 + 共享基础设施）；
- derived_dataset.py: DerivedDataset CRUD；
- view.py: ResearchView CRUD；
- artifact_link.py: Insight CRUD + 产物列表；
- product_service.py: ProductService 装配。

本文件仅 re-export ``ProductService``，保持旧式导入路径
``from packages.research.products.products import ProductService`` 仍可工作。
业务逻辑见上述子模块。
"""

from packages.research.products.product_service import ProductService

__all__ = ["ProductService"]
