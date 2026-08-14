"""研究域 — 研究产物子包。

包含研究产物的管理、候选、工件服务与目录：
- ProductService: 研究产物服务
- CandidateService: 候选服务
- RunArtifactService: 运行工件服务
- ResearchCatalogImpl: 研究目录
"""

# 向后兼容：re-export ProductService，
# 使 ``from packages.research.products import ProductService`` 仍可工作。
from packages.research.products.product_service import ProductService  # noqa: F401

__all__ = ["ProductService"]
