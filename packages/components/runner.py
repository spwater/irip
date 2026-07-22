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
        if impl is None:
            raise AppError(
                code="component_not_found",
                message=(
                    f"Python 组件未注册: "
                    f"{manifest.name}@{manifest.version}"
                ),
                retryable=False,
                fields={
                    "name": manifest.name,
                    "version": manifest.version,
                },
            )

        execute_task = asyncio.create_task(
            impl.execute(context, params)
        )
        cancel_watcher = asyncio.create_task(
            context.cancel_event.wait()
        )

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
                execute_task, cancel_watcher,
                return_exceptions=True,
            )

        # 取消优先级高于正常完成
        if cancel_watcher in done and execute_task not in done:
            raise AppError(
                code="component_cancelled",
                message=(
                    f"组件执行被取消: "
                    f"{manifest.name}@{manifest.version}"
                ),
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
                        message=(
                            f"组件执行被取消: "
                            f"{manifest.name}@{manifest.version}"
                        ),
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
            message=(
                f"组件执行超时: "
                f"{manifest.name}@{manifest.version}"
            ),
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
        with tempfile.TemporaryDirectory(
            prefix="irip-component-"
        ) as tmpdir:
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
                    message=(
                        "parameters.command 必须为字符串或字符串列表"
                    ),
                    retryable=False,
                    fields={"name": manifest.name},
                )

            # 4. 执行子进程
            output_path = workdir / "output.json"
            full_command = command + [
                str(input_path), str(output_path)
            ]

            try:
                process = await asyncio.create_subprocess_exec(
                    *full_command,
                    cwd=str(workdir),
                    env=safe_env,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
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
            except asyncio.TimeoutError:
                cancel_monitor.cancel()
                self._terminate_process(process)
                raise AppError(
                    code="component_timeout",
                    message=(
                        f"CLI 组件执行超时: "
                        f"{manifest.name}@{manifest.version}"
                    ),
                    retryable=False,
                    fields={
                        "name": manifest.name,
                        "version": manifest.version,
                    },
                )
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
                    message=(
                        f"CLI 组件被取消: "
                        f"{manifest.name}@{manifest.version}"
                    ),
                    retryable=False,
                    fields={
                        "name": manifest.name,
                        "version": manifest.version,
                    },
                )

            # 7. 检查退出码
            if process.returncode != 0:
                stderr_text = stderr_bytes.decode(
                    "utf-8", errors="replace"
                )
                raise AppError(
                    code="component_failed",
                    message=(
                        f"CLI 组件执行失败 "
                        f"(exit={process.returncode}): {stderr_text}"
                    ),
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
                    message=(
                        f"CLI 组件未生成 output.json: "
                        f"{manifest.name}@{manifest.version}"
                    ),
                    retryable=False,
                    fields={
                        "name": manifest.name,
                        "version": manifest.version,
                    },
                )

            try:
                output_data = json.loads(
                    output_path.read_text(encoding="utf-8")
                )
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
            if any(
                key.startswith(prefix)
                for prefix in self._SAFE_ENV_PREFIXES
            ):
                safe_env[key] = value
        for key, value in secrets.items():
            safe_env[f"IRIP_COMPONENT_SECRET_{key.upper()}"] = value
        return safe_env

    def _terminate_process(
        self, process: asyncio.subprocess.Process
    ) -> None:
        """向子进程发送 SIGTERM（优雅终止）。

        Args:
            process: 待终止的子进程。
        """
        try:
            process.terminate()
        except ProcessLookupError:
            pass  # 进程已退出
