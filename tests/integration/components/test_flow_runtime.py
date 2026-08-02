"""流程运行时集成测试（IRIP V2-T03）。

验证：
- 流程执行成功
- 节点恢复（跳过已成功节点）
- 节点取消
- 节点重试
- output_digest 一致性

依赖数据库（需设置 IRIP_TEST_DATABASE_URL，未设置时 skip）。
fixture async_session_factory / test_user 由 tests/conftest.py 提供。
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.clock import FixedClock
from packages.common.errors import AppError
from packages.components.flow_runtime import (
    FlowRuntimeService,
)
from packages.components.flows import (
    FlowEdge,
    FlowNode,
)
from packages.components.manifest import ComponentManifest
from packages.components.registry import ComponentRegistryService
from packages.components.runner import PythonComponentRunner
from packages.components.sdk import (
    ComponentContext,
    ComponentResult,
    PortSpec,
)

#: JSON Schema 路径（相对项目根目录）。
SCHEMA_PATH: Path = (
    Path(__file__).resolve().parents[3] / "schemas" / "component-manifest" / "v1.schema.json"
)

#: 有效清单 YAML — echo 组件 v1（输入 dataset → 输出 dataset）。
ECHO_YAML: str = """\
name: echo_flow
version: 1.0.0
kind: transform
runtime: python
inputs:
  - name: data
    data_type: dataset
outputs:
  - name: data
    data_type: dataset
"""

#: 有效清单 YAML — source 组件 v1（无输入 → 输出 dataset）。
SOURCE_YAML: str = """\
name: flow_source
version: 1.0.0
kind: ingestion
runtime: python
outputs:
  - name: data
    data_type: dataset
"""


class EchoComponent:
    """测试用 echo 组件：直接透传输入到输出。"""

    async def execute(
        self,
        context: ComponentContext,
        params: dict,
    ) -> ComponentResult:
        return ComponentResult(
            outputs={"data": params.get("value", "default")},
            summary="echo executed",
            metadata={"params": params},
        )


class FailingComponent:
    """测试用失败组件：总是抛出异常。"""

    async def execute(
        self,
        context: ComponentContext,
        params: dict,
    ) -> ComponentResult:
        raise RuntimeError("intentional failure for testing")


class FlakyComponent:
    """测试用不稳定组件：首次失败，重试后成功。"""

    _call_count: int = 0

    async def execute(
        self,
        context: ComponentContext,
        params: dict,
    ) -> ComponentResult:
        type(self)._call_count += 1
        if type(self)._call_count == 1:
            raise RuntimeError("first attempt fails")
        return ComponentResult(
            outputs={"data": "recovered"},
            summary="flaky succeeded on retry",
            metadata={"attempt": type(self)._call_count},
        )


@pytest.fixture
def fixed_clock() -> FixedClock:
    """固定时钟（确定性测试）。"""
    return FixedClock(instant=datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC))


@pytest.fixture
def runner() -> PythonComponentRunner:
    """Python 组件运行器（组件在 _publish_components 中动态注册）。"""
    return PythonComponentRunner()


@pytest.fixture
def failing_runner() -> PythonComponentRunner:
    """Python 组件运行器（注册失败组件）。"""
    r = PythonComponentRunner()

    failing_manifest = ComponentManifest(
        name="failing_comp",
        display_name="failing_comp",
        version="1.0.0",
        kind="transform",
        runtime="python",
        inputs=(PortSpec(name="data", data_type="dataset"),),
        outputs=(PortSpec(name="data", data_type="dataset"),),
        parameters={},
        dependencies=(),
        raw_yaml="name: failing_comp\nversion: 1.0.0\nkind: transform\nruntime: python",
        sha256="",
    )
    r.register(failing_manifest, FailingComponent())
    return r


@pytest.fixture
async def registry_service(
    async_session_factory: async_sessionmaker[AsyncSession],
    test_user: object,
    fixed_clock: FixedClock,
) -> ComponentRegistryService:
    """组件注册表服务。"""
    return ComponentRegistryService(
        session_factory=async_session_factory,
        department_id=test_user.department_id,  # type: ignore[attr-defined]
        actor_id=test_user.user_id,  # type: ignore[attr-defined]
        clock=fixed_clock,
    )


class MockJobService:
    """Mock 作业服务（避免依赖完整 job 系统）。"""

    def __init__(self) -> None:
        self._counter: int = 0

    async def accept(
        self,
        kind: str,
        payload: dict,
        idempotency_key: str,
    ) -> object:
        from packages.common.ids import new_id
        from packages.jobs.entities import JobRef, JobStatus

        self._counter += 1
        return JobRef(
            job_id=new_id(),
            status=JobStatus.ACCEPTED,
            kind=kind,
        )


@pytest.fixture
def job_service() -> MockJobService:
    """Mock 作业服务。"""
    return MockJobService()


@pytest.fixture
async def flow_service(
    async_session_factory: async_sessionmaker[AsyncSession],
    test_user: object,
    registry_service: ComponentRegistryService,
    runner: PythonComponentRunner,
    job_service: MockJobService,
    fixed_clock: FixedClock,
) -> FlowRuntimeService:
    """流程运行时服务。"""
    return FlowRuntimeService(
        session_factory=async_session_factory,
        department_id=test_user.department_id,  # type: ignore[attr-defined]
        actor_id=test_user.user_id,  # type: ignore[attr-defined]
        registry=registry_service,
        runner=runner,
        job_service=job_service,
        clock=fixed_clock,
    )


async def _publish_components(
    registry: ComponentRegistryService,
    runner: PythonComponentRunner,
) -> dict[str, str]:
    """发布测试用组件到注册表并在 runner 中注册实现。

    publish() 会自动生成组件编码（iface_ 前缀），需在发布后获取实际编码，
    并在 runner 中按该编码注册组件实现，以便执行时能找到。

    Returns:
        dict[str, str]: ``{原始名称: 自动生成编码}`` 映射。
    """
    from packages.components.manifest import ManifestValidator

    validator = ManifestValidator(SCHEMA_PATH)

    # 发布 source 组件
    v1 = await registry.publish(validator.validate(SOURCE_YAML))
    comp1, _ = await registry.get_version_by_id(v1.id)

    # 发布 echo 组件
    v2 = await registry.publish(validator.validate(ECHO_YAML))
    comp2, _ = await registry.get_version_by_id(v2.id)

    # 在 runner 中注册实现（使用自动生成的编码）
    runner.register(
        ComponentManifest(
            name=comp1.name,
            display_name=comp1.name,
            version=v1.version,
            kind="ingestion",
            runtime="python",
            inputs=(),
            outputs=(PortSpec(name="data", data_type="dataset"),),
            parameters={},
            dependencies=(),
            raw_yaml=v1.manifest_yaml,
            sha256=v1.manifest_sha256,
        ),
        EchoComponent(),
    )

    runner.register(
        ComponentManifest(
            name=comp2.name,
            display_name=comp2.name,
            version=v2.version,
            kind="transform",
            runtime="python",
            inputs=(PortSpec(name="data", data_type="dataset"),),
            outputs=(PortSpec(name="data", data_type="dataset"),),
            parameters={},
            dependencies=(),
            raw_yaml=v2.manifest_yaml,
            sha256=v2.manifest_sha256,
        ),
        EchoComponent(),
    )

    return {
        "flow_source": comp1.name,
        "echo_flow": comp2.name,
    }


@pytest.mark.asyncio
class TestFlowExecution:
    """流程执行测试。"""

    async def test_flow_execution_succeeds(
        self,
        flow_service: FlowRuntimeService,
        registry_service: ComponentRegistryService,
        runner: PythonComponentRunner,
    ) -> None:
        """流程执行成功：创建 → 发布 → 执行。"""
        name_map = await _publish_components(registry_service, runner)

        # 1. 创建流程定义
        definition = await flow_service.create_definition(
            code="test_flow_1",
            display_name="Test Flow 1",
        )
        assert definition.status == "draft"
        assert definition.code == "test_flow_1"

        # 2. 发布版本
        nodes = (
            FlowNode(
                node_id="source",
                component_name=name_map["flow_source"],
                component_version="1.0.0",
                params={"value": "hello"},
            ),
            FlowNode(
                node_id="echo",
                component_name=name_map["echo_flow"],
                component_version="1.0.0",
                input_bindings={"data": "source:data"},
            ),
        )
        edges = (FlowEdge("source", "data", "echo", "data"),)
        version = await flow_service.publish_version(
            flow_definition_id=definition.id,
            nodes=nodes,
            edges=edges,
            random_seed=42,
        )
        assert version.version == 1
        assert version.status == "published"
        assert version.digest != ""

        # 3. 创建执行
        run = await flow_service.create_run(
            flow_version_id=version.id,
            inputs={"test_key": "test_value"},
        )
        assert run.status == "pending"
        assert run.job_id is not None

        # 4. 执行
        await flow_service.execute(run.id)

        # 5. 验证结果
        run_result, executions = await flow_service.get_run(run.id)
        assert run_result.status == "succeeded"
        assert run_result.output_digest is not None
        assert run_result.completed_at is not None
        assert len(executions) == 2
        for exec_record in executions:
            assert exec_record.status == "succeeded"
            assert exec_record.duration_ms is not None

    async def test_output_digest_consistency(
        self,
        flow_service: FlowRuntimeService,
        registry_service: ComponentRegistryService,
        runner: PythonComponentRunner,
    ) -> None:
        """相同输入和版本产生相同的 output_digest。"""
        name_map = await _publish_components(registry_service, runner)

        definition = await flow_service.create_definition(
            code="test_flow_digest",
            display_name="Digest Test",
        )

        nodes = (
            FlowNode(
                node_id="source",
                component_name=name_map["flow_source"],
                component_version="1.0.0",
                params={"value": "consistency_test"},
            ),
        )
        edges: tuple[FlowEdge, ...] = ()
        version = await flow_service.publish_version(
            flow_definition_id=definition.id,
            nodes=nodes,
            edges=edges,
            random_seed=0,
        )

        # 执行两次
        run1 = await flow_service.create_run(
            flow_version_id=version.id,
            inputs={"key": "value"},
        )
        await flow_service.execute(run1.id)

        run2 = await flow_service.create_run(
            flow_version_id=version.id,
            inputs={"key": "value"},
        )
        await flow_service.execute(run2.id)

        # 验证摘要一致
        result1, _ = await flow_service.get_run(run1.id)
        result2, _ = await flow_service.get_run(run2.id)

        assert result1.output_digest is not None
        assert result2.output_digest is not None
        assert result1.output_digest == result2.output_digest


@pytest.mark.asyncio
class TestFlowResume:
    """流程恢复测试。"""

    async def test_resume_skips_succeeded_nodes(
        self,
        flow_service: FlowRuntimeService,
        registry_service: ComponentRegistryService,
        runner: PythonComponentRunner,
    ) -> None:
        """恢复执行时跳过已成功节点。"""
        name_map = await _publish_components(registry_service, runner)

        definition = await flow_service.create_definition(
            code="test_flow_resume",
            display_name="Resume Test",
        )

        nodes = (
            FlowNode(
                node_id="source",
                component_name=name_map["flow_source"],
                component_version="1.0.0",
                params={"value": "resume_test"},
            ),
            FlowNode(
                node_id="echo",
                component_name=name_map["echo_flow"],
                component_version="1.0.0",
                input_bindings={"data": "source:data"},
            ),
        )
        edges = (FlowEdge("source", "data", "echo", "data"),)
        version = await flow_service.publish_version(
            flow_definition_id=definition.id,
            nodes=nodes,
            edges=edges,
        )

        run = await flow_service.create_run(
            flow_version_id=version.id,
            inputs={},
        )
        await flow_service.execute(run.id)

        # 验证初始成功
        run_result, executions = await flow_service.get_run(run.id)
        assert run_result.status == "succeeded"
        assert len(executions) == 2

        # 恢复执行（应跳过已成功节点）
        await flow_service.resume(run.id)

        # 验证没有新增执行记录
        run_result2, executions2 = await flow_service.get_run(run.id)
        assert run_result2.status == "succeeded"
        assert len(executions2) == 2  # 没有新增


@pytest.mark.asyncio
class TestFlowCancel:
    """流程取消测试。"""

    async def test_cancel_pending_run(
        self,
        flow_service: FlowRuntimeService,
        registry_service: ComponentRegistryService,
        runner: PythonComponentRunner,
    ) -> None:
        """取消处于 pending 状态的执行。"""
        name_map = await _publish_components(registry_service, runner)

        definition = await flow_service.create_definition(
            code="test_flow_cancel",
            display_name="Cancel Test",
        )

        nodes = (
            FlowNode(
                node_id="source",
                component_name=name_map["flow_source"],
                component_version="1.0.0",
                params={"value": "cancel_test"},
            ),
        )
        edges: tuple[FlowEdge, ...] = ()
        version = await flow_service.publish_version(
            flow_definition_id=definition.id,
            nodes=nodes,
            edges=edges,
        )

        run = await flow_service.create_run(
            flow_version_id=version.id,
            inputs={},
        )
        assert run.status == "pending"

        # 取消
        cancelled_run = await flow_service.cancel(run.id)
        assert cancelled_run.status == "cancelled"


@pytest.mark.asyncio
class TestFlowRetry:
    """节点重试测试。"""

    async def test_retry_succeeded_node_fails(
        self,
        flow_service: FlowRuntimeService,
        registry_service: ComponentRegistryService,
        runner: PythonComponentRunner,
    ) -> None:
        """重试已成功节点应失败（仅失败节点可重试）。"""
        name_map = await _publish_components(registry_service, runner)

        definition = await flow_service.create_definition(
            code="test_flow_retry_succeeded",
            display_name="Retry Succeeded Test",
        )

        nodes = (
            FlowNode(
                node_id="source",
                component_name=name_map["flow_source"],
                component_version="1.0.0",
                params={"value": "test"},
            ),
        )
        edges: tuple[FlowEdge, ...] = ()
        version = await flow_service.publish_version(
            flow_definition_id=definition.id,
            nodes=nodes,
            edges=edges,
        )

        run = await flow_service.create_run(
            flow_version_id=version.id,
            inputs={},
        )
        await flow_service.execute(run.id)

        # 重试已成功节点应失败
        with pytest.raises(AppError) as exc_info:
            await flow_service.retry_node(run.id, "source")
        assert exc_info.value.code == "validation_failed"

    async def test_retry_nonexistent_node_fails(
        self,
        flow_service: FlowRuntimeService,
        registry_service: ComponentRegistryService,
        runner: PythonComponentRunner,
    ) -> None:
        """重试不存在的节点应失败。"""
        name_map = await _publish_components(registry_service, runner)

        definition = await flow_service.create_definition(
            code="test_flow_retry_nonexistent",
            display_name="Retry Nonexistent Test",
        )

        nodes = (
            FlowNode(
                node_id="source",
                component_name=name_map["flow_source"],
                component_version="1.0.0",
                params={"value": "test"},
            ),
        )
        edges: tuple[FlowEdge, ...] = ()
        version = await flow_service.publish_version(
            flow_definition_id=definition.id,
            nodes=nodes,
            edges=edges,
        )

        run = await flow_service.create_run(
            flow_version_id=version.id,
            inputs={},
        )
        await flow_service.execute(run.id)

        with pytest.raises(AppError) as exc_info:
            await flow_service.retry_node(run.id, "nonexistent_node")
        assert exc_info.value.code == "not_found"


@pytest.mark.asyncio
class TestFlowDefinition:
    """流程定义管理测试。"""

    async def test_create_definition_with_dag_validation(
        self,
        flow_service: FlowRuntimeService,
        registry_service: ComponentRegistryService,
        runner: PythonComponentRunner,
    ) -> None:
        """创建定义时进行 DAG 校验。"""
        name_map = await _publish_components(registry_service, runner)

        # 有效 DAG
        nodes = (
            FlowNode(
                node_id="source",
                component_name=name_map["flow_source"],
                component_version="1.0.0",
            ),
        )
        edges: tuple[FlowEdge, ...] = ()
        definition = await flow_service.create_definition(
            code="test_dag_valid",
            display_name="Valid DAG",
            nodes=nodes,
            edges=edges,
        )
        assert definition.code == "test_dag_valid"

    async def test_create_definition_with_cycle_fails(
        self,
        flow_service: FlowRuntimeService,
    ) -> None:
        """创建定义时 DAG 有环应失败。"""
        nodes = (
            FlowNode("a", "comp_a", "1.0.0"),
            FlowNode("b", "comp_b", "1.0.0"),
        )
        edges = (
            FlowEdge("a", "out", "b", "in"),
            FlowEdge("b", "out", "a", "in"),
        )

        with pytest.raises(AppError) as exc_info:
            await flow_service.create_definition(
                code="test_dag_cycle",
                display_name="Cycle DAG",
                nodes=nodes,
                edges=edges,
            )
        assert exc_info.value.code == "validation_failed"

    async def test_duplicate_code_fails(
        self,
        flow_service: FlowRuntimeService,
    ) -> None:
        """重复编码应失败。"""
        await flow_service.create_definition(
            code="test_dup_code",
            display_name="First",
        )

        with pytest.raises(AppError) as exc_info:
            await flow_service.create_definition(
                code="test_dup_code",
                display_name="Second",
            )
        assert exc_info.value.code == "conflict"

    async def test_list_definitions(
        self,
        flow_service: FlowRuntimeService,
    ) -> None:
        """列表查询流程定义。"""
        await flow_service.create_definition(
            code="test_list_1",
            display_name="Flow 1",
        )
        await flow_service.create_definition(
            code="test_list_2",
            display_name="Flow 2",
        )

        items = await flow_service.list_definitions()
        codes = [d.code for d, _v in items]
        assert "test_list_1" in codes
        assert "test_list_2" in codes

    async def test_get_definition(
        self,
        flow_service: FlowRuntimeService,
    ) -> None:
        """获取流程定义详情。"""
        definition = await flow_service.create_definition(
            code="test_get",
            display_name="Get Test",
        )

        result_def, result_version = await flow_service.get_definition(definition.id)
        assert result_def.code == "test_get"
        assert result_version is None  # 无已发布版本

    async def test_get_nonexistent_definition_fails(
        self,
        flow_service: FlowRuntimeService,
    ) -> None:
        """获取不存在的定义应失败。"""
        from uuid import uuid4

        with pytest.raises(AppError) as exc_info:
            await flow_service.get_definition(uuid4())
        assert exc_info.value.code == "not_found"
