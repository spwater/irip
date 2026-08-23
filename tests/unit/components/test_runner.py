"""组件运行器单元测试。

覆盖 packages/components/runner/runner.py：
- PythonComponentRunner: 注册、运行、版本回退、LLM 自动注册、
  超时、取消、未注册报错；
- CLIComponentRunner: _build_safe_env 环境过滤、
  _build_sandbox_command 沙箱命令构建、
  无 command 报错、command 类型校验、
  生产环境 fail-closed。
"""

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from packages.common.errors import AppError
from packages.components.manifest import ComponentManifest
from packages.components.runner.runner import (
    CLIComponentRunner,
    PythonComponentRunner,
)
from packages.components.sdk import Component, ComponentContext, ComponentResult

# ---------------------------------------------------------------------------
# 辅助：构建测试 manifest 与 context
# ---------------------------------------------------------------------------


def _make_manifest(
    name: str = "test-component",
    version: str = "1.0.0",
    runtime: str = "python",
    parameters: dict[str, Any] | None = None,
) -> ComponentManifest:
    """构建测试用 ComponentManifest。"""
    return ComponentManifest(
        name=name,
        display_name="Test Component",
        version=version,
        kind="transform",
        runtime=runtime,
        inputs=(),
        outputs=(),
        parameters=parameters or {},
        dependencies=(),
        raw_yaml="",
        sha256="",
    )


def _make_context(cancel_event: asyncio.Event | None = None) -> ComponentContext:
    """构建测试用 ComponentContext。"""
    return ComponentContext(
        department_id=uuid4(),
        user_id=uuid4(),
        clock=MagicMock(),
        artifact_service=MagicMock(),
        job_id=uuid4(),
        cancel_event=cancel_event or asyncio.Event(),
        secrets={},
        workdir=Path("/tmp/test"),
    )


# ---------------------------------------------------------------------------
# PythonComponentRunner
# ---------------------------------------------------------------------------


class TestPythonComponentRunnerRegister:
    """PythonComponentRunner 注册逻辑。"""

    def test_register_and_lookup(self) -> None:
        """注册后能通过 manifest 查到。"""
        runner = PythonComponentRunner()
        impl = MagicMock(spec=Component)
        manifest = _make_manifest("comp-a", "1.0.0")
        runner.register(manifest, impl)
        key = ("comp-a", "1.0.0")
        assert runner._registry[key] is impl

    def test_register_overwrites(self) -> None:
        """重复注册覆盖。"""
        runner = PythonComponentRunner()
        impl1 = MagicMock(spec=Component)
        impl2 = MagicMock(spec=Component)
        manifest = _make_manifest("comp-a", "1.0.0")
        runner.register(manifest, impl1)
        runner.register(manifest, impl2)
        assert runner._registry[("comp-a", "1.0.0")] is impl2


class TestPythonComponentRunnerRun:
    """PythonComponentRunner 运行逻辑。"""

    @pytest.mark.asyncio
    async def test_run_success(self) -> None:
        """已注册组件正常执行返回结果。"""
        runner = PythonComponentRunner()
        expected = ComponentResult(outputs={"out": 1}, summary="ok")
        impl = MagicMock(spec=Component)
        impl.execute = AsyncMock(return_value=expected)
        manifest = _make_manifest("comp-a", "1.0.0")
        runner.register(manifest, impl)

        result = await runner.run(manifest, _make_context(), {"x": 1})
        assert result is expected
        impl.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_not_found(self) -> None:
        """未注册组件 → component_not_found。"""
        runner = PythonComponentRunner()
        manifest = _make_manifest("missing-comp", "9.9.9")
        with pytest.raises(AppError) as exc_info:
            await runner.run(manifest, _make_context(), {})
        assert exc_info.value.code == "component_not_found"

    @pytest.mark.asyncio
    async def test_version_fallback_by_name(self) -> None:
        """版本不匹配时按 name 回退查找。"""
        runner = PythonComponentRunner()
        expected = ComponentResult(outputs={}, summary="fallback")
        impl = MagicMock(spec=Component)
        impl.execute = AsyncMock(return_value=expected)
        # 注册 1.0.0，但请求 1.0.1
        runner.register(_make_manifest("comp-a", "1.0.0"), impl)
        manifest = _make_manifest("comp-a", "1.0.1")

        result = await runner.run(manifest, _make_context(), {})
        assert result is expected

    @pytest.mark.asyncio
    async def test_llm_auto_registration(self) -> None:
        """含 prompt 参数的 manifest 自动注册 EZScanExtractor。"""
        runner = PythonComponentRunner()
        manifest = _make_manifest(
            "llm-comp",
            "1.0.0",
            parameters={"properties": {"prompt": {"type": "string"}}},
        )
        # EZScanExtractor 需要 path 参数，提供一个不存在的路径使其抛异常
        # 但验证它已被自动注册
        with pytest.raises((KeyError, AppError, Exception)):
            await runner.run(manifest, _make_context(), {})
        # 验证已自动注册
        assert ("llm-comp", "1.0.0") in runner._registry

    @pytest.mark.asyncio
    async def test_non_dict_properties_treated_as_empty(self) -> None:
        """properties 为非 dict（如字符串）时不误判 prompt。"""
        runner = PythonComponentRunner()
        manifest = _make_manifest(
            "non-dict-props",
            "1.0.0",
            parameters={"properties": "not-a-dict"},
        )
        with pytest.raises(AppError) as exc_info:
            await runner.run(manifest, _make_context(), {})
        assert exc_info.value.code == "component_not_found"

    @pytest.mark.asyncio
    async def test_execute_raises_non_cancel_exception(self) -> None:
        """execute 抛出非 CancelledError 异常时传递。"""
        runner = PythonComponentRunner()
        impl = MagicMock(spec=Component)
        impl.execute = AsyncMock(side_effect=ValueError("boom"))
        manifest = _make_manifest("comp-a", "1.0.0")
        runner.register(manifest, impl)

        with pytest.raises(ValueError, match="boom"):
            await runner.run(manifest, _make_context(), {})

    @pytest.mark.asyncio
    async def test_cancel_event_set(self) -> None:
        """cancel_event 被设置 → component_cancelled。"""
        runner = PythonComponentRunner()
        cancel_event = asyncio.Event()

        async def slow_execute(ctx, params):
            await asyncio.sleep(10)
            return ComponentResult(outputs={}, summary="")

        impl = MagicMock(spec=Component)
        impl.execute = slow_execute
        manifest = _make_manifest("comp-a", "1.0.0")
        runner.register(manifest, impl)

        # 在执行前设置取消事件
        cancel_event.set()
        with pytest.raises(AppError) as exc_info:
            await runner.run(manifest, _make_context(cancel_event), {})
        assert exc_info.value.code == "component_cancelled"


# ---------------------------------------------------------------------------
# CLIComponentRunner._build_safe_env
# ---------------------------------------------------------------------------


class TestBuildSafeEnv:
    """CLIComponentRunner 环境变量过滤。"""

    def test_filters_unsafe_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """非白名单前缀的环境变量被过滤。"""
        monkeypatch.setenv("PATH", "/usr/bin")
        monkeypatch.setenv("HOME", "/home/test")
        monkeypatch.setenv("LANG", "en_US.UTF-8")
        monkeypatch.setenv("SECRET_TOKEN", "super-secret")
        monkeypatch.setenv("DATABASE_PASSWORD", "should-not-leak")

        runner = CLIComponentRunner()
        safe_env = runner._build_safe_env({})

        assert "PATH" in safe_env
        assert "HOME" in safe_env
        assert "LANG" in safe_env
        assert "SECRET_TOKEN" not in safe_env
        assert "DATABASE_PASSWORD" not in safe_env

    def test_keeps_irip_component_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """IRIP_COMPONENT_ 前缀的环境变量保留。"""
        monkeypatch.setenv("IRIP_COMPONENT_TIMEOUT", "60")
        monkeypatch.setenv("IRIP_DEBUG", "true")

        runner = CLIComponentRunner()
        safe_env = runner._build_safe_env({})

        assert "IRIP_COMPONENT_TIMEOUT" in safe_env
        assert "IRIP_DEBUG" not in safe_env

    def test_injects_secrets(self) -> None:
        """secrets 以 IRIP_COMPONENT_SECRET_ 前缀注入。"""
        runner = CLIComponentRunner()
        safe_env = runner._build_safe_env({"api_key": "abc123", "token": "xyz"})

        assert safe_env["IRIP_COMPONENT_SECRET_API_KEY"] == "abc123"
        assert safe_env["IRIP_COMPONENT_SECRET_TOKEN"] == "xyz"

    def test_empty_secrets(self) -> None:
        """空 secrets 字典不注入任何密钥。"""
        runner = CLIComponentRunner()
        safe_env = runner._build_safe_env({})
        # 不应有 IRIP_COMPONENT_SECRET_ 前缀的键
        assert not any(k.startswith("IRIP_COMPONENT_SECRET_") for k in safe_env)

    def test_lc_prefix_env_kept(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """LC_ 前缀环境变量保留。"""
        monkeypatch.setenv("LC_ALL", "en_US.UTF-8")
        runner = CLIComponentRunner()
        safe_env = runner._build_safe_env({})
        assert "LC_ALL" in safe_env


# ---------------------------------------------------------------------------
# CLIComponentRunner._build_sandbox_command
# ---------------------------------------------------------------------------


class TestBuildSandboxCommand:
    """CLIComponentRunner 沙箱命令构建。"""

    def test_docker_run_prefix(self) -> None:
        """沙箱命令以 docker run 开头。"""
        runner = CLIComponentRunner()
        workdir = Path("/tmp/test")
        cmd = runner._build_sandbox_command(
            ["my-tool"], workdir, workdir / "input.json", workdir / "output.json"
        )
        assert cmd[0] == "docker"
        assert cmd[1] == "run"
        assert "--rm" in cmd

    def test_security_flags(self) -> None:
        """沙箱命令包含安全限制标志。"""
        runner = CLIComponentRunner()
        workdir = Path("/tmp/test")
        cmd = runner._build_sandbox_command(
            ["my-tool"], workdir, workdir / "in.json", workdir / "out.json"
        )
        cmd_str = " ".join(cmd)
        assert "--network=none" in cmd_str
        assert "--read-only" in cmd_str
        assert "--cap-drop=ALL" in cmd_str
        assert "--no-new-privileges" in cmd_str

    def test_volume_mount(self) -> None:
        """沙箱命令挂载工作目录。"""
        runner = CLIComponentRunner()
        workdir = Path("/tmp/my-work")
        cmd = runner._build_sandbox_command(
            ["my-tool"], workdir, workdir / "in.json", workdir / "out.json"
        )
        assert "-v" in cmd
        idx = cmd.index("-v")
        mount_spec = cmd[idx + 1]
        host_path = mount_spec.split(":")[0]
        assert str(workdir) == host_path

    def test_user_non_root(self) -> None:
        """沙箱以非 root 用户运行。"""
        runner = CLIComponentRunner()
        workdir = Path("/tmp/test")
        cmd = runner._build_sandbox_command(
            ["my-tool"], workdir, workdir / "in.json", workdir / "out.json"
        )
        assert "--user" in cmd
        idx = cmd.index("--user")
        assert cmd[idx + 1] == "2000:2000"

    def test_command_passthrough(self) -> None:
        """组件命令被传递到 docker run 末尾。"""
        runner = CLIComponentRunner()
        workdir = Path("/tmp/test")
        cmd = runner._build_sandbox_command(
            ["python", "tool.py"], workdir, workdir / "in.json", workdir / "out.json"
        )
        assert "python" in cmd
        assert "tool.py" in cmd


# ---------------------------------------------------------------------------
# CLIComponentRunner.run 错误路径
# ---------------------------------------------------------------------------


class TestCLIRunnerErrors:
    """CLIComponentRunner.run 错误路径。"""

    @pytest.mark.asyncio
    async def test_missing_command(self) -> None:
        """manifest 缺少 parameters.command → invalid_manifest。"""
        runner = CLIComponentRunner(timeout=5)
        manifest = _make_manifest("cli-comp", "1.0.0", parameters={})
        ctx = _make_context()
        with pytest.raises(AppError) as exc_info:
            await runner.run(manifest, ctx, {})
        assert exc_info.value.code == "invalid_manifest"

    @pytest.mark.asyncio
    async def test_command_wrong_type(self) -> None:
        """parameters.command 为非 str/list → invalid_manifest。"""
        runner = CLIComponentRunner(timeout=5)
        manifest = _make_manifest(
            "cli-comp",
            "1.0.0",
            parameters={"command": 12345},
        )
        ctx = _make_context()
        with pytest.raises(AppError) as exc_info:
            await runner.run(manifest, ctx, {})
        assert exc_info.value.code == "invalid_manifest"

    @pytest.mark.asyncio
    async def test_command_list_type(self) -> None:
        """parameters.command 为 list 时正确解析。"""
        runner = CLIComponentRunner(timeout=5)
        manifest = _make_manifest(
            "cli-comp",
            "1.0.0",
            parameters={"command": ["echo", "hello"]},
        )
        ctx = _make_context()
        # echo 命令存在但不生成 output.json → invalid_output
        with pytest.raises(AppError) as exc_info:
            await runner.run(manifest, ctx, {})
        # 在沙箱模式下 docker 可能不存在 → component_not_found
        # 在直接模式下 echo 存在但不写 output.json → invalid_output
        assert exc_info.value.code in ("invalid_output", "component_not_found", "component_failed")

    @pytest.mark.asyncio
    async def test_production_fail_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """生产环境 + 非沙箱模式 → forbidden。"""
        monkeypatch.setattr("packages.components.runner.runner._IS_PRODUCTION", True)
        monkeypatch.setattr("packages.components.runner.runner._SAFE_CLI_MODE", False)
        runner = CLIComponentRunner(timeout=5)
        manifest = _make_manifest(
            "cli-comp",
            "1.0.0",
            parameters={"command": "echo hello"},
        )
        ctx = _make_context()
        with pytest.raises(AppError) as exc_info:
            await runner.run(manifest, ctx, {})
        assert exc_info.value.code == "forbidden"

    @pytest.mark.asyncio
    async def test_command_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """命令不存在 → component_not_found。"""
        monkeypatch.setattr("packages.components.runner.runner._SAFE_CLI_MODE", False)
        runner = CLIComponentRunner(timeout=5)
        manifest = _make_manifest(
            "cli-comp",
            "1.0.0",
            parameters={"command": "nonexistent-cli-command-xyz"},
        )
        ctx = _make_context()
        with pytest.raises(AppError) as exc_info:
            await runner.run(manifest, ctx, {})
        assert exc_info.value.code == "component_not_found"


# ---------------------------------------------------------------------------
# CLIComponentRunner._terminate_process
# ---------------------------------------------------------------------------


class TestTerminateProcess:
    """CLIComponentRunner._terminate_process。"""

    def test_terminate_existing_process(self) -> None:
        """对模拟进程调用 terminate。"""
        runner = CLIComponentRunner()
        proc = MagicMock()
        runner._terminate_process(proc)
        proc.terminate.assert_called_once()

    def test_terminate_already_dead(self) -> None:
        """进程已退出 → ProcessLookupError 被吞掉。"""
        runner = CLIComponentRunner()
        proc = MagicMock()
        proc.terminate.side_effect = ProcessLookupError()
        # 不应抛异常
        runner._terminate_process(proc)
