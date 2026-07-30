"""IRIP 组件运行器：Python 进程内 + CLI 子进程。

提供：
- PythonComponentRunner: 维护 (name, version) → Component 实例注册表，
  在当前进程内直接调用 execute()；
- CLIComponentRunner: 通过 subprocess + stdin/stdout JSON 通信执行
  CLI 组件，支持超时、取消、受限环境变量。

设计要点（IRIP V2-T01）：
- 两种 Runner 均实现 ComponentRunner 协议；
- 超时用 asyncio.wait_for / asyncio.wait(timeout=...)，取消用
  cancel_event（Python）/ SIGTERM（CLI）；
- CLI Runner 创建临时工作目录，隔离执行环境；
- CLI Runner 过滤环境变量，仅传递安全变量（不含 secrets 明文）。

安全增强（技术设计文档 F-13，T1-9 + H-12）：
- SAFE_CLI_MODE 环境变量开关（默认 true），开启后 CLI 组件在
  沙箱容器中执行（无网络、只读 FS、非 root、资源限制）；
- 生产环境强制 fail-closed：非沙箱模式时拒绝执行（H-12）。
"""

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from packages.common.errors import AppError
from packages.components.manifest import ComponentManifest
from packages.components.sdk import (
    Component,
    ComponentContext,
    ComponentResult,
)

#: Python 组件默认超时秒数。
_DEFAULT_PYTHON_TIMEOUT: float = 300.0

#: CLI 组件沙箱模式开关（环境变量 IRIP_SAFE_CLI_MODE）。
#: H-12: 默认 true（安全优先），生产环境强制 fail-closed。
#: - true（默认）：CLI 组件在独立沙箱容器中执行；
#: - false：CLI 组件直接在当前进程中执行（仅开发/测试环境）。
_SAFE_CLI_MODE: bool = os.getenv("IRIP_SAFE_CLI_MODE", "true").lower() in (
    "true",
    "1",
    "yes",
)

#: H-12: 是否为生产环境（用于强制 fail-closed）。
_IS_PRODUCTION: bool = os.getenv("IRIP_ENV") == "production"

#: 沙箱容器镜像名称。
_SANDBOX_IMAGE: str = os.getenv("IRIP_CLI_SANDBOX_IMAGE", "irip-cli-sandbox:latest")

#: 沙箱容器内存上限（字节）。
_SANDBOX_MEMORY_LIMIT: str = os.getenv("IRIP_CLI_SANDBOX_MEMORY", "512m")

#: 沙箱容器 CPU 核数上限。
_SANDBOX_CPU_LIMIT: str = os.getenv("IRIP_CLI_SANDBOX_CPUS", "1")


class PythonComponentRunner:
    """Python 进程内组件运行器。

    维护 (name, version) → Component 实例的内存注册表，
    执行时直接调用 Component.execute()。

    支持超时（asyncio.wait）与取消（cancel_event）：
    - 超时：asyncio.wait 的 timeout 参数，到期后取消执行任务；
    - 取消：同时监听 context.cancel_event，被设置后取消执行任务。

    Attributes:
        _registry: (name, version) → Component 实例映射。
    """

    def __init__(self) -> None:
        """初始化运行器，创建空注册表。"""
        self._registry: dict[tuple[str, str], Component] = {}

    def register(
        self,
        manifest: ComponentManifest,
        impl: Component,
    ) -> None:
        """注册组件实现。

        Args:
            manifest: 组件清单（提供 name + version 键）。
            impl: 组件实现实例（满足 Component 协议）。
        """
        self._registry[(manifest.name, manifest.version)] = impl

    async def run(
        self,
        manifest: ComponentManifest,
        context: ComponentContext,
        params: dict[str, Any],
    ) -> ComponentResult:
        """运行已注册的 Python 组件。

        流程：
        1. 查注册表获取实现实例；
        2. 创建执行任务与取消监听任务；
        3. asyncio.wait 竞速（FIRST_COMPLETED + timeout）；
        4. 根据先完成的任务决定结果/取消/超时。

        Args:
            manifest: 组件清单。
            context: 执行上下文。
            params: 组件参数。

        Returns:
            ComponentResult: 执行结果。

        Raises:
            AppError: code="component_not_found"，当实现未注册。
            AppError: code="component_timeout"，当执行超时。
            AppError: code="component_cancelled"，当被取消。
        """
        key = (manifest.name, manifest.version)
        impl = self._registry.get(key)
        # 版本不匹配时按 name 回退查找（网页发布的版本号可能比注册表里的新）
        if impl is None:
            for (reg_name, _reg_ver), reg_impl in self._registry.items():
                if reg_name == manifest.name:
                    impl = reg_impl
                    break
        if impl is None:
            # 检查 manifest 是否为 LLM 类型组件
            # （parameters.properties 中包含 prompt 键）
            _props = (manifest.parameters or {}).get("properties", {}) or {}
            # 防御：properties 理论上可能为非 dict（schema 仅约束 parameters 为 object），
            # 此处确保 _props 为 dict，避免对字符串做 `in` 触发子串误判
            if not isinstance(_props, dict):
                _props = {}
            if "prompt" in _props:
                # LLM 类型组件统一使用 EZScanExtractor 实现
                # 延迟导入避免循环依赖
                from packages.components.builtin.ingestion.ez_scan_extractor import (
                    EZScanExtractor,
                )

                impl = EZScanExtractor()
                # 自动注册，下次不用重复创建
                self._registry[(manifest.name, manifest.version)] = impl
            else:
                raise AppError(
                    code="component_not_found",
                    message=(f"Python 组件未注册: {manifest.name}@{manifest.version}"),
                    retryable=False,
                    fields={
                        "name": manifest.name,
                        "manifest_version": manifest.version,
                    },
                )

        execute_task = asyncio.create_task(impl.execute(context, params))
        cancel_watcher = asyncio.create_task(context.cancel_event.wait())

        done: set[asyncio.Task[object]] = set()
        pending: set[asyncio.Task[object]] = set()
        try:
            done, pending = await asyncio.wait(
                {execute_task, cancel_watcher},
                return_when=asyncio.FIRST_COMPLETED,
                timeout=_DEFAULT_PYTHON_TIMEOUT,
            )
        finally:
            for task in (execute_task, cancel_watcher):
                if not task.done():
                    task.cancel()
            # 确保已取消的任务完成，抑制 CancelledError 警告
            await asyncio.gather(
                execute_task,
                cancel_watcher,
                return_exceptions=True,
            )

        # 取消优先级高于正常完成
        if cancel_watcher in done and execute_task not in done:
            raise AppError(
                code="component_cancelled",
                message=(f"组件执行被取消: {manifest.name}@{manifest.version}"),
                retryable=False,
                fields={
                    "name": manifest.name,
                    "version": manifest.version,
                },
            )

        if execute_task in done:
            exc = execute_task.exception()
            if exc is not None:
                if isinstance(exc, asyncio.CancelledError):
                    raise AppError(
                        code="component_cancelled",
                        message=(f"组件执行被取消: {manifest.name}@{manifest.version}"),
                        retryable=False,
                        fields={
                            "name": manifest.name,
                            "version": manifest.version,
                        },
                    ) from exc
                raise exc
            return execute_task.result()

        # 两者均未完成 → 超时
        raise AppError(
            code="component_timeout",
            message=(f"组件执行超时: {manifest.name}@{manifest.version}"),
            retryable=False,
            fields={
                "name": manifest.name,
                "version": manifest.version,
            },
        )


class CLIComponentRunner:
    """CLI 子进程组件运行器。

    通过 subprocess + JSON 文件通信执行 CLI 组件。
    创建临时工作目录，写入 input.json，执行命令，读取 output.json。

    通信协议：
    - input.json: ``{"params": {...}, "context": {...}}``；
    - output.json: ``{"outputs": {...}, "summary": "...", ...}``；
    - 命令格式: ``<command> <input_path> <output_path>``。

    安全措施：
    - 超时用 asyncio.wait_for，到期发 SIGTERM；
    - 取消用 cancel_event 监听 + SIGTERM 终止子进程；
    - 环境变量白名单过滤（仅 PATH/HOME/LANG/LC_*/IRIP_COMPONENT_*），
      secrets 以 ``IRIP_COMPONENT_SECRET_<KEY>`` 前缀注入。

    Attributes:
        _timeout: 默认超时秒数。
        _network_allowlist: 允许访问的主机列表
            （空表示无网络限制检查）。
    """

    #: 安全的环境变量前缀白名单（其余一律过滤）。
    _SAFE_ENV_PREFIXES: tuple[str, ...] = (
        "PATH",
        "HOME",
        "LANG",
        "LC_",
        "IRIP_COMPONENT_",
    )

    def __init__(
        self,
        timeout: float = 300.0,
        network_allowlist: tuple[str, ...] = (),
    ) -> None:
        """初始化 CLI 运行器。

        Args:
            timeout: 默认超时秒数。
            network_allowlist: 允许访问的主机列表
                （空表示无网络限制检查）。
        """
        self._timeout = timeout
        self._network_allowlist = network_allowlist

    async def run(
        self,
        manifest: ComponentManifest,
        context: ComponentContext,
        params: dict[str, Any],
    ) -> ComponentResult:
        """运行 CLI 组件。

        流程：
        1. 创建临时工作目录；
        2. 写入 input.json（params + context 元数据）；
        3. 从 manifest.parameters 提取 command；
        4. 构建受限环境变量；
        5. asyncio.create_subprocess_exec 执行命令；
        6. asyncio.wait_for 等待完成（超时发 SIGTERM）；
        7. 同时监听 cancel_event（被设置则终止子进程）；
        8. 读取 output.json → ComponentResult；
        9. 清理临时目录（with 语句自动）。

        Args:
            manifest: 组件清单。
            context: 执行上下文。
            params: 组件参数。

        Returns:
            ComponentResult: 执行结果。

        Raises:
            AppError: code="invalid_manifest"，当缺少 command。
            AppError: code="component_not_found"，当命令不存在。
            AppError: code="component_timeout"，当执行超时。
            AppError: code="component_cancelled"，当被取消。
            AppError: code="component_failed"，当子进程非零退出。
            AppError: code="invalid_output"，当 output.json 解析失败。
        """
        with tempfile.TemporaryDirectory(prefix="irip-component-") as tmpdir:
            workdir = Path(tmpdir)

            # 1. 写入 input.json
            input_payload: dict[str, Any] = {
                "params": params,
                "context": {
                    "organization_id": str(context.organization_id),
                    "user_id": str(context.user_id),
                    "job_id": str(context.job_id),
                },
            }
            input_path = workdir / "input.json"
            input_path.write_text(
                json.dumps(input_payload, ensure_ascii=False),
                encoding="utf-8",
            )

            # 2. 构建受限环境变量
            safe_env = self._build_safe_env(context.secrets)

            # 3. 从 manifest.parameters 提取 command
            command_spec = manifest.parameters.get("command")
            if command_spec is None:
                raise AppError(
                    code="invalid_manifest",
                    message="CLI 组件缺少 parameters.command 字段",
                    retryable=False,
                    fields={"name": manifest.name},
                )
            if isinstance(command_spec, str):
                command: list[str] = command_spec.split()
            elif isinstance(command_spec, list):
                command = [str(c) for c in command_spec]
            else:
                raise AppError(
                    code="invalid_manifest",
                    message=("parameters.command 必须为字符串或字符串列表"),
                    retryable=False,
                    fields={"name": manifest.name},
                )

            # 4. 执行子进程
            output_path = workdir / "output.json"

            # H-12: 生产环境强制 fail-closed（非沙箱时拒绝执行）
            if not _SAFE_CLI_MODE and _IS_PRODUCTION:
                raise AppError(
                    code="forbidden",
                    message="生产环境必须启用 CLI 沙箱（IRIP_SAFE_CLI_MODE=true）",
                    retryable=False,
                    fields={
                        "name": manifest.name,
                        "version": manifest.version,
                    },
                )

            if _SAFE_CLI_MODE:
                # 沙箱模式：在隔离容器中执行（F-13 安全增强）
                full_command = self._build_sandbox_command(
                    command, workdir, input_path, output_path
                )
                exec_kwargs: dict[str, Any] = {
                    "stdout": asyncio.subprocess.PIPE,
                    "stderr": asyncio.subprocess.PIPE,
                }
            else:
                # 直接模式：在当前进程中执行（默认，开发/测试环境）
                full_command = command + [str(input_path), str(output_path)]
                exec_kwargs = {
                    "cwd": str(workdir),
                    "env": safe_env,
                    "stdout": asyncio.subprocess.PIPE,
                    "stderr": asyncio.subprocess.PIPE,
                }

            try:
                process = await asyncio.create_subprocess_exec(
                    *full_command,
                    **exec_kwargs,
                )
            except FileNotFoundError as exc:
                raise AppError(
                    code="component_not_found",
                    message=f"CLI 命令不存在: {command[0]}",
                    retryable=False,
                    fields={"command": command[0]},
                ) from exc

            # 5. 取消监听 + 超时等待
            async def _monitor_cancel() -> None:
                """监听 cancel_event，触发时终止子进程。"""
                await context.cancel_event.wait()
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass

            cancel_monitor = asyncio.create_task(_monitor_cancel())

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self._timeout,
                )
            except TimeoutError:
                cancel_monitor.cancel()
                self._terminate_process(process)
                raise AppError(
                    code="component_timeout",
                    message=(f"CLI 组件执行超时: {manifest.name}@{manifest.version}"),
                    retryable=False,
                    fields={
                        "name": manifest.name,
                        "version": manifest.version,
                    },
                ) from None
            finally:
                if not cancel_monitor.done():
                    cancel_monitor.cancel()
                    try:
                        await cancel_monitor
                    except asyncio.CancelledError:
                        pass

            # 6. 检查取消信号
            if context.cancel_event.is_set():
                raise AppError(
                    code="component_cancelled",
                    message=(f"CLI 组件被取消: {manifest.name}@{manifest.version}"),
                    retryable=False,
                    fields={
                        "name": manifest.name,
                        "version": manifest.version,
                    },
                )

            # 7. 检查退出码
            if process.returncode != 0:
                stderr_text = stderr_bytes.decode("utf-8", errors="replace")
                raise AppError(
                    code="component_failed",
                    message=(f"CLI 组件执行失败 (exit={process.returncode}): {stderr_text}"),
                    retryable=False,
                    fields={
                        "name": manifest.name,
                        "version": manifest.version,
                        "exit_code": process.returncode,
                    },
                )

            # 8. 读取 output.json
            if not output_path.exists():
                raise AppError(
                    code="invalid_output",
                    message=(f"CLI 组件未生成 output.json: {manifest.name}@{manifest.version}"),
                    retryable=False,
                    fields={
                        "name": manifest.name,
                        "version": manifest.version,
                    },
                )

            try:
                output_data = json.loads(output_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise AppError(
                    code="invalid_output",
                    message=f"output.json 解析失败: {exc}",
                    retryable=False,
                    fields={
                        "name": manifest.name,
                        "version": manifest.version,
                    },
                ) from exc

            return ComponentResult(
                outputs=output_data.get("outputs", {}),
                summary=output_data.get("summary", ""),
                metadata=output_data.get("metadata", {}),
                diagnostics=output_data.get("diagnostics"),
            )

    def _build_sandbox_command(
        self,
        command: list[str],
        workdir: Path,
        input_path: Path,
        output_path: Path,
    ) -> list[str]:
        """构建沙箱容器执行命令。

        将 CLI 组件命令包装在 ``docker run`` 中，应用安全限制：
        - ``--network=none``：无网络访问；
        - ``--read-only``：只读根文件系统；
        - ``--tmpfs``：工作目录使用 tmpfs（可写）；
        - ``--user 2000:2000``：非 root 用户；
        - ``--cap-drop=ALL``：丢弃所有 Linux capabilities；
        - ``--memory`` / ``--cpus``：资源限制；
        - ``--rm``：执行完自动清理容器。

        Args:
            command: CLI 组件命令列表。
            workdir: 主机临时工作目录。
            input_path: input.json 路径。
            output_path: output.json 路径。

        Returns:
            list[str]: 完整的 docker run 命令列表。
        """
        container_workdir = "/tmp/component-work"
        container_input = f"{container_workdir}/input.json"
        container_output = f"{container_workdir}/output.json"

        docker_command: list[str] = [
            "docker",
            "run",
            "--rm",
            # 安全限制
            "--network=none",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,size=64m,mode=1777",
            "--user",
            "2000:2000",
            "--cap-drop=ALL",
            "--no-new-privileges",
            f"--memory={_SANDBOX_MEMORY_LIMIT}",
            f"--cpus={_SANDBOX_CPU_LIMIT}",
            # 挂载工作目录（input.json + output.json 通信）
            "-v",
            f"{workdir}:{container_workdir}",
            "-w",
            container_workdir,
            # 沙箱镜像
            _SANDBOX_IMAGE,
            # 组件命令
            *command,
            container_input,
            container_output,
        ]
        return docker_command

    def _build_safe_env(
        self,
        secrets: dict[str, str],
    ) -> dict[str, str]:
        """构建受限环境变量（过滤不安全变量，注入 secrets）。

        仅保留以安全前缀开头的环境变量，secrets 以
        ``IRIP_COMPONENT_SECRET_<KEY>`` 前缀注入。

        Args:
            secrets: 组件密钥字典。

        Returns:
            dict[str, str]: 安全的环境变量字典。
        """
        safe_env: dict[str, str] = {}
        for key, value in os.environ.items():
            if any(key.startswith(prefix) for prefix in self._SAFE_ENV_PREFIXES):
                safe_env[key] = value
        for key, value in secrets.items():
            safe_env[f"IRIP_COMPONENT_SECRET_{key.upper()}"] = value
        return safe_env

    def _terminate_process(self, process: asyncio.subprocess.Process) -> None:
        """向子进程发送 SIGTERM（优雅终止）。

        Args:
            process: 待终止的子进程。
        """
        try:
            process.terminate()
        except ProcessLookupError:
            pass  # 进程已退出
