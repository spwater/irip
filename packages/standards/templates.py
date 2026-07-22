"""事实模板 ORM 模型、验证器与业务服务（IRIP Task 12）。

定义两张表：
- fact_template: 事实模板主表，code 组织内唯一，含状态机字段；
- fact_template_version: 不可变版本表，存储观测要求 / 必要条件 / 工件角色 / 质量规则引用。

FactTemplateVersion 的 observations 列存储观测要求列表（JSONB），
每项包含 variable_version_id / required / cardinality。
TemplateValidator 验证模板版本引用的变量是否已发布、是否有重复观测等。

TemplateService 提供模板 CRUD + 生命周期管理 + 观测添加 + 验证。
"""

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from packages.common.database import Base, session_scope
from packages.common.db_types import GUID, UTCDateTime
from packages.common.errors import AppError
from packages.common.ids import new_id
from packages.common.pagination import MAX_PAGE_SIZE
from packages.standards.state_machine import StandardStatus, assert_transition
from packages.standards.variables import VariableVersion


class FactType(StrEnum):
    """事实类型枚举。

    Attributes:
        EXPERIMENT_RUN: 实验运行记录。
        SIMULATION_RUN: 仿真运行记录。
        DOCUMENT_RECORD: 文档记录。
        MODEL_EXECUTION: 模型执行记录。
    """

    EXPERIMENT_RUN = "experiment_run"
    SIMULATION_RUN = "simulation_run"
    DOCUMENT_RECORD = "document_record"
    MODEL_EXECUTION = "model_execution"


class Cardinality(StrEnum):
    """观测基数枚举。

    Attributes:
        ONE: 单值观测（一次事实中该变量只有一个值）。
        MANY: 多值观测（一次事实中该变量可有多个值）。
    """

    ONE = "one"
    MANY = "many"


@dataclass(frozen=True)
class ObservationRequirement:
    """观测要求：模板版本中对某一变量的观测约束。

    Attributes:
        variable_version_id: 引用的标准变量版本 ID。
        required: 是否为必需观测。
        cardinality: 基数（one / many）。
    """

    variable_version_id: UUID
    required: bool
    cardinality: str


@dataclass(frozen=True)
class ValidationReport:
    """模板验证报告。

    Attributes:
        valid: 是否通过验证。
        codes: 错误码元组（如 ``("duplicate_observation:temp", "missing_unit:press")``）。
        messages: 错误消息元组（与 codes 一一对应）。
    """

    valid: bool
    codes: tuple[str, ...]
    messages: tuple[str, ...]


class FactTemplate(Base):
    """事实模板实体（对应 fact_template 表）。

    code 在组织内唯一（UNIQUE 约束 (organization_id, code)）。

    Attributes:
        id: 模板 UUID。
        organization_id: 所属组织 ID。
        code: 模板编码（组织内唯一，创建后锁定）。
        display_name: 中文显示名。
        fact_type: 事实类型（experiment_run / simulation_run / ...）。
        status: 状态（draft / in_review / published / rejected / deprecated）。
        version_count: 已创建版本数（默认 0）。
        created_at: 创建时间。
        updated_at: 更新时间。
        lock_version: 乐观锁版本号。
    """

    __tablename__ = "fact_template"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    organization_id: Mapped[UUID] = mapped_column(GUID, nullable=False)
    code: Mapped[str] = mapped_column(sa.Text, nullable=False)
    display_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    fact_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'draft'")
    )
    version_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    lock_version: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "organization_id", "code", name="uq_fact_template_org_code"
        ),
    )

    def __repr__(self) -> str:
        return (
            f"FactTemplate(id={self.id!r}, code={self.code!r}, "
            f"fact_type={self.fact_type!r}, status={self.status!r})"
        )


class FactTemplateVersion(Base):
    """事实模板不可变版本实体（对应 fact_template_version 表）。

    提交审核时从当前模板草稿创建一行。发布后核心属性不可修改。

    Attributes:
        id: 版本 UUID。
        template_id: 所属模板 ID（FK→fact_template.id）。
        version: 版本号（从 1 开始递增）。
        code: 模板编码快照。
        display_name: 显示名快照。
        fact_type: 事实类型快照。
        required_conditions: 必要条件（JSONB UUID 字符串列表）。
        observations: 观测要求列表（JSONB，每项含 variable_version_id/required/cardinality）。
        required_artifact_roles: 必需工件角色（JSONB 字符串列表）。
        quality_rule_codes: 质量规则编码列表（JSONB 字符串列表）。
        status: 版本状态（draft / in_review / published / rejected / deprecated）。
        published_at: 发布时间（发布后设置）。
        published_by: 发布人 UUID（发布后设置）。
        deprecated_at: 弃用时间（弃用后设置）。
        deprecated_by: 弃用人 UUID（弃用后设置）。
        rejection_reason: 拒绝原因（拒绝后设置）。
        created_at: 版本创建时间。
        lock_version: 乐观锁版本号。
    """

    __tablename__ = "fact_template_version"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    template_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("fact_template.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    code: Mapped[str] = mapped_column(sa.Text, nullable=False)
    display_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    fact_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    required_conditions: Mapped[list[str] | None] = mapped_column(
        JSONB, nullable=True
    )
    observations: Mapped[list[dict[str, object]] | None] = mapped_column(
        JSONB, nullable=True
    )
    required_artifact_roles: Mapped[list[str] | None] = mapped_column(
        JSONB, nullable=True
    )
    quality_rule_codes: Mapped[list[str] | None] = mapped_column(
        JSONB, nullable=True
    )
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime, nullable=True
    )
    published_by: Mapped[UUID | None] = mapped_column(GUID, nullable=True)
    deprecated_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime, nullable=True
    )
    deprecated_by: Mapped[UUID | None] = mapped_column(GUID, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(
        sa.Text, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    lock_version: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )

    def __repr__(self) -> str:
        return (
            f"FactTemplateVersion(id={self.id!r}, "
            f"template_id={self.template_id!r}, "
            f"version={self.version!r}, status={self.status!r})"
        )


class TemplateValidator:
    """事实模板版本验证器。

    验证规则：
    1. 必需观测（required=True）的 variable_version_id 不在已发布变量集合中
       → ``missing_observation:{variable_code}``
    2. 非必需观测（required=False）的 variable_version_id 不在已发布变量集合中
       → ``reference_not_published:{variable_code}``
    3. 重复观测变量编码 → ``duplicate_observation:{code}``
    4. 数值型变量无 canonical_unit → ``missing_unit:{variable_code}``
    5. 必要条件引用的对象类型未发布 → ``condition_not_published:{object_id}``
    """

    @staticmethod
    def validate(
        template_version: FactTemplateVersion,
        published_variables: dict[UUID, VariableVersion],
    ) -> ValidationReport:
        """验证模板版本。

        Args:
            template_version: 待验证的模板版本实体。
            published_variables: 已发布变量版本字典（variable_version_id → VariableVersion）。

        Returns:
            ValidationReport: 验证报告，包含是否通过、错误码列表与消息列表。
        """
        codes: list[str] = []
        messages: list[str] = []

        observations = template_version.observations or []
        seen_codes: dict[str, int] = {}

        for obs in observations:
            vv_id_str = str(obs.get("variable_version_id", ""))
            required = bool(obs.get("required", False))

            try:
                vv_id = UUID(vv_id_str)
            except (ValueError, TypeError):
                codes.append(f"invalid_observation:{vv_id_str}")
                messages.append(f"观测引用的变量版本 ID 无效: {vv_id_str}")
                continue

            var_version = published_variables.get(vv_id)
            if var_version is None:
                if required:
                    codes.append(f"missing_observation:{vv_id_str}")
                    messages.append(
                        f"必需观测引用的变量版本未发布: {vv_id_str}"
                    )
                else:
                    codes.append(f"reference_not_published:{vv_id_str}")
                    messages.append(
                        f"观测引用的变量版本未发布: {vv_id_str}"
                    )
                continue

            var_code = var_version.code
            seen_codes[var_code] = seen_codes.get(var_code, 0) + 1

            if (
                var_version.data_type == "number"
                and not var_version.canonical_unit
            ):
                codes.append(f"missing_unit:{var_code}")
                messages.append(
                    f"数值型变量 '{var_code}' 缺少 canonical_unit"
                )

        for var_code, count in seen_codes.items():
            if count > 1:
                codes.append(f"duplicate_observation:{var_code}")
                messages.append(
                    f"观测中存在重复的变量编码: {var_code}"
                )

        valid = len(codes) == 0
        return ValidationReport(
            valid=valid,
            codes=tuple(codes),
            messages=tuple(messages),
        )


class TemplateService:
    """事实模板业务编排服务。

    依赖注入 session_factory（事务管理）、organization_id（当前组织）、actor_id（操作人）。

    Attributes:
        _factory: 异步会话工厂。
        _org_id: 当前组织 ID。
        _actor_id: 当前操作人 ID（用于 published_by / deprecated_by）。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        organization_id: UUID,
        actor_id: UUID | None = None,
    ) -> None:
        """初始化事实模板服务。

        Args:
            session_factory: 异步会话工厂。
            organization_id: 当前组织 ID。
            actor_id: 当前操作人 ID（可选）。
        """
        self._factory = session_factory
        self._org_id = organization_id
        self._actor_id = actor_id

    async def create_template(
        self,
        code: str,
        display_name: str,
        fact_type: str,
    ) -> FactTemplate:
        """创建事实模板（DRAFT 状态, version_count=0）。

        Args:
            code: 模板编码（组织内唯一）。
            display_name: 中文显示名。
            fact_type: 事实类型（experiment_run / simulation_run / ...）。

        Returns:
            FactTemplate: 新创建的模板实体。

        Raises:
            AppError: code="conflict"，当编码已存在时。
        """
        async with session_scope(self._factory) as session:
            existing = await session.execute(
                sa.select(FactTemplate).where(
                    FactTemplate.organization_id == self._org_id,
                    FactTemplate.code == code,
                )
            )
            if existing.scalar_one_or_none() is not None:
                raise AppError(
                    code="conflict",
                    message="事实模板编码已存在",
                    retryable=False,
                    fields={"code": code},
                )

            now = datetime.now(UTC)
            template = FactTemplate(
                id=new_id(),
                organization_id=self._org_id,
                code=code,
                display_name=display_name,
                fact_type=fact_type,
                status="draft",
                version_count=0,
                created_at=now,
                updated_at=now,
                lock_version=0,
            )
            session.add(template)
            await session.flush()
            return template

    async def add_observation(
        self,
        template_id: UUID,
        variable_version_id: UUID,
        required: bool,
        cardinality: str,
    ) -> FactTemplateVersion:
        """为模板添加观测要求（创建或更新草稿版本）。

        只能在模板处于 draft 或 rejected 状态时添加。rejected 时自动转为 draft。

        Args:
            template_id: 模板 UUID。
            variable_version_id: 标准变量版本 ID。
            required: 是否必需。
            cardinality: 基数（one / many）。

        Returns:
            FactTemplateVersion: 更新后的草稿版本。

        Raises:
            AppError: code="not_found"，当模板不存在时。
            AppError: code="invalid_transition"，当模板状态不允许修改时。
        """
        async with session_scope(self._factory) as session:
            template = await self._get_and_check_org(session, template_id)

            if template.status == "rejected":
                assert_transition("rejected", "draft")
                await session.execute(
                    sa.update(FactTemplate)
                    .values(
                        status="draft",
                        updated_at=sa.func.now(),
                        lock_version=FactTemplate.lock_version + 1,
                    )
                    .where(
                        FactTemplate.id == template_id,
                        FactTemplate.lock_version == template.lock_version,
                    )
                )
                template.status = "draft"

            if template.status != "draft":
                raise AppError(
                    code="invalid_transition",
                    message="只能在草稿状态下添加观测",
                    retryable=False,
                    fields={"status": template.status},
                )

            draft_version = await self._get_or_create_draft_version(
                session, template
            )

            observations = draft_version.observations or []
            observations.append(
                {
                    "variable_version_id": str(variable_version_id),
                    "required": required,
                    "cardinality": cardinality,
                }
            )

            await session.execute(
                sa.update(FactTemplateVersion)
                .values(observations=observations)
                .where(FactTemplateVersion.id == draft_version.id)
            )
            await session.flush()

            result = await session.execute(
                sa.select(FactTemplateVersion).where(
                    FactTemplateVersion.id == draft_version.id
                )
            )
            return result.scalar_one()

    async def submit_template(self, template_id: UUID) -> FactTemplateVersion:
        """提交审核（DRAFT → IN_REVIEW，验证后创建版本快照）。

        验证草稿版本的观测要求，通过后转为审核状态。

        Args:
            template_id: 模板 UUID。

        Returns:
            FactTemplateVersion: 转为 in_review 状态的版本。

        Raises:
            AppError: code="not_found"，当模板不存在时。
            AppError: code="invalid_transition"，当状态非 draft 时。
            AppError: code="validation_failed"，当验证不通过时。
        """
        async with session_scope(self._factory) as session:
            template = await self._get_and_check_org(session, template_id)
            assert_transition(template.status, StandardStatus.IN_REVIEW)

            draft = await self._get_draft_version(session, template_id)
            if draft is None:
                raise AppError(
                    code="validation_failed",
                    message="模板没有观测要求，无法提交",
                    retryable=False,
                    fields={"template_id": str(template_id)},
                )

            report = await self._validate_draft(session, draft)
            if not report.valid:
                raise AppError(
                    code="validation_failed",
                    message="模板验证失败: " + "; ".join(report.messages),
                    retryable=False,
                    fields={"codes": list(report.codes)},
                )

            await session.execute(
                sa.update(FactTemplateVersion)
                .values(
                    status=StandardStatus.IN_REVIEW,
                    lock_version=FactTemplateVersion.lock_version + 1,
                )
                .where(
                    FactTemplateVersion.id == draft.id,
                    FactTemplateVersion.lock_version == draft.lock_version,
                )
            )

            await session.execute(
                sa.update(FactTemplate)
                .values(
                    status=StandardStatus.IN_REVIEW,
                    updated_at=sa.func.now(),
                    lock_version=FactTemplate.lock_version + 1,
                    version_count=FactTemplate.version_count + 1,
                )
                .where(
                    FactTemplate.id == template_id,
                    FactTemplate.lock_version == template.lock_version,
                )
            )

            result = await session.execute(
                sa.select(FactTemplateVersion).where(
                    FactTemplateVersion.id == draft.id
                )
            )
            return result.scalar_one()

    async def publish_template(self, template_id: UUID) -> FactTemplateVersion:
        """发布模板（IN_REVIEW → PUBLISHED，版本此后不可变）。

        Args:
            template_id: 模板 UUID。

        Returns:
            FactTemplateVersion: 已发布的版本。

        Raises:
            AppError: code="not_found"，当模板不存在时。
            AppError: code="invalid_transition"，当状态非 in_review 时。
        """
        async with session_scope(self._factory) as session:
            template = await self._get_and_check_org(session, template_id)
            assert_transition(template.status, StandardStatus.PUBLISHED)

            latest = await self._get_latest_version(session, template_id)
            if latest is None:
                raise AppError(
                    code="not_found",
                    message="没有待审核的版本",
                    retryable=False,
                    fields={"template_id": str(template_id)},
                )

            await session.execute(
                sa.update(FactTemplateVersion)
                .values(
                    status=StandardStatus.PUBLISHED,
                    published_at=sa.func.now(),
                    published_by=self._actor_id,
                    lock_version=FactTemplateVersion.lock_version + 1,
                )
                .where(
                    FactTemplateVersion.id == latest.id,
                    FactTemplateVersion.lock_version == latest.lock_version,
                )
            )

            await session.execute(
                sa.update(FactTemplate)
                .values(
                    status=StandardStatus.PUBLISHED,
                    updated_at=sa.func.now(),
                    lock_version=FactTemplate.lock_version + 1,
                )
                .where(
                    FactTemplate.id == template_id,
                    FactTemplate.lock_version == template.lock_version,
                )
            )

            result = await session.execute(
                sa.select(FactTemplateVersion).where(
                    FactTemplateVersion.id == latest.id
                )
            )
            return result.scalar_one()

    async def reject_template(
        self, template_id: UUID, reason: str
    ) -> FactTemplateVersion:
        """拒绝模板（IN_REVIEW → REJECTED，设置拒绝原因）。

        Args:
            template_id: 模板 UUID。
            reason: 拒绝原因（必填）。

        Returns:
            FactTemplateVersion: 已拒绝的版本。
        """
        async with session_scope(self._factory) as session:
            template = await self._get_and_check_org(session, template_id)
            assert_transition(template.status, StandardStatus.REJECTED)

            latest = await self._get_latest_version(session, template_id)
            if latest is None:
                raise AppError(
                    code="not_found",
                    message="没有待审核的版本",
                    retryable=False,
                    fields={"template_id": str(template_id)},
                )

            await session.execute(
                sa.update(FactTemplateVersion)
                .values(
                    status=StandardStatus.REJECTED,
                    rejection_reason=reason,
                    lock_version=FactTemplateVersion.lock_version + 1,
                )
                .where(
                    FactTemplateVersion.id == latest.id,
                    FactTemplateVersion.lock_version == latest.lock_version,
                )
            )

            await session.execute(
                sa.update(FactTemplate)
                .values(
                    status=StandardStatus.REJECTED,
                    updated_at=sa.func.now(),
                    lock_version=FactTemplate.lock_version + 1,
                )
                .where(
                    FactTemplate.id == template_id,
                    FactTemplate.lock_version == template.lock_version,
                )
            )

            result = await session.execute(
                sa.select(FactTemplateVersion).where(
                    FactTemplateVersion.id == latest.id
                )
            )
            return result.scalar_one()

    async def deprecate_template(self, template_id: UUID) -> FactTemplateVersion:
        """弃用模板（PUBLISHED → DEPRECATED）。

        Args:
            template_id: 模板 UUID。

        Returns:
            FactTemplateVersion: 已弃用的版本。
        """
        async with session_scope(self._factory) as session:
            template = await self._get_and_check_org(session, template_id)
            assert_transition(template.status, StandardStatus.DEPRECATED)

            published = await self._get_published_version(session, template_id)
            if published is None:
                raise AppError(
                    code="not_found",
                    message="没有已发布的版本",
                    retryable=False,
                    fields={"template_id": str(template_id)},
                )

            await session.execute(
                sa.update(FactTemplateVersion)
                .values(
                    status=StandardStatus.DEPRECATED,
                    deprecated_at=sa.func.now(),
                    deprecated_by=self._actor_id,
                    lock_version=FactTemplateVersion.lock_version + 1,
                )
                .where(
                    FactTemplateVersion.id == published.id,
                    FactTemplateVersion.lock_version
                    == published.lock_version,
                )
            )

            await session.execute(
                sa.update(FactTemplate)
                .values(
                    status=StandardStatus.DEPRECATED,
                    updated_at=sa.func.now(),
                    lock_version=FactTemplate.lock_version + 1,
                )
                .where(
                    FactTemplate.id == template_id,
                    FactTemplate.lock_version == template.lock_version,
                )
            )

            result = await session.execute(
                sa.select(FactTemplateVersion).where(
                    FactTemplateVersion.id == published.id
                )
            )
            return result.scalar_one()

    async def validate_template(self, template_id: UUID) -> ValidationReport:
        """验证模板的草稿版本（不提交，仅返回验证报告）。

        Args:
            template_id: 模板 UUID。

        Returns:
            ValidationReport: 验证报告。

        Raises:
            AppError: code="not_found"，当模板不存在时。
        """
        async with self._factory() as session:
            template = await self._get_and_check_org(session, template_id)
            draft = await self._get_draft_version(session, template_id)
            if draft is None:
                return ValidationReport(
                    valid=True,
                    codes=(),
                    messages=(),
                )
            return await self._validate_draft(session, draft)

    async def get_template_by_code(self, code: str) -> dict:
        """按编码查询模板详情（含最新版本）。

        Args:
            code: 模板编码。

        Returns:
            dict: 模板详情。

        Raises:
            AppError: code="not_found"，当模板不存在时。
        """
        async with self._factory() as session:
            result = await session.execute(
                sa.select(FactTemplate).where(
                    FactTemplate.organization_id == self._org_id,
                    FactTemplate.code == code,
                )
            )
            template = result.scalar_one_or_none()
            if template is None:
                raise AppError(
                    code="not_found",
                    message="事实模板不存在",
                    retryable=False,
                    fields={"code": code},
                )
            latest = await self._get_latest_version(session, template.id)

        return {
            "id": str(template.id),
            "organization_id": str(template.organization_id),
            "code": template.code,
            "display_name": template.display_name,
            "fact_type": template.fact_type,
            "status": template.status,
            "version_count": template.version_count,
            "created_at": template.created_at,
            "updated_at": template.updated_at,
            "lock_version": template.lock_version,
            "latest_version": _template_version_to_dict(latest)
            if latest
            else None,
        }

    async def get_template(self, template_id: UUID) -> dict:
        """查询单个模板详情（含最新版本）。

        Args:
            template_id: 模板 UUID。

        Returns:
            dict: 模板详情。

        Raises:
            AppError: code="not_found"，当模板不存在时。
        """
        async with self._factory() as session:
            template = await self._get_and_check_org(session, template_id)
            latest = await self._get_latest_version(session, template_id)

        return {
            "id": str(template.id),
            "organization_id": str(template.organization_id),
            "code": template.code,
            "display_name": template.display_name,
            "fact_type": template.fact_type,
            "status": template.status,
            "version_count": template.version_count,
            "created_at": template.created_at,
            "updated_at": template.updated_at,
            "lock_version": template.lock_version,
            "latest_version": _template_version_to_dict(latest)
            if latest
            else None,
        }

    async def list_templates(
        self,
        cursor: str | None = None,
        page_size: int = 20,
    ) -> tuple[list[dict], str | None]:
        """分页查询模板列表（含最新版本摘要）。

        Args:
            cursor: 分页游标，None 表示第一页。
            page_size: 每页数量（默认 20，最大 100）。

        Returns:
            tuple[list[dict], str | None]: (模板列表, 下一页游标)。
        """
        effective_size = min(max(page_size, 1), MAX_PAGE_SIZE)
        fetch_limit = effective_size + 1

        query = (
            sa.select(FactTemplate)
            .where(FactTemplate.organization_id == self._org_id)
            .order_by(
                FactTemplate.created_at.asc(), FactTemplate.id.asc()
            )
            .limit(fetch_limit)
        )

        if cursor is not None:
            cursor_created_at, cursor_id = _decode_cursor(cursor)
            query = query.where(
                sa.or_(
                    FactTemplate.created_at > cursor_created_at,
                    sa.and_(
                        FactTemplate.created_at == cursor_created_at,
                        FactTemplate.id > cursor_id,
                    ),
                )
            )

        async with self._factory() as session:
            result = await session.execute(query)
            templates = list(result.scalars().all())

            items: list[dict] = []
            for t in templates:
                latest = await self._get_latest_version(session, t.id)
                items.append(
                    {
                        "id": str(t.id),
                        "code": t.code,
                        "display_name": t.display_name,
                        "fact_type": t.fact_type,
                        "status": t.status,
                        "version_count": t.version_count,
                        "created_at": t.created_at,
                        "updated_at": t.updated_at,
                        "lock_version": t.lock_version,
                        "latest_version": _template_version_to_dict(latest)
                        if latest
                        else None,
                    }
                )

        has_more = len(items) > effective_size
        page_items = items[:effective_size]

        next_cursor: str | None = None
        if has_more and page_items:
            last = templates[:effective_size][-1]
            next_cursor = _encode_cursor(last.created_at, last.id)

        return page_items, next_cursor

    async def _get_and_check_org(
        self,
        session: AsyncSession,
        template_id: UUID,
    ) -> FactTemplate:
        """读取模板并校验组织归属。"""
        result = await session.execute(
            sa.select(FactTemplate).where(FactTemplate.id == template_id)
        )
        template = result.scalar_one_or_none()
        if template is None or template.organization_id != self._org_id:
            raise AppError(
                code="not_found",
                message="事实模板不存在",
                retryable=False,
                fields={"template_id": str(template_id)},
            )
        return template

    async def _get_or_create_draft_version(
        self,
        session: AsyncSession,
        template: FactTemplate,
    ) -> FactTemplateVersion:
        """获取或创建草稿版本（version = version_count + 1, status=draft）。"""
        draft = await self._get_draft_version(session, template.id)
        if draft is not None:
            return draft

        new_version_number = template.version_count + 1
        now = datetime.now(UTC)
        draft = FactTemplateVersion(
            id=new_id(),
            template_id=template.id,
            version=new_version_number,
            code=template.code,
            display_name=template.display_name,
            fact_type=template.fact_type,
            required_conditions=[],
            observations=[],
            required_artifact_roles=[],
            quality_rule_codes=[],
            status="draft",
            lock_version=0,
        )
        session.add(draft)
        await session.flush()
        return draft

    async def _get_draft_version(
        self,
        session: AsyncSession,
        template_id: UUID,
    ) -> FactTemplateVersion | None:
        """查询模板的草稿版本（status=draft，按版本号降序取第一条）。"""
        result = await session.execute(
            sa.select(FactTemplateVersion)
            .where(
                FactTemplateVersion.template_id == template_id,
                FactTemplateVersion.status == "draft",
            )
            .order_by(FactTemplateVersion.version.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _get_latest_version(
        self,
        session: AsyncSession,
        template_id: UUID,
    ) -> FactTemplateVersion | None:
        """查询模板的最新版本（按版本号降序取第一条）。"""
        result = await session.execute(
            sa.select(FactTemplateVersion)
            .where(FactTemplateVersion.template_id == template_id)
            .order_by(FactTemplateVersion.version.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _get_published_version(
        self,
        session: AsyncSession,
        template_id: UUID,
    ) -> FactTemplateVersion | None:
        """查询模板的已发布版本（status=published）。"""
        result = await session.execute(
            sa.select(FactTemplateVersion)
            .where(
                FactTemplateVersion.template_id == template_id,
                FactTemplateVersion.status == "published",
            )
            .order_by(FactTemplateVersion.version.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _validate_draft(
        self,
        session: AsyncSession,
        draft: FactTemplateVersion,
    ) -> ValidationReport:
        """验证草稿版本：查询已发布变量版本，调用 TemplateValidator。"""
        observations = draft.observations or []
        vv_ids: list[UUID] = []
        for obs in observations:
            vv_id_str = str(obs.get("variable_version_id", ""))
            try:
                vv_ids.append(UUID(vv_id_str))
            except (ValueError, TypeError):
                continue

        published_vars: dict[UUID, VariableVersion] = {}
        if vv_ids:
            result = await session.execute(
                sa.select(VariableVersion).where(
                    VariableVersion.id.in_(vv_ids),
                    VariableVersion.status == "published",
                )
            )
            for vv in result.scalars().all():
                published_vars[vv.id] = vv

        return TemplateValidator.validate(draft, published_vars)


def _template_version_to_dict(version: FactTemplateVersion) -> dict:
    """将 FactTemplateVersion ORM 实体转为字典。"""
    return {
        "id": str(version.id),
        "template_id": str(version.template_id),
        "version": version.version,
        "code": version.code,
        "display_name": version.display_name,
        "fact_type": version.fact_type,
        "required_conditions": version.required_conditions or [],
        "observations": version.observations or [],
        "required_artifact_roles": version.required_artifact_roles or [],
        "quality_rule_codes": version.quality_rule_codes or [],
        "status": version.status,
        "published_at": version.published_at,
        "published_by": str(version.published_by)
        if version.published_by
        else None,
        "deprecated_at": version.deprecated_at,
        "deprecated_by": str(version.deprecated_by)
        if version.deprecated_by
        else None,
        "rejection_reason": version.rejection_reason,
        "created_at": version.created_at,
        "lock_version": version.lock_version,
    }


def _encode_cursor(created_at: datetime, entity_id: UUID) -> str:
    """编码 keyset 分页游标。"""
    payload = json.dumps(
        {"v": created_at.isoformat(), "id": str(entity_id)},
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    """解码 keyset 分页游标。"""
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
    except (binascii.Error, UnicodeEncodeError, ValueError) as exc:
        raise AppError(
            code="invalid_cursor",
            message="分页游标无效：base64url 解码失败",
            retryable=False,
            fields={"cursor": cursor},
        ) from exc

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise AppError(
            code="invalid_cursor",
            message="分页游标无效：JSON 解析失败",
            retryable=False,
            fields={"cursor": cursor},
        ) from exc

    if not isinstance(payload, dict) or "v" not in payload or "id" not in payload:
        raise AppError(
            code="invalid_cursor",
            message="分页游标无效：缺少必要字段 v / id",
            retryable=False,
            fields={"cursor": cursor},
        )

    try:
        created_at = datetime.fromisoformat(str(payload["v"]))
    except (ValueError, TypeError) as exc:
        raise AppError(
            code="invalid_cursor",
            message="分页游标无效：v 字段不是合法 ISO 时间",
            retryable=False,
            fields={"cursor": cursor},
        ) from exc

    try:
        entity_id = UUID(str(payload["id"]))
    except (ValueError, AttributeError, TypeError) as exc:
        raise AppError(
            code="invalid_cursor",
            message="分页游标无效：id 字段不是合法 UUID",
            retryable=False,
            fields={"cursor": cursor},
        ) from exc

    return created_at, entity_id
