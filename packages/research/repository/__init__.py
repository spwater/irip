"""研究域数据访问层（子包入口）。

原 ``packages/research/repository.py``（单文件 ~2450 行、单一类
``ResearchRepository``）按功能域拆分为多个子仓库类，各自位于独立模块中。

本 ``__init__.py`` 负责向后兼容：
- 重新导出 ``_encode_cursor`` / ``_decode_cursor``；
- 重新导出各子仓库类（``WorkspaceRepository`` 等），便于直接按域使用；
- 提供 ``ResearchRepository`` 兼容聚合类，通过多重继承聚合全部 @staticmethod
  方法，使既有调用 ``ResearchRepository.get_workspace(session, ...)`` 无需修改。

所有方法均为 @staticmethod async，接受 AsyncSession 参数，不自行管理事务。
事务由 Service 层通过 ScopedSessionMixin 管理。
"""

from packages.research.repository._cursor import _decode_cursor, _encode_cursor
from packages.research.repository.dataset import DatasetRepository
from packages.research.repository.evidence import EvidenceRefRepository, SnapshotRepository
from packages.research.repository.favorite import FavoriteRepository
from packages.research.repository.insight import InsightRepository
from packages.research.repository.knowledge import KnowledgeReferenceRepository
from packages.research.repository.lineage import LineageEdgeRepository
from packages.research.repository.question import QuestionRepository
from packages.research.repository.result import ResultRepository
from packages.research.repository.search import SearchRepository
from packages.research.repository.view import ViewRepository
from packages.research.repository.workspace import WorkspaceRepository

__all__ = [
    "_encode_cursor",
    "_decode_cursor",
    "WorkspaceRepository",
    "QuestionRepository",
    "EvidenceRefRepository",
    "SnapshotRepository",
    "DatasetRepository",
    "ViewRepository",
    "InsightRepository",
    "ResultRepository",
    "LineageEdgeRepository",
    "FavoriteRepository",
    "SearchRepository",
    "KnowledgeReferenceRepository",
    "ResearchRepository",
]


class ResearchRepository(
    WorkspaceRepository,
    QuestionRepository,
    EvidenceRefRepository,
    SnapshotRepository,
    DatasetRepository,
    ViewRepository,
    InsightRepository,
    ResultRepository,
    LineageEdgeRepository,
    FavoriteRepository,
    SearchRepository,
    KnowledgeReferenceRepository,
):
    """向后兼容聚合类 — 方法已拆分到各子仓库类。

    通过多重继承聚合全部 @staticmethod 方法，使既有调用
    ``ResearchRepository.get_workspace(session, ...)`` 无需修改。
    事务仍由 Service 层通过 ScopedSessionMixin 管理。

    各子仓库类方法名互不重叠，故 MRO 可安全线性化；新代码建议直接使用
    对应的子仓库类（如 ``WorkspaceRepository``）以获得更清晰的依赖边界。
    """
