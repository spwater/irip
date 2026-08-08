"""研究域 — 成果发布子包。

包含成果发布、搜索、知识引用与知识提供：
- PublicationService: 成果发布服务
- ResultSearchService: 结果搜索服务
- KnowledgeReferenceService: 知识引用服务
- KnowledgeProviderService: 知识提供服务
"""

# re-export PublicationService，使 ``from packages.research.publication import
# PublicationService`` 可工作。实现已按功能域拆分到 publisher / acl /
# revision / reuse / _base，publication.py 仅保留兼容 re-export。
from packages.research.publication.publisher import PublicationService  # noqa: F401

__all__ = ["PublicationService"]
