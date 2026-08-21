"""模型生命周期集成测试（IRIP V2-T04）。

验证模型从创建→验证→发布→预测→回滚的完整生命周期：
- 创建模型 + 版本；
- 提交验证 → 验证 → 发布；
- 使用当前发布版本预测；
- 回滚发布指针；
- 适用域检查拒绝超出范围的输入；
- 预测结果写入 model_execution 事实。

需要数据库（model / model_version 表）。未设置
``IRIP_TEST_DATABASE_URL`` 时自动 skip。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio

from packages.common.errors import AppError
from packages.models.contracts import ModelContract
from packages.models.service import ModelService

_ONNX_AVAILABLE: bool = True
try:
    import onnx  # noqa: F401
    import onnxruntime  # noqa: F401
except ImportError:
    _ONNX_AVAILABLE = False


#: 测试用模型契约（2 输入 → 2 输出）。
_TEST_CONTRACT_DICT: dict = {
    "name": "integration_test_model",
    "version": "1.0.0",
    "input_schema": {
        "type": "object",
        "required": ["x", "y"],
        "properties": {
            "x": {"type": "number", "minimum": 0, "maximum": 100},
            "y": {"type": "number", "minimum": 0, "maximum": 100},
        },
        "additionalProperties": False,
    },
    "output_schema": {
        "type": "object",
        "required": ["sum", "product"],
        "properties": {
            "sum": {"type": "number"},
            "product": {"type": "number"},
        },
        "additionalProperties": False,
    },
    "applicability_domain": {
        "x": {"min": 0.0, "max": 100.0},
        "y": {"min": 0.0, "max": 100.0},
    },
    "executor": {"type": "onnx", "timeout_seconds": 60},
}


class _FakeArtifactService:
    """模拟工件服务：get_bytes 返回预置的模型字节。"""

    def __init__(self, model_bytes: bytes) -> None:
        """初始化模拟工件服务。

        Args:
            model_bytes: 预置的模型工件字节。
        """
        self._model_bytes = model_bytes
        self._uploaded: dict[UUID, bytes] = {}

    async def put_bytes(
        self,
        data: bytes,
        media_type: str,
        filename: str,
    ) -> Any:
        """模拟上传，返回含 artifact_id 的伪 ArtifactRef。"""
        from packages.common.artifacts import ArtifactRef

        artifact_id = UUID("00000000-0000-0000-0000-000000000001")
        self._uploaded[artifact_id] = data
        return ArtifactRef(
            artifact_id=artifact_id,
            object_key="sha256/ab/test",
            sha256="test",
            media_type=media_type,
            size_bytes=len(data),
        )

    async def get_bytes(self, artifact_id: UUID) -> bytes:
        """模拟下载，返回预置的模型字节。"""
        return self._model_bytes


class _FakeFactService:
    """模拟事实服务：记录 create 调用。"""

    def __init__(self) -> None:
        """初始化模拟事实服务。"""
        self.calls: list[Any] = []

    async def create(self, command: Any) -> Any:
        """模拟创建事实，返回含 fact_id 的伪引用。"""
        from packages.facts.observations import FactRef

        self.calls.append(command)
        return FactRef(
            fact_id=UUID("00000000-0000-0000-0000-000000000002"),
            fact_type=command.fact_type,
            subject_id=command.subject_id,
            status="active",
        )


def _build_onnx_model() -> bytes:
    """构建一个微型确定性 ONNX 模型（x,y 两输入 -> sum,product 两输出）。

    sum = x + y，product = x * y。需 onnx 包。

    Returns:
        bytes: 序列化的 ONNX 模型字节。
    """
    from onnx import TensorProto, helper

    x_in = helper.make_tensor_value_info("x", TensorProto.FLOAT, [None, 1])
    y_in = helper.make_tensor_value_info("y", TensorProto.FLOAT, [None, 1])
    s_out = helper.make_tensor_value_info("sum", TensorProto.FLOAT, [None, 1])
    p_out = helper.make_tensor_value_info("product", TensorProto.FLOAT, [None, 1])
    add_node = helper.make_node("Add", ["x", "y"], ["sum"])
    mul_node = helper.make_node("Mul", ["x", "y"], ["product"])
    graph = helper.make_graph(
        [add_node, mul_node], "tiny", [x_in, y_in], [s_out, p_out]
    )
    model = helper.make_model(graph)
    model.opset_import[0].opset = 13
    return model.SerializeToString()


@pytest_asyncio.fixture
async def model_service(
    async_session_factory: Any,
    test_user: object,
) -> Any:
    """构建 ModelService 实例（含模拟工件服务与事实服务）。

    Args:
        async_session_factory: 异步会话工厂 fixture。
        test_user: 测试用户 fixture（提供 user_id 作为 actor_id）。

    Returns:
        ModelService: 模型服务实例。
    """
    if not _ONNX_AVAILABLE:
        pytest.skip("onnx/onnxruntime 未安装")

    org_id = test_user.department_id  # type: ignore[attr-defined]
    model_bytes = _build_onnx_model()
    artifact_service = _FakeArtifactService(model_bytes)
    fact_service = _FakeFactService()
    service = ModelService(
        session_factory=async_session_factory,
        department_id=org_id,
        actor_id=test_user.user_id,  # type: ignore[attr-defined]
        artifact_service=artifact_service,
        fact_service=fact_service,
    )
    # 暴露 fact_service 供断言
    service._fake_fact_service = fact_service  # type: ignore[attr-defined]
    return service


@pytest.fixture
def test_contract() -> ModelContract:
    """构建测试模型契约。"""
    return ModelContract.from_dict(_TEST_CONTRACT_DICT)


@pytest.mark.integration
class TestModelLifecycle:
    """模型生命周期集成测试。"""

    @pytest.mark.asyncio
    async def test_create_validate_publish_predict_rollback(
        self,
        model_service: Any,
        test_contract: ModelContract,
    ) -> None:
        """完整生命周期：创建→验证→发布→预测→回滚。"""

        # 1. 创建模型
        model = await model_service.create_model(
            code=f"grate_cooler_test_{uuid4().hex[:8]}", display_name="篦冷机测试模型"
        )
        assert model.status == "draft"
        assert model.code.startswith("grate_cooler_test")
        model_id = model.id

        # 2. 创建版本（带模型工件 ID）
        artifact_id = UUID("00000000-0000-0000-0000-000000000001")
        version = await model_service.create_version(
            model_id=model_id,
            contract=test_contract,
            model_artifact_id=artifact_id,
            metrics={"r2": 0.95},
        )
        assert version.status == "draft"
        assert version.version == 1
        version_id = version.id

        # 3. 提交验证
        submitted = await model_service.submit_for_validation(model_id, version_id)
        assert submitted.status == "pending_validation"

        # 4. 验证
        validated = await model_service.validate(
            model_id=model_id,
            version_id=version_id,
            metrics={"r2": 0.95, "rmse": 1.2},
            applicability_domain=test_contract.applicability_domain,
        )
        assert validated.status == "validated"

        # 5. 发布
        published_model = await model_service.publish(model_id, version_id)
        assert published_model.status == "published"
        assert published_model.current_version_id == version_id

        # 6. 预测（使用当前发布版本）
        result = await model_service.predict(model_id, {"x": 10.0, "y": 20.0})
        assert result.model_id == model_id
        assert result.model_version_id == version_id
        assert "sum" in result.predictions
        assert "product" in result.predictions

        # 7. 事实已写入
        assert result.fact_id is not None
        fact_service = model_service._fake_fact_service
        assert len(fact_service.calls) == 1
        assert fact_service.calls[0].fact_type == "model_execution"

        # 8. 回滚（回退到同一版本，验证指针可移动）
        rolled = await model_service.rollback(model_id, version_id)
        assert rolled.current_version_id == version_id
        assert rolled.status == "published"

    @pytest.mark.asyncio
    async def test_applicability_domain_rejects(
        self,
        model_service: Any,
        test_contract: ModelContract,
    ) -> None:
        """适用域检查拒绝超出范围的输入。"""
        # 创建模型 + 版本 + 验证 + 发布
        model = await model_service.create_model(
            code=f"applicability_test_{uuid4().hex[:8]}", display_name="适用域测试模型"
        )
        model_id = model.id
        artifact_id = UUID("00000000-0000-0000-0000-000000000001")
        version = await model_service.create_version(
            model_id=model_id,
            contract=test_contract,
            model_artifact_id=artifact_id,
        )
        version_id = version.id
        await model_service.submit_for_validation(model_id, version_id)
        await model_service.validate(
            model_id=model_id,
            version_id=version_id,
            applicability_domain={"x": {"min": 0.0, "max": 50.0}, "y": {"min": 0.0, "max": 50.0}},
        )
        await model_service.publish(model_id, version_id)

        # 超出适用域但在 JSON schema 范围内（x max=100 in schema, max=50 in domain）
        with pytest.raises(AppError) as exc_info:
            await model_service.predict(model_id, {"x": 75.0, "y": 20.0})
        assert exc_info.value.code == "outside_applicability_domain"

    @pytest.mark.asyncio
    async def test_predict_writes_fact(
        self,
        model_service: Any,
        test_contract: ModelContract,
    ) -> None:
        """预测结果写入 model_execution 事实。"""
        model = await model_service.create_model(
            code=f"fact_test_{uuid4().hex[:8]}", display_name="事实测试模型"
        )
        model_id = model.id
        artifact_id = UUID("00000000-0000-0000-0000-000000000001")
        version = await model_service.create_version(
            model_id=model_id,
            contract=test_contract,
            model_artifact_id=artifact_id,
        )
        version_id = version.id
        await model_service.submit_for_validation(model_id, version_id)
        await model_service.validate(
            model_id=model_id,
            version_id=version_id,
            applicability_domain=test_contract.applicability_domain,
        )
        await model_service.publish(model_id, version_id)

        # 预测前无事实调用
        fact_service = model_service._fake_fact_service
        assert len(fact_service.calls) == 0

        # 预测
        result = await model_service.predict(model_id, {"x": 5.0, "y": 15.0})

        # 预测后事实已写入
        assert result.fact_id is not None
        assert len(fact_service.calls) == 1
        fact_command = fact_service.calls[0]
        assert fact_command.fact_type == "model_execution"
        assert "version" in fact_command.subject_id

    @pytest.mark.asyncio
    async def test_deprecate_model(
        self,
        model_service: Any,
        test_contract: ModelContract,
    ) -> None:
        """废弃模型。"""
        model = await model_service.create_model(
            code=f"deprecate_test_{uuid4().hex[:8]}", display_name="废弃测试模型"
        )
        model_id = model.id
        deprecated = await model_service.deprecate(model_id)
        assert deprecated.status == "deprecated"

    @pytest.mark.asyncio
    async def test_list_and_get_versions(
        self,
        model_service: Any,
        test_contract: ModelContract,
    ) -> None:
        """列表查询与版本列表。"""
        model = await model_service.create_model(
            code=f"list_test_{uuid4().hex[:8]}", display_name="列表测试模型"
        )
        model_id = model.id

        # 创建两个版本
        v1 = await model_service.create_version(model_id=model_id, contract=test_contract)
        v2 = await model_service.create_version(model_id=model_id, contract=test_contract)
        assert v1.version == 1
        assert v2.version == 2

        # 列表查询
        models = await model_service.list_models()
        assert any(m.id == model_id for m in models)

        # 版本列表
        versions = await model_service.get_versions(model_id)
        assert len(versions) == 2
        assert versions[0].version == 1
        assert versions[1].version == 2

    @pytest.mark.asyncio
    async def test_invalid_state_transitions(
        self,
        model_service: Any,
        test_contract: ModelContract,
    ) -> None:
        """非法状态转换被拒绝。"""
        model = await model_service.create_model(
            code=f"state_test_{uuid4().hex[:8]}", display_name="状态测试模型"
        )
        model_id = model.id
        version = await model_service.create_version(model_id=model_id, contract=test_contract)
        version_id = version.id

        # 未提交验证直接发布应失败
        with pytest.raises(AppError) as exc_info:
            await model_service.publish(model_id, version_id)
        assert exc_info.value.code == "invalid_state"

        # 未验证直接再次提交验证应失败（已是 pending_validation）
        await model_service.submit_for_validation(model_id, version_id)
        with pytest.raises(AppError) as exc_info2:
            await model_service.submit_for_validation(model_id, version_id)
        assert exc_info2.value.code == "invalid_state"
