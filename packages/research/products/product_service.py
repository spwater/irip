"""ProductService 装配模块：组合各功能域 Mixin 为最终 ProductService。

拆分自 products.py（IRIP 拆分任务）。``product_service`` 将
``DerivedDatasetMixin`` / ``ViewMixin`` / ``ArtifactLinkMixin`` 装配为
``ProductService``。

向后兼容：``products.py`` 与 ``packages.research.products.__init__``
均 re-export 本模块的 ``ProductService``，使旧式导入路径
``from packages.research.products import ProductService`` 与
``from packages.research.products.products import ProductService`` 仍可工作。
业务逻辑见各子模块（product_base / derived_dataset / view / artifact_link）。

核心不变量：
1. 版本实体创建后不可变（Repository 不提供 update/delete 方法）；
2. 编辑 API 仅接受 stable identity 元数据字段；
3. 非 publishable 工件不允许创建产物；
4. 所有写操作产生审计记录。
"""

from packages.research.products.artifact_link import ArtifactLinkMixin
from packages.research.products.derived_dataset import DerivedDatasetMixin
from packages.research.products.product_base import ProductServiceBase
from packages.research.products.view import ViewMixin


class ProductService(
    DerivedDatasetMixin,
    ViewMixin,
    ArtifactLinkMixin,
    ProductServiceBase,
):
    """DerivedDataset / ResearchView / Insight 生命周期管理服务。

    由各功能域 Mixin 装配而成：
    - DerivedDatasetMixin: 派生数据集 CRUD；
    - ViewMixin: 视图 CRUD；
    - ArtifactLinkMixin: Insight CRUD + 产物列表。
    """


__all__ = ["ProductService"]
