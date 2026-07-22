"""L3 参数业务编排服务（IRIP Task 18）。

ParameterService 提供参数的创建、候选管理、审批（含职责分离）、
发布、弃用与过期检查。

核心不变量：
1. self_approval_forbidden: 提交人不能审批自己的候选；
2. derivation_not_succeeded: 推导运行未成功时不能审批候选；
3. published_version_immutable: 已发布的参数版本不可修改；
4. staleness: 事实产生新修订时，依赖参数变为 review_required。

依赖注入 session_factory（事务管理）、organization_id（当前组织）、
actor_id（操作人）。所有写操作通过 session_scope 事务上下文管理。
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.database import session_scope
from packages.common.errors import AppError
from packages.common.ids import new_id
from packages.facts.entities import Fact, FactRevision
from packages.parameters.entities import (
    Parameter,
    ParameterCandidate,
    ParameterStaleness,
    ParameterVersion,
)
from packages.parameters.staleness import StalenessChecker
from packages.provenance.entities import (
    DerivationRun,
    EvidenceSetVersion,
    ProvenanceEdge,
)


class ParameterStatus(StrEnum):
    """参数状态枚举。"""

    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    PUBLISHED = "published"
    REJECTED = "rejected"
    EXPIRED = "expired"
    DEPRECATED = "deprecated"


class ParameterReviewState(StrEnum):
    """参数审核/过期状态枚举。"""

    CURRENT = "current"
    REVIEW_REQUIRED = "review_required"


@dataclass(frozen=True)
class ParameterVersionRef:
    """参数版本引用（不可变值对象）。

    Attributes:
        parameter_id: 参数 UUID。
        version: 版本号。
        version_id: 版本 UUID。
        variable_code: 变量代码。
        value: 参数值（字符串形式）。
        unit: 单位（可选）。
        confidence: 置信度（字符串形式，可选）。
        status: 版本状态（published / deprecated）。
        conditions: 条件 AST（可选）。
        published_at: 发布时间。
    """

    parameter_id: UUID
    version: int
    version_id: UUID
    variable_code: str
    value: str
    unit: str | None
    confidence: str | None
    status: str
    conditions: dict | None
    published_at: datetime | None


class ParameterService:
    """参数业务编排服务。

    依赖注入 session_factory（事务管理）、organization_id（当前组织）、
    actor_id（操作人）。

    Attributes:
        _factory: 异步会话工厂。
        _org_id: 当前组织 ID。
        _actor_id: 当前操作人 ID（用于 created_by / submitted_by）。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        organization_id: UUID,
        actor_id: UUID | None = None,
    ) -> None:
        """初始化参数服务。

        Args:
            session_factory: 异步会话工厂。
            organization_id: 当前组织 ID。
            actor_id: 当前操作人 ID（可选，用于 created_by / submitted_by）。
        """
        self._factory = session_factory
        self._org_id = organization_id
        self._actor_id = actor_id

    async def create_parameter(
        self,
        variable_code: str,
        object_id: UUID,
    ) -> dict:
        """创建参数（draft 状态）。

        Args:
            variable_code: 变量代码（关联标准变量）。
            object_id: 工业对象 ID。

        Returns:
            dict: 包含 parameter_id, variable_code, object_id, status。

        Raises:
            AppError: code="validation_failed"，当 variable_code 为空时。
            AppError: code="conflict"，当参数已存在时。
        """
        if not variable_code or not variable_code.strip():
            raise AppError(
                code="validation_failed",
                message="变量代码不能为空",
                retryable=False,
                fields={"variable_code": "required"},
            )

        async with session_scope(self._factory) as session:
            # 检查唯一性
            existing = await session.scalar(
                sa.select(Parameter).where(
                    Parameter.organization_id == self._org_id,
                    Parameter.variable_code == variable_code.strip(),
                    Parameter.object_id == object_id,
                )
            )
            if existing is not None:
                raise AppError(
                    code="conflict",
                    message=(
                        f"参数已存在: variable_code={variable_code}, "
                        f"object_id={object_id}"
                    ),
                    retryable=False,
                    fields={
                        "variable_code": variable_code,
                        "object_id": str(object_id),
                    },
                )

            param = Parameter(
                id=new_id(),
                organization_id=self._org_id,
                variable_code=variable_code.strip(),
                object_id=object_id,
                status=ParameterStatus.DRAFT.value,
                lock_version=0,
                created_by=self._actor_id,
            )
            session.add(param)
            await session.flush()

            return {
                "parameter_id": param.id,
                "variable_code": param.variable_code,
                "object_id": param.object_id,
                "status": param.status,
            }

    async def create_candidate(
        self,
        parameter_id: UUID,
        derivation_run_id: UUID,
        value: str,
        unit: str | None,
        confidence: str | None,
        conditions: dict | None = None,
    ) -> dict:
        """创建参数候选（pending_review 状态）。

        流程：
        1. 验证推导运行状态为 succeeded；
        2. 验证参数存在且属于当前组织；
        3. 创建 parameter_candidate 行（status=pending_review）；
        4. 返回候选信息。

        Args:
            parameter_id: 参数 UUID。
            derivation_run_id: 推导运行 UUID。
            value: 候选值（字符串形式）。
            unit: 单位（可选）。
            confidence: 置信度（字符串形式，可选）。
            conditions: 条件 AST（可选）。

        Returns:
            dict: 包含 candidate_id, parameter_id, derivation_run_id,
            status。

        Raises:
            AppError: code="not_found"，当参数或推导运行不存在时。
            AppError: code="derivation_not_succeeded"，当推导运行未成功时。
            AppError: code="conflict"，当候选已存在时。
        """
        async with session_scope(self._factory) as session:
            # 1. 验证推导运行
            run = await session.scalar(
                sa.select(DerivationRun).where(
                    DerivationRun.id == derivation_run_id,
                    DerivationRun.organization_id == self._org_id,
                )
            )
            if run is None:
                raise AppError(
                    code="not_found",
                    message=f"推导运行不存在: {derivation_run_id}",
                    retryable=False,
                    fields={"derivation_run_id": str(derivation_run_id)},
                )

            if run.status != "succeeded":
                raise AppError(
                    code="derivation_not_succeeded",
                    message=(
                        f"推导运行未成功，当前状态: {run.status}，"
                        f"无法创建参数候选"
                    ),
                    retryable=False,
                    fields={
                        "derivation_run_id": str(derivation_run_id),
                        "status": run.status,
                    },
                )

            # 2. 验证参数存在
            param = await session.scalar(
                sa.select(Parameter).where(
                    Parameter.id == parameter_id,
                    Parameter.organization_id == self._org_id,
                )
            )
            if param is None:
                raise AppError(
                    code="not_found",
                    message=f"参数不存在: {parameter_id}",
                    retryable=False,
                    fields={"parameter_id": str(parameter_id)},
                )

            # 3. 检查唯一约束（每参数每推导一个候选）
            existing_candidate = await session.scalar(
                sa.select(ParameterCandidate).where(
                    ParameterCandidate.parameter_id == parameter_id,
                    ParameterCandidate.derivation_run_id
                    == derivation_run_id,
                )
            )
            if existing_candidate is not None:
                raise AppError(
                    code="conflict",
                    message=(
                        "该参数在此推导运行中已有候选: "
                        f"parameter_id={parameter_id}, "
                        f"derivation_run_id={derivation_run_id}"
                    ),
                    retryable=False,
                    fields={
                        "parameter_id": str(parameter_id),
                        "derivation_run_id": str(derivation_run_id),
                    },
                )

            # 4. 创建候选
            candidate = ParameterCandidate(
                id=new_id(),
                parameter_id=parameter_id,
                derivation_run_id=derivation_run_id,
                value=value,
                unit=unit,
                confidence=confidence,
                confidence_interval=None,
                conditions=conditions,
                status="pending_review",
                submitted_by=self._actor_id,
                submitted_at=datetime.now(UTC),
            )
            session.add(candidate)

            # 更新参数状态为 pending_review
            await session.execute(
                sa.update(Parameter)
                .values(
                    status=ParameterStatus.PENDING_REVIEW.value,
                    updated_at=sa.func.now(),
                    lock_version=Parameter.lock_version + 1,
                )
                .where(Parameter.id == parameter_id)
            )

            await session.flush()

            return {
                "candidate_id": candidate.id,
                "parameter_id": parameter_id,
                "derivation_run_id": derivation_run_id,
                "value": value,
                "unit": unit,
                "confidence": confidence,
                "status": candidate.status,
            }

    async def submit_for_review(self, candidate_id: UUID) -> dict:
        """提交候选审核（pending_review 状态）。

        候选在创建时即为 pending_review 状态，此方法确认并返回候选信息。

        Args:
            candidate_id: 候选 UUID。

        Returns:
            dict: 候选信息。

        Raises:
            AppError: code="not_found"，当候选不存在时。
        """
        async with self._factory() as session:
            candidate = await session.scalar(
                sa.select(ParameterCandidate).where(
                    ParameterCandidate.id == candidate_id
                )
            )
            if candidate is None:
                raise AppError(
                    code="not_found",
                    message=f"参数候选不存在: {candidate_id}",
                    retryable=False,
                    fields={"candidate_id": str(candidate_id)},
                )

            return {
                "candidate_id": candidate.id,
                "parameter_id": candidate.parameter_id,
                "derivation_run_id": candidate.derivation_run_id,
                "value": candidate.value,
                "unit": candidate.unit,
                "confidence": candidate.confidence,
                "status": candidate.status,
            }

    async def approve(
        self, candidate_id: UUID, reviewer: UUID
    ) -> ParameterVersionRef:
        """审批通过候选，创建不可变参数版本。

        流程：
        1. 验证候选状态为 pending_review；
        2. 职责分离：验证 reviewer != candidate.submitted_by；
        3. 验证推导运行状态为 succeeded；
        4. 创建不可变 parameter_version（status=published）；
        5. 更新 parameter.status = published；
        6. 更新候选状态为 approved；
        7. 创建溯源边：parameter_version → published_as → derivation_run；
        8. 为证据集中所有事实修订创建 staleness 跟踪条目；
        9. 返回 ParameterVersionRef。

        Args:
            candidate_id: 候选 UUID。
            reviewer: 审核人 UUID。

        Returns:
            ParameterVersionRef: 参数版本引用。

        Raises:
            AppError: code="not_found"，当候选或推导运行不存在时。
            AppError: code="candidate_not_pending"，当候选非 pending_review。
            AppError: code="self_approval_forbidden"，当审核人==提交人。
            AppError: code="derivation_not_succeeded"，当推导运行未成功。
        """
        async with session_scope(self._factory) as session:
            # 1. 加载候选
            candidate = await session.scalar(
                sa.select(ParameterCandidate).where(
                    ParameterCandidate.id == candidate_id
                )
            )
            if candidate is None:
                raise AppError(
                    code="not_found",
                    message=f"参数候选不存在: {candidate_id}",
                    retryable=False,
                    fields={"candidate_id": str(candidate_id)},
                )

            if candidate.status != "pending_review":
                raise AppError(
                    code="candidate_not_pending",
                    message=(
                        f"候选不在 pending_review 状态，"
                        f"当前状态: {candidate.status}"
                    ),
                    retryable=False,
                    fields={
                        "candidate_id": str(candidate_id),
                        "status": candidate.status,
                    },
                )

            # 2. 职责分离：提交人不能审批自己的候选
            if candidate.submitted_by == reviewer:
                raise AppError(
                    code="self_approval_forbidden",
                    message="提交人不能审批自己提交的参数候选",
                    retryable=False,
                    fields={
                        "candidate_id": str(candidate_id),
                        "reviewer": str(reviewer),
                    },
                )

            # 3. 验证推导运行状态
            run = await session.scalar(
                sa.select(DerivationRun).where(
                    DerivationRun.id == candidate.derivation_run_id,
                    DerivationRun.organization_id == self._org_id,
                )
            )
            if run is None:
                raise AppError(
                    code="not_found",
                    message=(
                        f"推导运行不存在: {candidate.derivation_run_id}"
                    ),
                    retryable=False,
                    fields={
                        "derivation_run_id": str(
                            candidate.derivation_run_id
                        )
                    },
                )

            if run.status != "succeeded":
                raise AppError(
                    code="derivation_not_succeeded",
                    message=(
                        f"推导运行未成功，当前状态: {run.status}，"
                        f"无法审批参数候选"
                    ),
                    retryable=False,
                    fields={
                        "derivation_run_id": str(run.id),
                        "status": run.status,
                    },
                )

            # 4. 计算新版本号
            max_version_result = await session.execute(
                sa.select(sa.func.max(ParameterVersion.version)).where(
                    ParameterVersion.parameter_id == candidate.parameter_id
                )
            )
            max_version: int | None = max_version_result.scalar()
            new_version: int = (max_version or 0) + 1

            # 5. 创建不可变参数版本
            version_id: UUID = new_id()
            now: datetime = datetime.now(UTC)
            param_version = ParameterVersion(
                id=version_id,
                parameter_id=candidate.parameter_id,
                version=new_version,
                value=candidate.value,
                unit=candidate.unit,
                confidence=candidate.confidence,
                confidence_interval=candidate.confidence_interval,
                conditions=candidate.conditions,
                derivation_run_id=candidate.derivation_run_id,
                evidence_set_version_id=run.evidence_set_version_id,
                recipe_version_id=run.recipe_version_id,
                status="published",
                published_at=now,
                published_by=reviewer,
                lock_version=0,
            )
            session.add(param_version)

            # 6. 更新参数状态为 published
            await session.execute(
                sa.update(Parameter)
                .values(
                    status=ParameterStatus.PUBLISHED.value,
                    updated_at=sa.func.now(),
                    lock_version=Parameter.lock_version + 1,
                )
                .where(Parameter.id == candidate.parameter_id)
            )

            # 7. 更新候选状态为 approved
            candidate.status = "approved"
            candidate.reviewed_by = reviewer
            candidate.reviewed_at = now
            candidate.review_decision = "approved"

            # 8. 创建溯源边：parameter_version → published_as → derivation_run
            edge = ProvenanceEdge(
                id=new_id(),
                organization_id=self._org_id,
                derivation_run_id=candidate.derivation_run_id,
                source_type="parameter_version",
                source_id=version_id,
                target_type="derivation_run",
                target_id=candidate.derivation_run_id,
                edge_type="published_as",
                metadata_=None,
            )
            session.add(edge)

            # 9. 为证据集中所有事实修订创建 staleness 跟踪条目
            ev_version = await session.scalar(
                sa.select(EvidenceSetVersion).where(
                    EvidenceSetVersion.id
                    == run.evidence_set_version_id
                )
            )
            if ev_version is not None:
                members_list: list = ev_version.members or []
                for member in members_list:
                    if (
                        member.get("decision") == "included"
                        and member.get("fact_revision_id")
                    ):
                        staleness_entry = ParameterStaleness(
                            id=new_id(),
                            parameter_version_id=version_id,
                            fact_revision_id=UUID(
                                str(member["fact_revision_id"])
                            ),
                            review_state="current",
                            last_checked_at=now,
                        )
                        session.add(staleness_entry)

            await session.flush()

            # 加载参数以获取 variable_code
            param = await session.scalar(
                sa.select(Parameter).where(
                    Parameter.id == candidate.parameter_id
                )
            )
            variable_code: str = param.variable_code if param else ""

            return ParameterVersionRef(
                parameter_id=candidate.parameter_id,
                version=new_version,
                version_id=version_id,
                variable_code=variable_code,
                value=candidate.value,
                unit=candidate.unit,
                confidence=candidate.confidence,
                status="published",
                conditions=candidate.conditions,
                published_at=now,
            )

    async def reject(
        self, candidate_id: UUID, reviewer: UUID, comment: str
    ) -> dict:
        """拒绝候选。

        流程：
        1. 验证候选状态为 pending_review；
        2. 职责分离：验证 reviewer != submitted_by；
        3. 更新候选状态为 rejected；
        4. 更新参数状态为 rejected。

        Args:
            candidate_id: 候选 UUID。
            reviewer: 审核人 UUID。
            comment: 审核备注。

        Returns:
            dict: 候选信息。

        Raises:
            AppError: code="not_found"，当候选不存在时。
            AppError: code="candidate_not_pending"，当候选非 pending_review。
            AppError: code="self_approval_forbidden"，当审核人==提交人。
        """
        async with session_scope(self._factory) as session:
            candidate = await session.scalar(
                sa.select(ParameterCandidate).where(
                    ParameterCandidate.id == candidate_id
                )
            )
            if candidate is None:
                raise AppError(
                    code="not_found",
                    message=f"参数候选不存在: {candidate_id}",
                    retryable=False,
                    fields={"candidate_id": str(candidate_id)},
                )

            if candidate.status != "pending_review":
                raise AppError(
                    code="candidate_not_pending",
                    message=(
                        f"候选不在 pending_review 状态，"
                        f"当前状态: {candidate.status}"
                    ),
                    retryable=False,
                    fields={
                        "candidate_id": str(candidate_id),
                        "status": candidate.status,
                    },
                )

            # 职责分离
            if candidate.submitted_by == reviewer:
                raise AppError(
                    code="self_approval_forbidden",
                    message="提交人不能审批自己提交的参数候选",
                    retryable=False,
                    fields={
                        "candidate_id": str(candidate_id),
                        "reviewer": str(reviewer),
                    },
                )

            now: datetime = datetime.now(UTC)
            candidate.status = "rejected"
            candidate.reviewed_by = reviewer
            candidate.reviewed_at = now
            candidate.review_decision = "rejected"
            candidate.review_comment = comment

            # 更新参数状态为 rejected
            await session.execute(
                sa.update(Parameter)
                .values(
                    status=ParameterStatus.REJECTED.value,
                    updated_at=sa.func.now(),
                    lock_version=Parameter.lock_version + 1,
                )
                .where(Parameter.id == candidate.parameter_id)
            )

            await session.flush()

            return {
                "candidate_id": candidate.id,
                "parameter_id": candidate.parameter_id,
                "status": candidate.status,
                "reviewed_by": reviewer,
                "review_decision": candidate.review_decision,
            }

    async def get_parameter(self, parameter_id: UUID) -> dict:
        """获取参数详情（含当前版本）。

        Args:
            parameter_id: 参数 UUID。

        Returns:
            dict: 参数详情，含最新已发布版本信息。

        Raises:
            AppError: code="not_found"，当参数不存在时。
        """
        async with self._factory() as session:
            param = await session.scalar(
                sa.select(Parameter).where(
                    Parameter.id == parameter_id,
                    Parameter.organization_id == self._org_id,
                )
            )
            if param is None:
                raise AppError(
                    code="not_found",
                    message=f"参数不存在: {parameter_id}",
                    retryable=False,
                    fields={"parameter_id": str(parameter_id)},
                )

            # 查找最新已发布版本
            latest_version = await session.scalar(
                sa.select(ParameterVersion)
                .where(
                    ParameterVersion.parameter_id == parameter_id,
                    ParameterVersion.status == "published",
                )
                .order_by(ParameterVersion.version.desc())
                .limit(1)
            )

            return {
                "parameter_id": param.id,
                "variable_code": param.variable_code,
                "object_id": param.object_id,
                "status": param.status,
                "current_version": (
                    latest_version.version if latest_version else None
                ),
                "current_version_id": (
                    latest_version.id if latest_version else None
                ),
                "value": (
                    latest_version.value if latest_version else None
                ),
                "unit": (
                    latest_version.unit if latest_version else None
                ),
            }

    async def get_version(
        self, parameter_id: UUID, version: int | None = None
    ) -> ParameterVersionRef:
        """获取参数版本（指定版本或最新已发布版本）。

        Args:
            parameter_id: 参数 UUID。
            version: 版本号（None 表示最新已发布版本）。

        Returns:
            ParameterVersionRef: 参数版本引用。

        Raises:
            AppError: code="not_found"，当参数或版本不存在时。
        """
        async with self._factory() as session:
            # 校验参数存在
            param = await session.scalar(
                sa.select(Parameter).where(
                    Parameter.id == parameter_id,
                    Parameter.organization_id == self._org_id,
                )
            )
            if param is None:
                raise AppError(
                    code="not_found",
                    message=f"参数不存在: {parameter_id}",
                    retryable=False,
                    fields={"parameter_id": str(parameter_id)},
                )

            if version is not None:
                pv = await session.scalar(
                    sa.select(ParameterVersion).where(
                        ParameterVersion.parameter_id == parameter_id,
                        ParameterVersion.version == version,
                    )
                )
            else:
                pv = await session.scalar(
                    sa.select(ParameterVersion)
                    .where(
                        ParameterVersion.parameter_id == parameter_id,
                        ParameterVersion.status == "published",
                    )
                    .order_by(ParameterVersion.version.desc())
                    .limit(1)
                )

            if pv is None:
                raise AppError(
                    code="not_found",
                    message=(
                        f"参数版本不存在: parameter_id={parameter_id}, "
                        f"version={version}"
                    ),
                    retryable=False,
                    fields={
                        "parameter_id": str(parameter_id),
                        "version": str(version),
                    },
                )

            return ParameterVersionRef(
                parameter_id=parameter_id,
                version=pv.version,
                version_id=pv.id,
                variable_code=param.variable_code,
                value=pv.value,
                unit=pv.unit,
                confidence=pv.confidence,
                status=pv.status,
                conditions=pv.conditions,
                published_at=pv.published_at,
            )

    async def list_parameters(
        self,
        filters: dict | None = None,
        cursor: str | None = None,
        page_size: int = 20,
    ) -> tuple[list[dict], str | None]:
        """分页列出参数（按 variable_code, object_id, status 过滤）。

        Args:
            filters: 过滤条件字典，支持 variable_code, object_id, status。
            cursor: 分页游标（None 表示第一页）。
            page_size: 每页数量。

        Returns:
            tuple[list[dict], str | None]:
            (参数列表, 下一页游标)。
        """
        filters = filters or {}

        async with self._factory() as session:
            stmt = (
                sa.select(Parameter)
                .where(Parameter.organization_id == self._org_id)
                .order_by(Parameter.created_at, Parameter.id)
                .limit(page_size + 1)
            )

            # 应用过滤条件
            if filters.get("variable_code"):
                stmt = stmt.where(
                    Parameter.variable_code == filters["variable_code"]
                )
            if filters.get("object_id"):
                stmt = stmt.where(
                    Parameter.object_id == UUID(str(filters["object_id"]))
                )
            if filters.get("status"):
                stmt = stmt.where(Parameter.status == filters["status"])

            # 游标分页
            if cursor is not None:
                try:
                    raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
                    payload = json.loads(raw)
                    cursor_time = datetime.fromisoformat(
                        str(payload["v"])
                    )
                    cursor_id = UUID(str(payload["id"]))
                    stmt = stmt.where(
                        sa.or_(
                            Parameter.created_at > cursor_time,
                            sa.and_(
                                Parameter.created_at == cursor_time,
                                Parameter.id > cursor_id,
                            ),
                        )
                    )
                except Exception as exc:
                    raise AppError(
                        code="invalid_cursor",
                        message=f"分页游标无效: {exc}",
                        retryable=False,
                        fields={"cursor": cursor},
                    ) from exc

            result = await session.execute(stmt)
            params = result.scalars().all()

            next_cursor: str | None = None
            if len(params) > page_size:
                last = params[page_size - 1]
                next_cursor = base64.urlsafe_b64encode(
                    json.dumps(
                        {
                            "v": last.created_at.isoformat(),
                            "id": str(last.id),
                        },
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).decode("ascii")

            items: list[dict] = []
            for p in params[:page_size]:
                items.append(
                    {
                        "parameter_id": p.id,
                        "variable_code": p.variable_code,
                        "object_id": p.object_id,
                        "status": p.status,
                    }
                )
            return items, next_cursor

    async def list_candidates(self, parameter_id: UUID) -> list[dict]:
        """列出参数的所有候选。

        Args:
            parameter_id: 参数 UUID。

        Returns:
            list[dict]: 候选列表。
        """
        async with self._factory() as session:
            result = await session.execute(
                sa.select(ParameterCandidate)
                .where(ParameterCandidate.parameter_id == parameter_id)
                .order_by(ParameterCandidate.created_at)
            )
            candidates = result.scalars().all()

            return [
                {
                    "candidate_id": c.id,
                    "parameter_id": c.parameter_id,
                    "derivation_run_id": c.derivation_run_id,
                    "value": c.value,
                    "unit": c.unit,
                    "confidence": c.confidence,
                    "status": c.status,
                    "submitted_by": c.submitted_by,
                    "submitted_at": c.submitted_at,
                    "reviewed_by": c.reviewed_by,
                    "reviewed_at": c.reviewed_at,
                    "review_decision": c.review_decision,
                    "review_comment": c.review_comment,
                }
                for c in candidates
            ]

    async def check_staleness(
        self, parameter_version_id: UUID
    ) -> str:
        """检查参数版本的过期状态。

        查询所有关联的事实修订，检查是否有新修订。
        如果有新修订 → review_required；否则 current。

        Args:
            parameter_version_id: 参数版本 UUID。

        Returns:
            str: 过期状态（"current" 或 "review_required"）。
        """
        checker = StalenessChecker(self._factory, self._org_id)
        return await checker.check_parameter(parameter_version_id)

    async def deprecate(self, parameter_id: UUID) -> dict:
        """弃用参数（published → deprecated）。

        Args:
            parameter_id: 参数 UUID。

        Returns:
            dict: 参数信息。

        Raises:
            AppError: code="not_found"，当参数不存在时。
            AppError: code="invalid_transition"，当参数非 published 状态时。
        """
        async with session_scope(self._factory) as session:
            param = await session.scalar(
                sa.select(Parameter).where(
                    Parameter.id == parameter_id,
                    Parameter.organization_id == self._org_id,
                )
            )
            if param is None:
                raise AppError(
                    code="not_found",
                    message=f"参数不存在: {parameter_id}",
                    retryable=False,
                    fields={"parameter_id": str(parameter_id)},
                )

            if param.status != ParameterStatus.PUBLISHED.value:
                raise AppError(
                    code="invalid_transition",
                    message=(
                        f"参数当前状态为 {param.status}，"
                        f"仅 published 状态可弃用"
                    ),
                    retryable=False,
                    fields={
                        "parameter_id": str(parameter_id),
                        "status": param.status,
                    },
                )

            await session.execute(
                sa.update(Parameter)
                .values(
                    status=ParameterStatus.DEPRECATED.value,
                    updated_at=sa.func.now(),
                    lock_version=Parameter.lock_version + 1,
                )
                .where(Parameter.id == parameter_id)
            )

            # 同时将最新已发布版本标记为 deprecated
            await session.execute(
                sa.update(ParameterVersion)
                .values(status="deprecated")
                .where(
                    ParameterVersion.parameter_id == parameter_id,
                    ParameterVersion.status == "published",
                )
            )

            await session.flush()

            return {
                "parameter_id": parameter_id,
                "status": ParameterStatus.DEPRECATED.value,
            }
