"""L2 事实业务编排服务。

FactService 提供事实的创建、查询、全文搜索与列表功能。

核心不变量：
1. idempotency: 幂等键匹配已有成功事实时返回已有事实（不创建重复）。

依赖注入 session_factory（事务管理）、organization_id（当前组织）、
actor_id（操作人）。所有写操作通过 session_scope 事务上下文管理。
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.database import session_scope
from packages.common.errors import AppError
from packages.facts.observations import FactRef
from packages.facts.repository import FactRepository
from packages.standards.objects import IndustrialObject


class FactType(StrEnum):
    """事实类型枚举。

    原 packages.standards.templates.templates.FactType 已随标准层空表
    一并删除（migration 0057），枚举迁移至本模块以保持 fact_type 取值不变。

    Attributes:
        EXPERIMENT_RUN: 实验运行。
        SIMULATION_RUN: 仿真运行。
        DOCUMENT_RECORD: 文档记录。
        MODEL_EXECUTION: 模型执行。
    """

    EXPERIMENT_RUN = "experiment_run"
    SIMULATION_RUN = "simulation_run"
    DOCUMENT_RECORD = "document_record"
    MODEL_EXECUTION = "model_execution"


#: 合法事实类型集合。
_VALID_FACT_TYPES: frozenset[str] = frozenset(
    {
        FactType.EXPERIMENT_RUN.value,
        FactType.SIMULATION_RUN.value,
        FactType.DOCUMENT_RECORD.value,
        FactType.MODEL_EXECUTION.value,
    }
)


@dataclass(frozen=True)
class CreateFactCommand:
    """创建事实命令。

    Attributes:
        fact_type: 事实类型。
        organization_id: 组织 ID。
        object_id: 工业对象 ID。
        subject_id: 主体标识。
        started_at: 开始时间。
        ended_at: 结束时间。
        idempotency_key: 幂等键（可选）。
        created_by: 创建人 ID。
        task_code: 任务编码快照（可选）。
        task_name: 任务名称快照（可选）。
        department_name: 部门名称快照（可选）。
        operator: 操作人快照（可选）。
        run_operator: 运行操作人快照（可选）。
        equipment_name: 设备名快照（可选）。
        flow_run_id: 流程运行 ID（可选）。
    """

    fact_type: Literal["experiment_run", "simulation_run", "document_record", "model_execution"]
    organization_id: UUID
    object_id: UUID
    subject_id: str
    started_at: datetime | None
    ended_at: datetime | None
    idempotency_key: str | None
    created_by: UUID | None
    task_code: str | None = None
    task_name: str | None = None
    department_name: str | None = None
    operator: str | None = None
    run_operator: str | None = None
    equipment_name: str | None = None
    flow_run_id: UUID | None = None
    source_artifact_id: UUID | None = None


class FactService:
    """事实业务编排服务。

    依赖注入 session_factory（事务管理）、organization_id（当前组织）、
    actor_id（操作人）。

    Attributes:
        _factory: 异步会话工厂。
        _org_id: 当前组织 ID。
        _actor_id: 当前操作人 ID（用于 created_by）。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        organization_id: UUID,
        actor_id: UUID | None = None,
    ) -> None:
        """初始化事实服务。

        Args:
            session_factory: 异步会话工厂。
            organization_id: 当前组织 ID。
            actor_id: 当前操作人 ID（可选，用于 created_by）。
        """
        self._factory = session_factory
        self._org_id = organization_id
        self._actor_id = actor_id

    # ---- 公开只读属性（替代路由直接访问私有属性） ----

    @property
    def organization_id(self) -> UUID:
        """当前组织 ID（公开只读访问，替代 ``service._org_id``）。"""
        return self._org_id

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        """异步会话工厂（公开只读访问，替代 ``service._factory``）。"""
        return self._factory

    async def create(self, command: CreateFactCommand) -> FactRef:
        """创建事实。

        流程：
        1. 校验 fact_type 合法；
        2. 幂等检查：若 idempotency_key 已存在 → 返回已有事实；
        3. 校验 object_id 属于当前组织；
        4. 创建 fact 行（含合并字段，status=active）；
        5. 返回 FactRef。

        Args:
            command: 创建事实命令。

        Returns:
            FactRef: 事实引用。

        Raises:
            AppError: code="validation_failed"，当 fact_type 无效时。
            AppError: code="not_found"，当工业对象不属于当前组织时。
        """
        # 1. 校验 fact_type
        if command.fact_type not in _VALID_FACT_TYPES:
            raise AppError(
                code="validation_failed",
                message=f"无效的事实类型: {command.fact_type}",
                retryable=False,
                fields={"fact_type": command.fact_type},
            )

        # 2. 幂等检查
        if command.idempotency_key is not None:
            async with self._factory() as session:
                existing = await FactRepository.find_by_idempotency_key(
                    session, command.organization_id, command.idempotency_key
                )
            if existing is not None:
                return FactRef(
                    fact_id=existing.id,
                    fact_type=existing.fact_type,
                    subject_id=existing.subject_id,
                    status=existing.status,
                )

        async with session_scope(self._factory) as session:
            # 3. 校验工业对象属于组织
            obj = await session.scalar(
                sa.select(IndustrialObject).where(
                    IndustrialObject.id == command.object_id,
                    IndustrialObject.organization_id == command.organization_id,
                )
            )
            if obj is None:
                raise AppError(
                    code="not_found",
                    message="工业对象不存在或不属于当前组织",
                    retryable=False,
                    fields={"object_id": str(command.object_id)},
                )

            # 4. 创建 fact 行
            fact = await FactRepository.insert_fact(
                session,
                organization_id=command.organization_id,
                fact_type=command.fact_type,
                object_id=command.object_id,
                status="active",
                idempotency_key=command.idempotency_key,
                created_by=command.created_by,
                subject_id=command.subject_id,
                flow_run_id=command.flow_run_id,
                started_at=command.started_at,
                ended_at=command.ended_at,
                task_code=command.task_code,
                task_name=command.task_name,
                department_name=command.department_name,
                operator=command.operator,
                run_operator=command.run_operator,
                equipment_name=command.equipment_name,
                source_artifact_id=command.source_artifact_id,
            )

            # 5. 返回 FactRef
            return FactRef(
                fact_id=fact.id,
                fact_type=fact.fact_type,
                subject_id=fact.subject_id,
                status=fact.status,
            )

    async def get(self, fact_id: UUID) -> FactRef:
        """获取事实。

        Args:
            fact_id: 事实 ID。

        Returns:
            FactRef: 事实引用。

        Raises:
            AppError: code="not_found"，当事实不存在时。
        """
        async with self._factory() as session:
            fact = await FactRepository.get_fact(session, fact_id, self._org_id)
            return FactRef(
                fact_id=fact_id,
                fact_type=fact.fact_type,
                subject_id=fact.subject_id,
                status=fact.status,
            )

    async def search(
        self,
        query: str,
        filters: dict | None = None,
        cursor: str | None = None,
        page_size: int = 20,
    ) -> tuple[list[FactRef], str | None]:
        """全文搜索事实（使用 PostgreSQL tsvector）。

        Args:
            query: 搜索查询字符串。
            filters: 过滤条件字典（fact_type, object_id, status）。
            cursor: 分页游标。
            page_size: 每页数量。

        Returns:
            tuple[list[FactRef], str | None]:
            (事实引用列表, 下一页游标)。
        """
        async with self._factory() as session:
            items, next_cursor = await FactRepository.search_facts(
                session,
                query=query,
                org_id=self._org_id,
                filters=filters,
                cursor=cursor,
                page_size=page_size,
            )
            refs = [
                FactRef(
                    fact_id=item["fact_id"],
                    fact_type=item["fact_type"],
                    subject_id=item["subject_id"],
                    status=item["status"],
                )
                for item in items
            ]
            return refs, next_cursor

    async def list_facts(
        self,
        filters: dict | None = None,
        cursor: str | None = None,
        page_size: int = 20,
    ) -> tuple[list[FactRef], str | None]:
        """分页列出事实（按 fact_type, object_id, status 等过滤）。

        Args:
            filters: 过滤条件字典。
            cursor: 分页游标。
            page_size: 每页数量。

        Returns:
            tuple[list[FactRef], str | None]:
            (事实引用列表, 下一页游标)。
        """
        async with self._factory() as session:
            items, next_cursor = await FactRepository.list_facts(
                session,
                org_id=self._org_id,
                filters=filters,
                cursor=cursor,
                page_size=page_size,
            )
            refs = [
                FactRef(
                    fact_id=item["fact_id"],
                    fact_type=item["fact_type"],
                    subject_id=item["subject_id"],
                    status=item["status"],
                )
                for item in items
            ]
            return refs, next_cursor
