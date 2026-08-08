"""ProductService 基类：依赖注入与共享基础设施。

拆分自 products.py（IRIP 拆分任务）。本模块定义 ``ProductServiceBase``，
承载构造器、操作人校验以及各功能域 Mixin 共享的实例属性声明，
供 ``derived_dataset`` / ``view`` / ``artifact_link`` 等 Mixin 继承，
避免循环导入。

参照 packages/research/service.py 的 ScopedSessionMixin 模式。
"""

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.database import ScopedSessionMixin
from packages.common.errors import AppError


class ProductServiceBase(ScopedSessionMixin):
    """DerivedDataset / ResearchView / Insight 生命周期管理服务基类。

    依赖注入 session_factory / department_id / actor_id / RunArtifactService。

    Attributes:
        _factory: 异步会话工厂。
        _dept_id: 当前部门 ID。
        _actor_id: 当前操作人 ID。
        _artifact_service: RunArtifactService（工件内容读取和下载）。
        _lineage_writer: LineageWriterService 实例（可选，阶段 5 新增）。
        _rls_dept_id: RLS 部门 ID（平台管理员绕过隔离，可选）。
    """

    _factory: async_sessionmaker[AsyncSession]
    _dept_id: UUID
    _actor_id: UUID | None
    _artifact_service: Any
    _lineage_writer: Any | None
    _rls_dept_id: UUID | None

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        department_id: UUID,
        actor_id: UUID | None,
        artifact_service: Any,
        lineage_writer: Any | None = None,
    ) -> None:
        """初始化产物服务。

        Args:
            session_factory: 异步会话工厂。
            department_id: 当前部门 ID。
            actor_id: 当前操作人 ID。
            artifact_service: RunArtifactService 实例。
            lineage_writer: LineageWriterService 实例（可选，阶段 5 新增）。
        """
        self._factory = session_factory
        self._dept_id = department_id
        self._actor_id = actor_id
        self._artifact_service = artifact_service
        self._lineage_writer = lineage_writer
        self._rls_dept_id: UUID | None = None

    def _require_actor(self) -> UUID:
        """获取当前操作人 ID，为空时抛出异常。"""
        if self._actor_id is None:
            raise AppError(
                code="forbidden",
                message="操作需要已认证用户",
                retryable=False,
                fields={},
            )
        return self._actor_id
