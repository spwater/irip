"""IRIP 模型生命周期业务编排服务（V2-T04）。

ModelService 提供模型从创建→提交验证→验证→发布→预测→回滚→废弃
的完整生命周期管理。

核心不变量：
1. 唯一性：部门内 (department_id, code) 唯一；
2. 版本不可变：已发布版本不可修改，新变更创建新版本；
3. 发布指针：current_version_id 指向当前已发布版本，
   rollback 通过移动指针实现（不删除旧版本）；
4. 状态机：版本状态流转
   draft → pending_validation → validated → published → deprecated；
5. 适用域：预测前校验输入是否在适用域范围内。

依赖注入 session_factory（事务管理）、department_id（当前部门）、
artifact_service（工件上传/下载）、fact_service（写 model_execution 事实）。
所有写操作通过 session_scope 事务上下文管理。
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.clock import Clock, SystemClock
from packages.common.database import ScopedSessionMixin
from packages.common.errors import AppError
from packages.common.ids import new_id
from packages.models.adapters import build_adapter
from packages.models.applicability import ApplicabilityChecker
from packages.models.contracts import ModelContract, ModelOutput
from packages.models.entities import Model, ModelVersion

#: 合法版本状态集合。
_VERSION_STATUSES: frozenset[str] = frozenset(
    {
        "draft",
        "pending_validation",
        "validated",
        "published",
        "deprecated",
    }
)


@dataclass(frozen=True)
class PredictionResult:
    """模型预测结果（不可变值对象）。

    由 ModelService.predict() / predict_version() 返回，
    封装预测输出、使用的模型版本信息与元数据。

    Attributes:
        model_id: 模型 ID。
        model_version_id: 使用的模型版本 ID。
        version: 版本号。
        predictions: 预测结果字典（输出维度名 → 预测值）。
        metadata: 预测元数据（如耗时、是否在适用域内等）。
        fact_id: 写入的 model_execution 事实 ID（可空）。
    """

    model_id: UUID
    model_version_id: UUID
    version: int
    predictions: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    fact_id: UUID | None = None


class ModelService(ScopedSessionMixin):
    """模型生命周期业务编排服务。

    依赖注入 session_factory（事务管理）、department_id（当前部门）、
    artifact_service（工件上传/下载）、fact_service（写事实）。

    Attributes:
        _factory: 异步会话工厂。
        _dept_id: 当前部门 ID。
        _artifact_service: 工件服务实例。
        _fact_service: 事实服务实例。
        _clock: 时钟（默认 SystemClock）。
        _applicability_checker: 适用域检查器。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        department_id: UUID,
        actor_id: UUID,
        artifact_service: Any,
        fact_service: Any | None = None,
        clock: Clock | None = None,
    ) -> None:
        """初始化模型服务。

        Args:
            session_factory: 异步会话工厂。
            department_id: 当前部门 ID。
            actor_id: 当前操作者用户 ID（用于模型所有者 owner_user_id）。
            artifact_service: 工件服务实例（用于上传/下载模型工件）。
            fact_service: 事实服务实例（用于写 model_execution 事实）。
            clock: 时钟（默认 SystemClock）。
        """
        self._factory = session_factory
        self._dept_id = department_id
        self._actor_id = actor_id
        self._artifact_service = artifact_service
        self._fact_service = fact_service
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._applicability_checker = ApplicabilityChecker()

    # ---- 公开只读属性 ----

    @property
    def actor_id(self) -> UUID:
        """当前操作者用户 ID（公开只读访问）。"""
        return self._actor_id

    # ---- 创建与版本管理 ----

    async def create_model(
        self,
        code: str,
        display_name: str,
    ) -> Model:
        """创建模型（status=draft）。

        在当前部门内创建模型主记录，(department_id, code) 唯一。

        Args:
            code: 模型代码（部门内唯一）。
            display_name: 模型显示名称。

        Returns:
            Model: 新创建的模型实体。

        Raises:
            AppError: code="conflict"，当 code 已存在时。
        """
        # 幂等冲突检查
        async with self._scoped_session() as session:
            existing = await session.scalar(
                sa.select(Model).where(
                    Model.code == code,
                )
            )
            if existing is not None:
                raise AppError(
                    code="conflict",
                    message=f"模型代码已存在: {code}",
                    retryable=False,
                    fields={"code": code},
                )

        async with self._scoped_session() as session:
            model = Model(
                id=new_id(),
                department_id=self._dept_id,
                owner_user_id=self._actor_id,
                visibility_scope="tree",
                code=code,
                display_name=display_name,
                status="draft",
                lock_version=0,
            )
            session.add(model)
            await session.flush()
            return model

    async def create_version(
        self,
        model_id: UUID,
        contract: ModelContract,
        model_artifact_id: UUID | None = None,
        metrics: dict[str, Any] | None = None,
        applicability_domain: dict[str, Any] | None = None,
        code_hash: str | None = None,
        dependency_hash: str | None = None,
        model_hash: str | None = None,
    ) -> ModelVersion:
        """创建模型版本（status=draft）。

        为指定模型创建新版本，版本号自动递增。

        Args:
            model_id: 模型 ID。
            contract: 模型契约。
            model_artifact_id: 模型工件 UUID（可空）。
            metrics: 验证指标字典（可空）。
            applicability_domain: 适用域字典（可空，默认取契约中的）。
            code_hash: 训练代码哈希（可空）。
            dependency_hash: 依赖清单哈希（可空）。
            model_hash: 模型工件哈希（可空）。

        Returns:
            ModelVersion: 新创建的版本实体。

        Raises:
            AppError: code="not_found"，当模型不存在时。
        """
        async with self._scoped_session() as session:
            model = await self._get_model_owned(session, model_id)
            if model is None:
                raise AppError(
                    code="not_found",
                    message=f"模型不存在: {model_id}",
                    retryable=False,
                    fields={"model_id": str(model_id)},
                )

            # 计算下一版本号
            max_version = await session.scalar(
                sa.select(sa.func.max(ModelVersion.version)).where(
                    ModelVersion.model_id == model_id
                )
            )
            next_version: int = int(max_version or 0) + 1

            domain = applicability_domain or contract.applicability_domain
            version = ModelVersion(
                id=new_id(),
                model_id=model_id,
                version=next_version,
                contract_json=contract.to_dict(),
                model_artifact_id=model_artifact_id,
                metrics_json=metrics or {},
                applicability_domain_json=domain,
                code_hash=code_hash,
                dependency_hash=dependency_hash,
                model_hash=model_hash,
                status="draft",
            )
            session.add(version)
            await session.flush()
            return version

    async def submit_for_validation(
        self,
        model_id: UUID,
        version_id: UUID,
    ) -> ModelVersion:
        """提交验证（状态 draft → pending_validation）。

        Args:
            model_id: 模型 ID。
            version_id: 版本 ID。

        Returns:
            ModelVersion: 更新后的版本实体。

        Raises:
            AppError: code="not_found"，当版本不存在时。
            AppError: code="invalid_state"，当版本状态非 draft 时。
        """
        async with self._scoped_session() as session:
            version = await self._get_version_owned(session, model_id, version_id)
            if version is None:
                raise AppError(
                    code="not_found",
                    message=f"模型版本不存在: {version_id}",
                    retryable=False,
                    fields={"version_id": str(version_id)},
                )
            if version.status != "draft":
                raise AppError(
                    code="invalid_state",
                    message=(f"版本状态非 draft（当前: {version.status}），无法提交验证"),
                    retryable=False,
                    fields={"status": version.status},
                )
            version.status = "pending_validation"
            await session.flush()
            return version

    async def validate(
        self,
        model_id: UUID,
        version_id: UUID,
        dataset_artifact_id: UUID | None = None,
        metrics: dict[str, Any] | None = None,
        applicability_domain: dict[str, Any] | None = None,
    ) -> ModelVersion:
        """验证模型版本（状态 pending_validation → validated）。

        记录验证数据集、指标与适用域，将版本状态置为 validated。

        Args:
            model_id: 模型 ID。
            version_id: 版本 ID。
            dataset_artifact_id: 验证数据集工件 ID（可空）。
            metrics: 验证指标字典（如 R²、RMSE）。
            applicability_domain: 适用域字典（可空，保留原值）。

        Returns:
            ModelVersion: 更新后的版本实体。

        Raises:
            AppError: code="not_found"，当版本不存在时。
            AppError: code="invalid_state"，当版本状态非 pending_validation 时。
        """
        async with self._scoped_session() as session:
            version = await self._get_version_owned(session, model_id, version_id)
            if version is None:
                raise AppError(
                    code="not_found",
                    message=f"模型版本不存在: {version_id}",
                    retryable=False,
                    fields={"version_id": str(version_id)},
                )
            if version.status != "pending_validation":
                raise AppError(
                    code="invalid_state",
                    message=(f"版本状态非 pending_validation（当前: {version.status}），无法验证"),
                    retryable=False,
                    fields={"status": version.status},
                )

            if metrics is not None:
                version.metrics_json = {
                    **(version.metrics_json or {}),
                    **metrics,
                }
                if dataset_artifact_id is not None:
                    version.metrics_json["dataset_artifact_id"] = str(dataset_artifact_id)
            if applicability_domain is not None:
                version.applicability_domain_json = applicability_domain

            version.status = "validated"
            await session.flush()
            return version

    async def publish(
        self,
        model_id: UUID,
        version_id: UUID,
    ) -> Model:
        """发布模型版本（状态 → published，更新发布指针）。

        将指定版本状态置为 published，更新 model.current_version_id
        指向该版本，并将模型状态置为 published。

        Args:
            model_id: 模型 ID。
            version_id: 版本 ID。

        Returns:
            Model: 更新后的模型实体。

        Raises:
            AppError: code="not_found"，当版本不存在时。
            AppError: code="invalid_state"，当版本状态非 validated 时。
        """
        now = self._clock.now()
        async with self._scoped_session() as session:
            model = await self._get_model_owned(session, model_id)
            if model is None:
                raise AppError(
                    code="not_found",
                    message=f"模型不存在: {model_id}",
                    retryable=False,
                    fields={"model_id": str(model_id)},
                )
            version = await self._get_version_owned(session, model_id, version_id)
            if version is None:
                raise AppError(
                    code="not_found",
                    message=f"模型版本不存在: {version_id}",
                    retryable=False,
                    fields={"version_id": str(version_id)},
                )
            if version.status != "validated":
                raise AppError(
                    code="invalid_state",
                    message=(f"版本状态非 validated（当前: {version.status}），无法发布"),
                    retryable=False,
                    fields={"status": version.status},
                )

            version.status = "published"
            version.published_at = now
            model.current_version_id = version.id
            model.status = "published"
            model.lock_version = model.lock_version + 1
            await session.flush()
            return model

    async def rollback(
        self,
        model_id: UUID,
        target_version_id: UUID,
    ) -> Model:
        """回滚发布指针到指定版本。

        将 model.current_version_id 移动到目标版本。目标版本必须
        状态为 published 或 validated（已验证可重新发布）。

        Args:
            model_id: 模型 ID。
            target_version_id: 目标版本 ID。

        Returns:
            Model: 更新后的模型实体。

        Raises:
            AppError: code="not_found"，当版本不存在时。
            AppError: code="invalid_state"，当目标版本不可回滚时。
        """
        async with self._scoped_session() as session:
            model = await self._get_model_owned(session, model_id)
            if model is None:
                raise AppError(
                    code="not_found",
                    message=f"模型不存在: {model_id}",
                    retryable=False,
                    fields={"model_id": str(model_id)},
                )
            version = await self._get_version_owned(session, model_id, target_version_id)
            if version is None:
                raise AppError(
                    code="not_found",
                    message=f"目标版本不存在: {target_version_id}",
                    retryable=False,
                    fields={"version_id": str(target_version_id)},
                )
            if version.status not in ("published", "validated"):
                raise AppError(
                    code="invalid_state",
                    message=(f"目标版本状态不可回滚（当前: {version.status}）"),
                    retryable=False,
                    fields={"status": version.status},
                )

            # 确保目标版本为 published 状态
            if version.status != "published":
                version.status = "published"
                if version.published_at is None:
                    version.published_at = self._clock.now()

            model.current_version_id = version.id
            model.status = "published"
            model.lock_version = model.lock_version + 1
            await session.flush()
            return model

    async def deprecate(self, model_id: UUID) -> Model:
        """废弃模型（状态 → deprecated）。

        Args:
            model_id: 模型 ID。

        Returns:
            Model: 更新后的模型实体。

        Raises:
            AppError: code="not_found"，当模型不存在时。
        """
        async with self._scoped_session() as session:
            model = await self._get_model_owned(session, model_id)
            if model is None:
                raise AppError(
                    code="not_found",
                    message=f"模型不存在: {model_id}",
                    retryable=False,
                    fields={"model_id": str(model_id)},
                )
            model.status = "deprecated"
            model.lock_version = model.lock_version + 1
            await session.flush()
            return model

    # ---- 预测 ----

    async def predict(
        self,
        model_id: UUID,
        inputs: dict[str, Any],
    ) -> PredictionResult:
        """使用当前发布版本预测。

        获取模型当前发布版本，下载模型工件，构建适配器，
        校验输入与适用域，执行预测，写入 model_execution 事实。

        Args:
            model_id: 模型 ID。
            inputs: 输入参数字典。

        Returns:
            PredictionResult: 预测结果。

        Raises:
            AppError: code="not_found"，当模型或发布版本不存在时。
            AppError: code="invalid_state"，当模型未发布时。
            AppError: code="outside_applicability_domain"，当输入超出适用域时。
        """
        async with self._scoped_session() as session:
            model = await self._get_model_owned(session, model_id)
            if model is None:
                raise AppError(
                    code="not_found",
                    message=f"模型不存在: {model_id}",
                    retryable=False,
                    fields={"model_id": str(model_id)},
                )
            if model.current_version_id is None:
                raise AppError(
                    code="invalid_state",
                    message="模型未发布，无当前版本",
                    retryable=False,
                    fields={"model_id": str(model_id)},
                )
            version_id = model.current_version_id

        return await self.predict_version(version_id, inputs)

    async def predict_version(
        self,
        model_version_id: UUID,
        inputs: dict[str, Any],
    ) -> PredictionResult:
        """使用指定版本预测。

        下载模型工件，构建适配器，校验输入与适用域，
        执行预测，写入 model_execution 事实。

        Args:
            model_version_id: 模型版本 ID。
            inputs: 输入参数字典。

        Returns:
            PredictionResult: 预测结果。

        Raises:
            AppError: code="not_found"，当版本不存在时。
            AppError: code="outside_applicability_domain"，当输入超出适用域时。
        """
        started_at = self._clock.now()

        # 获取版本
        async with self._scoped_session() as session:
            version = await session.scalar(
                sa.select(ModelVersion).where(ModelVersion.id == model_version_id)
            )
            if version is None:
                raise AppError(
                    code="not_found",
                    message=f"模型版本不存在: {model_version_id}",
                    retryable=False,
                    fields={"version_id": str(model_version_id)},
                )
            await self._get_model_owned(session, version.model_id)
            contract_dict: dict[str, Any] = dict(version.contract_json or {})
            applicability_domain: dict[str, Any] = dict(version.applicability_domain_json or {})
            model_artifact_id = version.model_artifact_id
            version_no = version.version

        contract = ModelContract.from_dict(contract_dict)

        # 构建适配器
        adapter = build_adapter(contract)

        # 校验输入
        validation = adapter.validate_input(inputs, contract)
        if not validation.valid:
            raise AppError(
                code="input_validation_failed",
                message="输入校验失败: " + "; ".join(validation.errors),
                retryable=False,
                fields={"errors": list(validation.errors)},
            )

        # 适用域检查
        domain_result = self._applicability_checker.check(inputs, applicability_domain)
        if not domain_result.valid:
            raise AppError(
                code="outside_applicability_domain",
                message="输入超出适用域: " + "; ".join(domain_result.errors),
                retryable=False,
                fields={"errors": list(domain_result.errors)},
            )

        # 下载模型工件
        artifact_bytes: bytes = b""
        if model_artifact_id is not None:
            artifact_bytes = await self._artifact_service.get_bytes(model_artifact_id)

        # 加载并预测
        await adapter.load(artifact_bytes, contract)
        output: ModelOutput = await adapter.predict(inputs)

        ended_at = self._clock.now()
        metadata: dict[str, Any] = {
            "started_at": started_at.isoformat(),
            "ended_at": ended_at.isoformat(),
            "adapter_type": output.metadata.get("adapter_type"),
            "within_applicability_domain": True,
        }

        # 写入 model_execution 事实（F-11: 事实写入失败使执行失败）
        fact_id: UUID | None = None
        if self._fact_service is not None:
            fact_id = await self._write_execution_fact(
                model_id=version.model_id,
                model_version_id=model_version_id,
                version_no=version_no,
                inputs=inputs,
                predictions=output.predictions,
                started_at=started_at,
                ended_at=ended_at,
            )

        return PredictionResult(
            model_id=version.model_id,
            model_version_id=model_version_id,
            version=version_no,
            predictions=dict(output.predictions),
            metadata=metadata,
            fact_id=fact_id,
        )

    # ---- 查询 ----

    async def get_model(self, model_id: UUID) -> Model:
        """获取模型详情。

        Args:
            model_id: 模型 ID。

        Returns:
            Model: 模型实体。

        Raises:
            AppError: code="not_found"，当模型不存在时。
        """
        async with self._scoped_session() as session:
            model = await self._get_model_owned(session, model_id)
            if model is None:
                raise AppError(
                    code="not_found",
                    message=f"模型不存在: {model_id}",
                    retryable=False,
                    fields={"model_id": str(model_id)},
                )
            return model

    async def list_models(self, status: str | None = None) -> list[Model]:
        """列出当前部门的模型。

        Args:
            status: 可选，按状态过滤。

        Returns:
            list[Model]: 模型列表（按 created_at 升序）。
        """
        async with self._scoped_session() as session:
            stmt = sa.select(Model).order_by(Model.created_at)
            if status is not None:
                stmt = stmt.where(Model.status == status)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_versions(self, model_id: UUID) -> list[ModelVersion]:
        """列出模型的所有版本。

        Args:
            model_id: 模型 ID。

        Returns:
            list[ModelVersion]: 版本列表（按 version 升序）。
        """
        async with self._scoped_session() as session:
            result = await session.execute(
                sa.select(ModelVersion)
                .where(ModelVersion.model_id == model_id)
                .order_by(ModelVersion.version)
            )
            return list(result.scalars().all())

    # ---- 内部辅助 ----

    async def _get_model_owned(
        self,
        session: AsyncSession,
        model_id: UUID,
    ) -> Model | None:
        """获取属于当前部门的模型。"""
        return await session.scalar(  # type: ignore[no-any-return]
            sa.select(Model).where(
                Model.id == model_id,
            )
        )

    async def _get_version_owned(
        self,
        session: AsyncSession,
        model_id: UUID,
        version_id: UUID,
    ) -> ModelVersion | None:
        """获取属于指定模型的版本。"""
        return await session.scalar(  # type: ignore[no-any-return]
            sa.select(ModelVersion).where(
                ModelVersion.id == version_id,
                ModelVersion.model_id == model_id,
            )
        )

    async def _write_execution_fact(
        self,
        model_id: UUID,
        model_version_id: UUID,
        version_no: int,
        inputs: dict[str, Any],
        predictions: dict[str, Any],
        started_at: datetime,
        ended_at: datetime,
    ) -> UUID | None:
        """写入 model_execution 事实。

        通过 fact_service 创建一条 model_execution 类型的事实，
        记录模型预测的输入、输出与执行元数据。

        Args:
            model_id: 模型 ID。
            model_version_id: 版本 ID。
            version_no: 版本号。
            inputs: 预测输入。
            predictions: 预测输出。
            started_at: 开始时间。
            ended_at: 结束时间。

        Returns:
            UUID | None: 事实修订 ID，fact_service 不可用时返回 None。

        Raises:
            AppError: 当事实写入失败时（F-11: 事实写入失败使执行失败）。
        """
        if self._fact_service is None:
            return None
        # F-11: 事实写入失败使执行失败（raise 而非吞掉）
        from packages.facts.service import CreateFactCommand

        command = CreateFactCommand(
            fact_type="model_execution",
            department_id=self._dept_id,
            object_id=model_id,
            subject_id=f"model:{model_id}:version:{version_no}",
            started_at=started_at,
            ended_at=ended_at,
            idempotency_key=(f"model-exec-{model_version_id}-{started_at.isoformat()}"),
            created_by=None,
        )
        ref = await self._fact_service.create(command)
        return ref.fact_id  # type: ignore[no-any-return]
