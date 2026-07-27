"""L2 事实业务编排服务（IRIP Task 15）。

FactService 提供事实的创建、修订、查询、观察值管理、全文搜索与列表功能。

核心不变量：
1. normalized_without_raw: 每个标准化观察值必须引用一个原始观察值。
2. idempotency: 幂等键匹配已有成功事实时返回已有事实（不创建重复）。
3. immutable_revisions: 事实修订一旦创建不可修改，新变更创建新修订。
4. revision_preserves_previous: 旧修订数据在新修订创建后仍可查询。

依赖注入 session_factory（事务管理）、organization_id（当前组织）、
actor_id（操作人）。所有写操作通过 session_scope 事务上下文管理。
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.database import session_scope
from packages.common.errors import AppError
from packages.facts.entities import Fact, FactRevision
from packages.facts.observations import (
    FactRevisionRef,
    NormalizedObservation,
    RawObservation,
)
from packages.facts.repository import FactRepository
from packages.standards.methods import MethodVersion
from packages.standards.objects import IndustrialObject
from packages.standards.templates import FactTemplateVersion, FactType
from packages.standards.variables import VariableVersion

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
        template_version_id: 模板版本 ID。
        organization_id: 组织 ID。
        object_id: 工业对象 ID。
        subject_id: 主体标识。
        started_at: 开始时间。
        ended_at: 结束时间。
        method_version_id: 方法版本 ID（可选）。
        raw: 原始观察值输入元组。
        normalized: 标准化观察值输入元组。
        artifacts: 工件 ID 元组。
        idempotency_key: 幂等键（可选）。
        created_by: 创建人 ID。
    """

    fact_type: Literal[
        "experiment_run", "simulation_run", "document_record", "model_execution"
    ]
    template_version_id: UUID | None
    organization_id: UUID
    object_id: UUID
    subject_id: str
    started_at: datetime
    ended_at: datetime | None
    method_version_id: UUID | None
    raw: tuple
    normalized: tuple
    artifacts: tuple
    idempotency_key: str | None
    created_by: UUID
    # 入库时的任务信息快照
    task_code: str | None = None
    task_name: str | None = None
    department_name: str | None = None
    operator: str | None = None
    flow_run_id: UUID | None = None


@dataclass(frozen=True)
class ReviseFactCommand:
    """修订事实命令。

    Attributes:
        reason: 修订原因。
        subject_id: 新主体标识（None 表示不变）。
        method_version_id: 新方法版本 ID（None 表示不变）。
        started_at: 新开始时间（None 表示不变）。
        ended_at: 新结束时间（None 表示不变）。
        raw: 新原始观察值输入元组（空表示复制上一修订）。
        normalized: 新标准化观察值输入元组（空表示复制上一修订）。
        artifacts: 新工件 ID 元组（空表示复制上一修订）。
    """

    reason: str
    subject_id: str | None = None
    method_version_id: UUID | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    raw: tuple = ()
    normalized: tuple = ()
    artifacts: tuple = ()


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

    async def create(self, command: CreateFactCommand) -> FactRevisionRef:
        """创建事实（revision 1）。

        流程：
        1. 校验 fact_type 合法；
        2. 幂等检查：若 idempotency_key 已存在 → 返回已有事实的最新修订；
        3. 校验 template_version_id 已发布（PUBLISHED）；
        4. 校验 object_id 属于当前组织；
        5. 校验 method_version_id 已发布（如提供）；
        6. 校验所有标准化观察值有 raw_observation_id（非 None）；
        7. 创建 fact 行（status=active, current_revision=1）；
        8. 创建 fact_revision（revision=1）；
        9. 创建 raw_observations；
        10. 创建 normalized_observations（链接到 raw_observation_ids）；
        11. 创建 fact_artifacts；
        12. 返回 FactRevisionRef。

        Args:
            command: 创建事实命令。

        Returns:
            FactRevisionRef: 事实修订引用。

        Raises:
            AppError: code="validation_failed"，当 fact_type 无效时。
            AppError: code="normalized_without_raw"，当标准化观察值缺原始引用时。
            AppError: code="template_not_published"，当模板未发布时。
            AppError: code="method_not_published"，当方法未发布时。
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
                # 返回已有事实的最新修订
                async with self._factory() as session:
                    rev = await FactRepository.get_latest_revision(
                        session, existing.id, command.organization_id
                    )
                return FactRevisionRef(
                    fact_id=existing.id,
                    revision=rev.revision,
                    revision_id=rev.id,
                    fact_type=rev.fact_type,
                    subject_id=rev.subject_id,
                    status=existing.status,
                )

        # 6. 校验标准化观察值有原始引用（在事务前校验，快速失败）
        for norm in command.normalized:
            if norm.raw_observation_id is None:
                raise AppError(
                    code="normalized_without_raw",
                    message="标准化观察值必须引用一个原始观察值",
                    retryable=False,
                    fields={"variable_version_id": str(norm.variable_version_id)},
                )

        async with session_scope(self._factory) as session:
            # 3. 校验模板已发布（可选，template_version_id 为 None 时跳过）
            if command.template_version_id is not None:
                template_version = await session.scalar(
                    sa.select(FactTemplateVersion).where(
                        FactTemplateVersion.id == command.template_version_id
                    )
                )
                if template_version is None or template_version.status != "published":
                    raise AppError(
                        code="template_not_published",
                        message="事实模板版本未发布",
                        retryable=False,
                        fields={
                            "template_version_id": str(command.template_version_id)
                        },
                    )

            # 4. 校验工业对象属于组织
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

            # 5. 校验方法已发布（如提供）
            if command.method_version_id is not None:
                method_version = await session.scalar(
                    sa.select(MethodVersion).where(
                        MethodVersion.id == command.method_version_id
                    )
                )
                if method_version is None or method_version.status != "published":
                    raise AppError(
                        code="method_not_published",
                        message="方法版本未发布",
                        retryable=False,
                        fields={
                            "method_version_id": str(command.method_version_id)
                        },
                    )

            # 7. 创建 fact 行
            fact = await FactRepository.insert_fact(
                session,
                organization_id=command.organization_id,
                template_version_id=command.template_version_id,
                fact_type=command.fact_type,
                object_id=command.object_id,
                current_revision=1,
                status="active",
                idempotency_key=command.idempotency_key,
                created_by=command.created_by,
            )

            # 8. 创建 fact_revision（revision=1）
            rev = await FactRepository.insert_revision(
                session,
                fact_id=fact.id,
                revision=1,
                template_version_id=command.template_version_id,
                fact_type=command.fact_type,
                object_id=command.object_id,
                subject_id=command.subject_id,
                method_version_id=command.method_version_id,
                started_at=command.started_at,
                ended_at=command.ended_at,
                revision_reason=None,
                created_by=command.created_by,
                task_code=command.task_code,
                task_name=command.task_name,
                department_name=command.department_name,
                operator=command.operator,
                flow_run_id=command.flow_run_id,
            )

            # 9. 创建 raw_observations
            raw_dicts: list[dict] = [
                {
                    "id": r.id,
                    "source_path": r.source_path,
                    "source_value": r.source_value,
                    "source_unit": r.source_unit,
                    "source_name": r.source_name,
                    "artifact_id": r.artifact_id,
                }
                for r in command.raw
            ]
            raw_orms = await FactRepository.insert_raw_observations(
                session, rev.id, raw_dicts
            )

            # 构建 raw_input_index → raw_orm_id 映射
            raw_id_by_path: dict[str, UUID] = {}
            for r in raw_orms:
                raw_id_by_path[r.source_path] = r.id

            # 10. 创建 normalized_observations
            norm_dicts: list[dict] = []
            for norm in command.normalized:
                raw_id: UUID
                if norm.raw_observation_id is not None:
                    raw_id = norm.raw_observation_id
                else:
                    raise AppError(
                        code="normalized_without_raw",
                        message="标准化观察值必须引用一个原始观察值",
                        retryable=False,
                        fields={
                            "variable_version_id": str(norm.variable_version_id)
                        },
                    )
                norm_dicts.append(
                    {
                        "variable_version_id": norm.variable_version_id,
                        "raw_observation_id": raw_id,
                        "value": norm.value,
                        "unit": norm.unit,
                    }
                )
            await FactRepository.insert_normalized_observations(
                session, rev.id, norm_dicts
            )

            # 11. 创建 fact_artifacts
            art_dicts: list[dict] = [
                {"artifact_id": aid, "role": "raw_data"}
                for aid in command.artifacts
            ]
            await FactRepository.insert_artifacts(session, rev.id, art_dicts)

            # 12. 返回 FactRevisionRef
            return FactRevisionRef(
                fact_id=fact.id,
                revision=rev.revision,
                revision_id=rev.id,
                fact_type=rev.fact_type,
                subject_id=rev.subject_id,
                status=fact.status,
            )

    async def revise(
        self,
        fact_id: UUID,
        reason: str,
        changes: dict | None = None,
    ) -> FactRevisionRef:
        """修订事实（创建新 revision，旧 revision 不可变）。

        流程：
        1. 获取当前 fact + 最新修订；
        2. 以最新修订数据为基础；
        3. 应用变更（subject_id, method_version_id, started_at, ended_at 等）；
        4. 创建新 fact_revision（revision = latest + 1）；
        5. 更新 fact.current_revision；
        6. 创建 fact_revision_link（新 supersedes 旧）；
        7. 复制/更新 raw 和 normalized 观察值；
        8. 返回 FactRevisionRef。

        Args:
            fact_id: 事实 ID。
            reason: 修订原因。
            changes: 变更字典，支持 subject_id, method_version_id,
                started_at, ended_at, raw, normalized, artifacts。

        Returns:
            FactRevisionRef: 新修订引用。

        Raises:
            AppError: code="not_found"，当事实不存在时。
            AppError: code="normalized_without_raw"，当标准化观察值缺原始引用时。
        """
        changes = changes or {}

        async with session_scope(self._factory) as session:
            # 1. 获取当前 fact + 最新修订
            fact = await FactRepository.get_fact(session, fact_id, self._org_id)
            latest_rev = await FactRepository.get_latest_revision(
                session, fact_id, self._org_id
            )

            # 2-3. 计算新修订字段
            new_revision = latest_rev.revision + 1
            new_subject_id = changes.get("subject_id", latest_rev.subject_id)
            new_method_version_id = changes.get(
                "method_version_id", latest_rev.method_version_id
            )
            new_started_at = changes.get("started_at", latest_rev.started_at)
            new_ended_at = changes.get("ended_at", latest_rev.ended_at)

            # 校验标准化观察值
            if "normalized" in changes:
                for norm in changes["normalized"]:
                    if (
                        hasattr(norm, "raw_observation_id")
                        and norm.raw_observation_id is None
                    ):
                        raise AppError(
                            code="normalized_without_raw",
                            message="标准化观察值必须引用一个原始观察值",
                            retryable=False,
                            fields={},
                        )

            # 4. 创建新 fact_revision
            new_rev = await FactRepository.insert_revision(
                session,
                fact_id=fact_id,
                revision=new_revision,
                template_version_id=latest_rev.template_version_id,
                fact_type=latest_rev.fact_type,
                object_id=latest_rev.object_id,
                subject_id=new_subject_id,
                method_version_id=new_method_version_id,
                started_at=new_started_at,
                ended_at=new_ended_at,
                revision_reason=reason,
                created_by=self._actor_id,
            )

            # 5. 更新 fact.current_revision
            await session.execute(
                sa.update(Fact)
                .values(
                    current_revision=new_revision,
                    updated_at=sa.func.now(),
                    lock_version=Fact.lock_version + 1,
                )
                .where(Fact.id == fact_id)
            )

            # 6. 创建 fact_revision_link（新 supersedes 旧）
            await FactRepository.insert_revision_link(
                session,
                from_revision_id=new_rev.id,
                to_revision_id=latest_rev.id,
                link_type="supersedes",
            )

            # 7. 复制/更新观察值
            if "raw" in changes and changes["raw"]:
                raw_dicts = [
                    {
                        "id": r.id,
                        "source_path": r.source_path,
                        "source_value": r.source_value,
                        "source_unit": r.source_unit,
                        "source_name": r.source_name,
                        "artifact_id": r.artifact_id,
                    }
                    for r in changes["raw"]
                ]
                raw_orms = await FactRepository.insert_raw_observations(
                    session, new_rev.id, raw_dicts
                )
                raw_id_by_path = {r.source_path: r.id for r in raw_orms}
            else:
                # 复制上一修订的原始观察值
                old_raws = await FactRepository.get_raw_observations(
                    session, latest_rev.id
                )
                raw_dicts = [
                    {
                        "source_path": r.source_path,
                        "source_value": r.source_value,
                        "source_unit": r.source_unit,
                        "source_name": r.source_name,
                        "artifact_id": r.artifact_id,
                    }
                    for r in old_raws
                ]
                raw_orms = await FactRepository.insert_raw_observations(
                    session, new_rev.id, raw_dicts
                )
                raw_id_by_path = {r.source_path: r.id for r in raw_orms}

            if "normalized" in changes and changes["normalized"]:
                norm_dicts = []
                for norm in changes["normalized"]:
                    raw_id = norm.raw_observation_id
                    if raw_id is None:
                        raise AppError(
                            code="normalized_without_raw",
                            message="标准化观察值必须引用一个原始观察值",
                            retryable=False,
                            fields={},
                        )
                    norm_dicts.append(
                        {
                            "variable_version_id": norm.variable_version_id,
                            "raw_observation_id": raw_id,
                            "value": norm.value,
                            "unit": norm.unit,
                        }
                    )
                await FactRepository.insert_normalized_observations(
                    session, new_rev.id, norm_dicts
                )
            else:
                # 复制上一修订的标准化观察值，映射到新的 raw id
                old_norms = await FactRepository.get_normalized_observations(
                    session, latest_rev.id
                )
                # 需要将 old raw_observation_id 映射到 new raw id
                old_raws = await FactRepository.get_raw_observations(
                    session, latest_rev.id
                )
                old_raw_id_to_path = {
                    r.id: r.source_path for r in old_raws
                }
                norm_dicts = []
                for norm in old_norms:
                    path = old_raw_id_to_path.get(norm.raw_observation_id, "")
                    new_raw_id = raw_id_by_path.get(path)
                    if new_raw_id is None:
                        # 如果找不到映射，跳过
                        continue
                    norm_dicts.append(
                        {
                            "variable_version_id": norm.variable_version_id,
                            "raw_observation_id": new_raw_id,
                            "value": norm.value,
                            "unit": norm.unit,
                        }
                    )
                await FactRepository.insert_normalized_observations(
                    session, new_rev.id, norm_dicts
                )

            # 复制/更新工件链接
            if "artifacts" in changes and changes["artifacts"]:
                art_dicts = [
                    {"artifact_id": aid, "role": "raw_data"}
                    for aid in changes["artifacts"]
                ]
                await FactRepository.insert_artifacts(
                    session, new_rev.id, art_dicts
                )
            else:
                # 复制上一修订的工件链接
                old_arts = await FactRepository.get_artifacts(
                    session, latest_rev.id
                )
                art_dicts = [
                    {"artifact_id": a.artifact_id, "role": a.role}
                    for a in old_arts
                ]
                await FactRepository.insert_artifacts(
                    session, new_rev.id, art_dicts
                )

            # 8. 返回 FactRevisionRef
            return FactRevisionRef(
                fact_id=fact_id,
                revision=new_rev.revision,
                revision_id=new_rev.id,
                fact_type=new_rev.fact_type,
                subject_id=new_rev.subject_id,
                status=fact.status,
            )

    async def get(
        self, fact_id: UUID, revision: int | None = None
    ) -> FactRevisionRef:
        """获取事实（指定 revision 或最新）。

        Args:
            fact_id: 事实 ID。
            revision: 修订号（None 表示最新）。

        Returns:
            FactRevisionRef: 事实修订引用。

        Raises:
            AppError: code="not_found"，当事实或修订不存在时。
        """
        async with self._factory() as session:
            fact = await FactRepository.get_fact(session, fact_id, self._org_id)
            if revision is not None:
                rev = await FactRepository.get_revision(
                    session, fact_id, revision, self._org_id
                )
            else:
                rev = await FactRepository.get_latest_revision(
                    session, fact_id, self._org_id
                )
            return FactRevisionRef(
                fact_id=fact_id,
                revision=rev.revision,
                revision_id=rev.id,
                fact_type=rev.fact_type,
                subject_id=rev.subject_id,
                status=fact.status,
            )

    async def list_revisions(self, fact_id: UUID) -> list[FactRevisionRef]:
        """列出事实的所有修订历史。

        Args:
            fact_id: 事实 ID。

        Returns:
            list[FactRevisionRef]: 修订引用列表（按修订号升序）。

        Raises:
            AppError: code="not_found"，当事实不存在时。
        """
        async with self._factory() as session:
            fact = await FactRepository.get_fact(session, fact_id, self._org_id)
            revs = await FactRepository.get_revisions(
                session, fact_id, self._org_id
            )
            return [
                FactRevisionRef(
                    fact_id=fact_id,
                    revision=rev.revision,
                    revision_id=rev.id,
                    fact_type=rev.fact_type,
                    subject_id=rev.subject_id,
                    status=fact.status,
                )
                for rev in revs
            ]

    async def get_observations(
        self, fact_id: UUID, revision: int | None = None
    ) -> tuple[tuple[RawObservation, ...], tuple[NormalizedObservation, ...]]:
        """获取观察值（原始 + 标准化）。

        Args:
            fact_id: 事实 ID。
            revision: 修订号（None 表示最新）。

        Returns:
            tuple[tuple[RawObservation, ...], tuple[NormalizedObservation, ...]]:
            (原始观察值元组, 标准化观察值元组)。

        Raises:
            AppError: code="not_found"，当事实或修订不存在时。
        """
        async with self._factory() as session:
            if revision is not None:
                rev = await FactRepository.get_revision(
                    session, fact_id, revision, self._org_id
                )
            else:
                rev = await FactRepository.get_latest_revision(
                    session, fact_id, self._org_id
                )

            raw_orms = await FactRepository.get_raw_observations(
                session, rev.id
            )
            norm_orms = await FactRepository.get_normalized_observations(
                session, rev.id
            )

            raws = tuple(
                RawObservation(
                    id=r.id,
                    fact_revision_id=r.fact_revision_id,
                    source_path=r.source_path,
                    source_value=r.source_value,
                    source_unit=r.source_unit,
                    source_name=r.source_name,
                    artifact_id=r.artifact_id,
                )
                for r in raw_orms
            )
            norms = tuple(
                NormalizedObservation(
                    id=n.id,
                    fact_revision_id=n.fact_revision_id,
                    variable_version_id=n.variable_version_id,
                    raw_observation_id=n.raw_observation_id,
                    value=n.value,
                    unit=n.unit,
                )
                for n in norm_orms
            )
            return raws, norms

    async def search(
        self,
        query: str,
        filters: dict | None = None,
        cursor: str | None = None,
        page_size: int = 20,
    ) -> tuple[list[FactRevisionRef], str | None]:
        """全文搜索事实（使用 PostgreSQL tsvector）。

        Args:
            query: 搜索查询字符串。
            filters: 过滤条件字典（fact_type, object_id, status）。
            cursor: 分页游标。
            page_size: 每页数量。

        Returns:
            tuple[list[FactRevisionRef], str | None]:
            (修订引用列表, 下一页游标)。
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
                FactRevisionRef(
                    fact_id=item["fact_id"],
                    revision=item["revision"],
                    revision_id=item["revision_id"],
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
    ) -> tuple[list[FactRevisionRef], str | None]:
        """分页列出事实（按 fact_type, object_id, status 等过滤）。

        Args:
            filters: 过滤条件字典。
            cursor: 分页游标。
            page_size: 每页数量。

        Returns:
            tuple[list[FactRevisionRef], str | None]:
            (修订引用列表, 下一页游标)。
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
                FactRevisionRef(
                    fact_id=item["fact_id"],
                    revision=item["revision"],
                    revision_id=item["revision_id"],
                    fact_type=item["fact_type"],
                    subject_id=item["subject_id"],
                    status=item["status"],
                )
                for item in items
            ]
            return refs, next_cursor
