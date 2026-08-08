"""单元测试：ModelService 模型生命周期业务编排。

覆盖：
- create_model：成功 + 冲突（code 已存在）；
- create_version：成功 + 模型不存在 + 版本号递增；
- submit_for_validation：成功 + 状态非 draft；
- validate：成功 + 状态非 pending_validation + metrics 合并；
- publish：成功 + 状态非 validated + 发布指针更新；
- rollback：成功（published/validated）+ 状态不可回滚；
- deprecate：成功 + 模型不存在；
- predict：模型不存在 + 未发布 + 委托 predict_version；
- predict_version：版本不存在 + 输入校验失败 + 适用域超限 + 成功（无/有 fact_service）；
- get_model / list_models / get_versions 查询方法。

使用 patched _scoped_session 注入 mock session。
"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from packages.common.clock import FixedClock
from packages.common.database import ScopedSessionMixin
from packages.common.errors import AppError
from packages.models.contracts import ModelContract, ModelOutput, ValidationResult
from packages.models.service import ModelService, PredictionResult

# ============================================================
# Helpers
# ============================================================


@asynccontextmanager
async def _patch_scoped_session(mock_session: AsyncMock) -> Any:
    """临时替换 ScopedSessionMixin._scoped_session。"""
    original = ScopedSessionMixin._scoped_session

    @asynccontextmanager
    async def fake_scoped_session(self: Any) -> Any:
        yield mock_session

    ScopedSessionMixin._scoped_session = fake_scoped_session  # type: ignore[method-assign]
    try:
        yield
    finally:
        ScopedSessionMixin._scoped_session = original  # type: ignore[method-assign]


def _make_service(
    artifact_service: Any = None,
    fact_service: Any = None,
) -> ModelService:
    return ModelService(
        session_factory=MagicMock(),
        department_id=uuid4(),
        actor_id=uuid4(),
        artifact_service=artifact_service or MagicMock(),
        fact_service=fact_service,
        clock=FixedClock(datetime(2026, 1, 1, tzinfo=UTC)),
    )


def _make_contract(
    name: str = "m",
    version: str = "1.0.0",
    applicability_domain: dict[str, Any] | None = None,
) -> ModelContract:
    return ModelContract(
        name=name,
        version=version,
        input_schema={},
        output_schema={},
        applicability_domain=applicability_domain or {},
    )


def _make_model(model_id: UUID | None = None, status: str = "draft") -> MagicMock:
    m = MagicMock()
    m.id = model_id or uuid4()
    m.code = "test_model"
    m.display_name = "测试模型"
    m.status = status
    m.current_version_id = None
    m.lock_version = 0
    return m


def _make_version(
    version_id: UUID | None = None,
    status: str = "draft",
    version_no: int = 1,
) -> MagicMock:
    v = MagicMock()
    v.id = version_id or uuid4()
    v.model_id = uuid4()
    v.version = version_no
    v.status = status
    v.contract_json = {"name": "m", "version": "1.0.0", "input_schema": {}, "output_schema": {}}
    v.applicability_domain_json = {}
    v.metrics_json = {}
    v.model_artifact_id = None
    v.published_at = None
    return v


# ============================================================
# create_model
# ============================================================


class TestCreateModel:
    """create_model 测试。"""

    async def test_create_success(self) -> None:
        """成功创建模型。"""
        session = AsyncMock()
        session.scalar.return_value = None  # 无冲突
        service = _make_service()

        async with _patch_scoped_session(session):
            model = await service.create_model(code="new_model", display_name="新模型")

        assert model.code == "new_model"
        assert model.status == "draft"
        session.add.assert_called_once()
        session.flush.assert_awaited_once()

    async def test_create_conflict(self) -> None:
        """code 已存在抛 conflict。"""
        session = AsyncMock()
        session.scalar.return_value = _make_model()
        service = _make_service()

        async with _patch_scoped_session(session):
            with pytest.raises(AppError, match="已存在"):
                await service.create_model(code="existing", display_name="x")


# ============================================================
# create_version
# ============================================================


class TestCreateVersion:
    """create_version 测试。"""

    async def test_create_version_success(self) -> None:
        """成功创建版本，版本号递增。"""
        model = _make_model()
        session = AsyncMock()
        # _get_model_owned returns model, then max version query returns 2
        session.scalar.side_effect = [model, 2]
        service = _make_service()

        async with _patch_scoped_session(session):
            version = await service.create_version(
                model_id=model.id,
                contract=_make_contract(),
            )

        assert version.version == 3
        assert version.status == "draft"

    async def test_create_version_model_not_found(self) -> None:
        """模型不存在抛 not_found。"""
        session = AsyncMock()
        session.scalar.return_value = None
        service = _make_service()

        async with _patch_scoped_session(session):
            with pytest.raises(AppError, match="不存在"):
                await service.create_version(model_id=uuid4(), contract=_make_contract())

    async def test_create_version_first_version(self) -> None:
        """首个版本号为 1。"""
        model = _make_model()
        session = AsyncMock()
        session.scalar.side_effect = [model, None]  # max version None
        service = _make_service()

        async with _patch_scoped_session(session):
            version = await service.create_version(
                model_id=model.id,
                contract=_make_contract(),
            )

        assert version.version == 1


# ============================================================
# submit_for_validation
# ============================================================


class TestSubmitForValidation:
    """submit_for_validation 测试。"""

    async def test_submit_success(self) -> None:
        """draft → pending_validation。"""
        version = _make_version(status="draft")
        session = AsyncMock()
        session.scalar.return_value = version
        service = _make_service()

        async with _patch_scoped_session(session):
            result = await service.submit_for_validation(uuid4(), uuid4())

        assert result.status == "pending_validation"

    async def test_submit_version_not_found(self) -> None:
        """版本不存在抛 not_found。"""
        session = AsyncMock()
        session.scalar.return_value = None
        service = _make_service()

        async with _patch_scoped_session(session):
            with pytest.raises(AppError, match="不存在"):
                await service.submit_for_validation(uuid4(), uuid4())

    async def test_submit_wrong_state(self) -> None:
        """非 draft 状态抛 invalid_state。"""
        version = _make_version(status="published")
        session = AsyncMock()
        session.scalar.return_value = version
        service = _make_service()

        async with _patch_scoped_session(session):
            with pytest.raises(AppError, match="状态"):
                await service.submit_for_validation(uuid4(), uuid4())


# ============================================================
# validate
# ============================================================


class TestValidate:
    """validate 测试。"""

    async def test_validate_success_with_metrics(self) -> None:
        """pending_validation → validated，合并 metrics。"""
        version = _make_version(status="pending_validation")
        version.metrics_json = {"r2": 0.8}
        session = AsyncMock()
        session.scalar.return_value = version
        service = _make_service()

        async with _patch_scoped_session(session):
            result = await service.validate(
                uuid4(),
                uuid4(),
                metrics={"rmse": 0.1},
                dataset_artifact_id=uuid4(),
            )

        assert result.status == "validated"
        assert result.metrics_json["r2"] == 0.8
        assert result.metrics_json["rmse"] == 0.1
        assert "dataset_artifact_id" in result.metrics_json

    async def test_validate_success_without_metrics(self) -> None:
        """无 metrics 时仅改状态。"""
        version = _make_version(status="pending_validation")
        session = AsyncMock()
        session.scalar.return_value = version
        service = _make_service()

        async with _patch_scoped_session(session):
            result = await service.validate(uuid4(), uuid4())

        assert result.status == "validated"

    async def test_validate_wrong_state(self) -> None:
        """非 pending_validation 状态抛 invalid_state。"""
        version = _make_version(status="draft")
        session = AsyncMock()
        session.scalar.return_value = version
        service = _make_service()

        async with _patch_scoped_session(session):
            with pytest.raises(AppError, match="状态"):
                await service.validate(uuid4(), uuid4())

    async def test_validate_not_found(self) -> None:
        """版本不存在抛 not_found。"""
        session = AsyncMock()
        session.scalar.return_value = None
        service = _make_service()

        async with _patch_scoped_session(session):
            with pytest.raises(AppError, match="不存在"):
                await service.validate(uuid4(), uuid4())


# ============================================================
# publish
# ============================================================


class TestPublish:
    """publish 测试。"""

    async def test_publish_success(self) -> None:
        """validated → published，更新发布指针。"""
        model = _make_model(status="validated")
        version = _make_version(status="validated")
        session = AsyncMock()
        session.scalar.side_effect = [model, version]
        service = _make_service()

        async with _patch_scoped_session(session):
            result = await service.publish(uuid4(), uuid4())

        assert result.status == "published"
        assert result.current_version_id == version.id
        assert result.lock_version == 1
        assert version.status == "published"
        assert version.published_at is not None

    async def test_publish_model_not_found(self) -> None:
        """模型不存在抛 not_found。"""
        session = AsyncMock()
        session.scalar.return_value = None
        service = _make_service()

        async with _patch_scoped_session(session):
            with pytest.raises(AppError, match="不存在"):
                await service.publish(uuid4(), uuid4())

    async def test_publish_version_not_found(self) -> None:
        """版本不存在抛 not_found。"""
        model = _make_model()
        session = AsyncMock()
        session.scalar.side_effect = [model, None]
        service = _make_service()

        async with _patch_scoped_session(session):
            with pytest.raises(AppError, match="不存在"):
                await service.publish(uuid4(), uuid4())

    async def test_publish_wrong_state(self) -> None:
        """非 validated 状态抛 invalid_state。"""
        model = _make_model()
        version = _make_version(status="draft")
        session = AsyncMock()
        session.scalar.side_effect = [model, version]
        service = _make_service()

        async with _patch_scoped_session(session):
            with pytest.raises(AppError, match="状态"):
                await service.publish(uuid4(), uuid4())


# ============================================================
# rollback
# ============================================================


class TestRollback:
    """rollback 测试。"""

    async def test_rollback_to_published_version(self) -> None:
        """回滚到已发布版本。"""
        model = _make_model()
        version = _make_version(status="published")
        version.id = uuid4()
        session = AsyncMock()
        session.scalar.side_effect = [model, version]
        service = _make_service()

        async with _patch_scoped_session(session):
            result = await service.rollback(uuid4(), version.id)

        assert result.current_version_id == version.id
        assert result.status == "published"

    async def test_rollback_to_validated_promotes_to_published(self) -> None:
        """回滚到 validated 版本时提升为 published。"""
        model = _make_model()
        version = _make_version(status="validated")
        version.published_at = None
        session = AsyncMock()
        session.scalar.side_effect = [model, version]
        service = _make_service()

        async with _patch_scoped_session(session):
            await service.rollback(uuid4(), version.id)

        assert version.status == "published"
        assert version.published_at is not None

    async def test_rollback_model_not_found(self) -> None:
        """模型不存在抛 not_found。"""
        session = AsyncMock()
        session.scalar.return_value = None
        service = _make_service()

        async with _patch_scoped_session(session):
            with pytest.raises(AppError, match="不存在"):
                await service.rollback(uuid4(), uuid4())

    async def test_rollback_version_not_found(self) -> None:
        """版本不存在抛 not_found。"""
        model = _make_model()
        session = AsyncMock()
        session.scalar.side_effect = [model, None]
        service = _make_service()

        async with _patch_scoped_session(session):
            with pytest.raises(AppError, match="不存在"):
                await service.rollback(uuid4(), uuid4())

    async def test_rollback_invalid_state(self) -> None:
        """目标版本状态不可回滚抛 invalid_state。"""
        model = _make_model()
        version = _make_version(status="draft")
        session = AsyncMock()
        session.scalar.side_effect = [model, version]
        service = _make_service()

        async with _patch_scoped_session(session):
            with pytest.raises(AppError, match="状态"):
                await service.rollback(uuid4(), uuid4())


# ============================================================
# deprecate
# ============================================================


class TestDeprecate:
    """deprecate 测试。"""

    async def test_deprecate_success(self) -> None:
        """成功废弃模型。"""
        model = _make_model(status="published")
        model.lock_version = 3
        session = AsyncMock()
        session.scalar.return_value = model
        service = _make_service()

        async with _patch_scoped_session(session):
            result = await service.deprecate(uuid4())

        assert result.status == "deprecated"
        assert result.lock_version == 4

    async def test_deprecate_not_found(self) -> None:
        """模型不存在抛 not_found。"""
        session = AsyncMock()
        session.scalar.return_value = None
        service = _make_service()

        async with _patch_scoped_session(session):
            with pytest.raises(AppError, match="不存在"):
                await service.deprecate(uuid4())


# ============================================================
# predict / predict_version
# ============================================================


class TestPredict:
    """predict / predict_version 测试。"""

    async def test_predict_model_not_found(self) -> None:
        """predict 模型不存在抛 not_found。"""
        session = AsyncMock()
        session.scalar.return_value = None
        service = _make_service()

        async with _patch_scoped_session(session):
            with pytest.raises(AppError, match="不存在"):
                await service.predict(uuid4(), {"x": 1})

    async def test_predict_no_current_version(self) -> None:
        """predict 模型未发布抛 invalid_state。"""
        model = _make_model()
        model.current_version_id = None
        session = AsyncMock()
        session.scalar.return_value = model
        service = _make_service()

        async with _patch_scoped_session(session):
            with pytest.raises(AppError, match="未发布"):
                await service.predict(uuid4(), {"x": 1})

    async def test_predict_version_not_found(self) -> None:
        """predict_version 版本不存在抛 not_found。"""
        session = AsyncMock()
        session.scalar.return_value = None
        service = _make_service()

        async with _patch_scoped_session(session):
            with pytest.raises(AppError, match="不存在"):
                await service.predict_version(uuid4(), {"x": 1})

    async def test_predict_version_input_validation_failed(self) -> None:
        """predict_version 输入校验失败抛 input_validation_failed。"""
        version = _make_version()
        model = _make_model()
        session = AsyncMock()
        session.scalar.side_effect = [version, model]
        service = _make_service()

        async with _patch_scoped_session(session):
            with (
                patch(
                    "packages.models.service.ModelContract.from_dict",
                    return_value=_make_contract(),
                ),
                patch("packages.models.service.build_adapter") as mock_build,
            ):
                mock_adapter = MagicMock()
                mock_adapter.validate_input.return_value = ValidationResult(
                    valid=False, errors=("bad input",)
                )
                mock_build.return_value = mock_adapter
                with pytest.raises(AppError, match="输入校验失败"):
                    await service.predict_version(uuid4(), {"x": 1})

    async def test_predict_version_outside_applicability_domain(self) -> None:
        """predict_version 输入超出适用域抛 outside_applicability_domain。"""
        version = _make_version()
        version.applicability_domain_json = {"x": {"min": 0.0, "max": 10.0}}
        model = _make_model()
        session = AsyncMock()
        session.scalar.side_effect = [version, model]
        service = _make_service()

        async with _patch_scoped_session(session):
            with (
                patch(
                    "packages.models.service.ModelContract.from_dict",
                    return_value=_make_contract(),
                ),
                patch("packages.models.service.build_adapter") as mock_build,
            ):
                mock_adapter = MagicMock()
                mock_adapter.validate_input.return_value = ValidationResult(valid=True, errors=())
                mock_build.return_value = mock_adapter
                with pytest.raises(AppError, match="超出适用域"):
                    await service.predict_version(uuid4(), {"x": 999.0})

    async def test_predict_version_success_without_fact_service(self) -> None:
        """predict_version 成功（无 fact_service，fact_id 为 None）。"""
        version = _make_version(version_no=2)
        version.model_artifact_id = uuid4()
        model = _make_model()
        session = AsyncMock()
        session.scalar.side_effect = [version, model]
        artifact_service = MagicMock()
        artifact_service.get_bytes = AsyncMock(return_value=b"artifact")

        service = _make_service(artifact_service=artifact_service, fact_service=None)

        async with _patch_scoped_session(session):
            with (
                patch(
                    "packages.models.service.ModelContract.from_dict",
                    return_value=_make_contract(),
                ),
                patch("packages.models.service.build_adapter") as mock_build,
            ):
                mock_adapter = MagicMock()
                mock_adapter.validate_input.return_value = ValidationResult(valid=True, errors=())
                mock_adapter.load = AsyncMock()
                mock_adapter.predict = AsyncMock(
                    return_value=ModelOutput(
                        predictions={"y": 1.5}, metadata={"adapter_type": "python"}
                    )
                )
                mock_build.return_value = mock_adapter
                result = await service.predict_version(uuid4(), {"x": 5.0})

        assert isinstance(result, PredictionResult)
        assert result.version == 2
        assert result.predictions == {"y": 1.5}
        assert result.fact_id is None
        assert result.metadata["within_applicability_domain"] is True

    async def test_predict_version_success_with_fact_service(self) -> None:
        """predict_version 成功（有 fact_service，写入事实）。"""
        version = _make_version(version_no=1)
        version.model_artifact_id = None
        model = _make_model()
        session = AsyncMock()
        session.scalar.side_effect = [version, model]

        fact_ref = MagicMock()
        fact_ref.fact_id = uuid4()
        fact_service = MagicMock()
        fact_service.create = AsyncMock(return_value=fact_ref)

        service = _make_service(fact_service=fact_service)

        async with _patch_scoped_session(session):
            with (
                patch(
                    "packages.models.service.ModelContract.from_dict",
                    return_value=_make_contract(),
                ),
                patch("packages.models.service.build_adapter") as mock_build,
            ):
                mock_adapter = MagicMock()
                mock_adapter.validate_input.return_value = ValidationResult(valid=True, errors=())
                mock_adapter.load = AsyncMock()
                mock_adapter.predict = AsyncMock(
                    return_value=ModelOutput(predictions={"y": 2.0}, metadata={})
                )
                mock_build.return_value = mock_adapter
                result = await service.predict_version(uuid4(), {"x": 5.0})

        assert result.fact_id == fact_ref.fact_id
        fact_service.create.assert_awaited_once()


# ============================================================
# query methods
# ============================================================


class TestQueryMethods:
    """get_model / list_models / get_versions 测试。"""

    async def test_get_model_success(self) -> None:
        """get_model 成功返回。"""
        model = _make_model()
        session = AsyncMock()
        session.scalar.return_value = model
        service = _make_service()

        async with _patch_scoped_session(session):
            result = await service.get_model(uuid4())

        assert result is model

    async def test_get_model_not_found(self) -> None:
        """get_model 不存在抛 not_found。"""
        session = AsyncMock()
        session.scalar.return_value = None
        service = _make_service()

        async with _patch_scoped_session(session):
            with pytest.raises(AppError, match="不存在"):
                await service.get_model(uuid4())

    async def test_list_models(self) -> None:
        """list_models 返回模型列表。"""
        models = [_make_model(), _make_model()]
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = models
        session = AsyncMock()
        session.execute = AsyncMock(return_value=result_mock)
        service = _make_service()

        async with _patch_scoped_session(session):
            result = await service.list_models()

        assert result == models

    async def test_list_models_with_status_filter(self) -> None:
        """list_models 带 status 过滤。"""
        models = [_make_model(status="published")]
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = models
        session = AsyncMock()
        session.execute = AsyncMock(return_value=result_mock)
        service = _make_service()

        async with _patch_scoped_session(session):
            result = await service.list_models(status="published")

        assert len(result) == 1

    async def test_get_versions(self) -> None:
        """get_versions 返回版本列表。"""
        versions = [_make_version(version_no=1), _make_version(version_no=2)]
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = versions
        session = AsyncMock()
        session.execute = AsyncMock(return_value=result_mock)
        service = _make_service()

        async with _patch_scoped_session(session):
            result = await service.get_versions(uuid4())

        assert len(result) == 2


class TestActorId:
    """actor_id 属性测试。"""

    def test_actor_id_property(self) -> None:
        """actor_id 属性返回构造时传入的值。"""
        actor = uuid4()
        service = _make_service()
        service = ModelService(
            session_factory=MagicMock(),
            department_id=uuid4(),
            actor_id=actor,
            artifact_service=MagicMock(),
        )
        assert service.actor_id == actor
