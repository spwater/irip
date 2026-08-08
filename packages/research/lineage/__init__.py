"""研究域 — 统一溯源子包。

包含统一溯源、溯源边、溯源写入、标签与核心适配：
- UnifiedProvenanceQueryService: 统一溯源查询服务
- LineageEdgeService: 溯源边服务
- LineageWriterService: 溯源写入服务
- NodeDisplayLabelGenerator: 节点标签生成器
- CoreFactProviderImpl: 核心事实提供者实现
"""

# 向后兼容：re-export LineageEdgeService，
# 使 ``from packages.research.lineage import LineageEdgeService`` 仍可工作。
from packages.research.lineage.lineage import LineageEdgeService  # noqa: F401

__all__ = ["LineageEdgeService"]
