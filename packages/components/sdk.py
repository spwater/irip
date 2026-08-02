"""IRIP 组件系统核心 SDK。

定义组件执行契约的核心类型：
- PortSpec: 端口规格（输入/输出）；
- ComponentContext: 组件执行上下文（注入组织、用户、时钟、工件服务、取消信号等）；
- ComponentResult: 组件执行结果（不可变）；
- Component: 组件实现协议（async execute）；
- ComponentRunner: 组件运行器协议（async run）。

设计要点（IRIP V2-T01）：
- 所有核心类型为 frozen dataclass / Protocol，确保不可变性与可测试性；
- ComponentContext 通过依赖注入传递时钟、工件服务、取消信号，
  禁止组件直接访问全局状态；
- ComponentResult 携带 outputs / summary / metadata / diagnostics，
  供管线编排器消费。
"""

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from packages.common.clock import Clock


@dataclass(frozen=True)
class PortSpec:
    """端口规格（输入/输出端口）。

    描述组件端口的名称、数据类型、是否必需、以及可选的 JSON Schema 约束。

    Attributes:
        name: 端口名称（小写字母/数字/下划线，如 ``raw_data``）。
        data_type: 数据类型标识（如 ``dataset``、``table``、``model``）。
        required: 该端口是否必需（输入端缺数据时报错，输出端缺数据时告警）。
        schema: 可选的 JSON Schema 约束，用于校验端口载荷。
    """

    name: str
    data_type: str
    required: bool = True
    schema: dict[str, Any] | None = None


@dataclass(frozen=True)
class ComponentContext:
    """组件执行上下文（由管线编排器构造并注入）。

    封装组件执行所需的全部外部依赖与运行时信号：
    - department_id / user_id: 租户与操作人隔离；
    - clock: 注入时钟（生产 SystemClock，测试 FixedClock）；
    - artifact_service: 工件服务（上传/下载中间结果）；
    - job_id: 关联作业 ID（用于溯源）；
    - cancel_event: 取消信号（编排器设置后组件应尽快退出）；
    - secrets: 密钥字典（仅含组件声明的 secret key，值已解密）；
    - workdir: 组件专属临时工作目录（执行结束后清理）；
    - ai_config_provider: AI 配置提供函数（返回 dict | None），
      消除 packages→apps 反向依赖（T3-3）。

    Attributes:
        department_id: 当前部门 ID。
        user_id: 当前操作人 ID。
        clock: 时钟（依赖注入）。
        artifact_service: 工件服务实例（Any 类型避免循环导入）。
        job_id: 关联作业 ID。
        cancel_event: 取消事件（asyncio.Event）。
        secrets: 密钥字典。
        workdir: 临时工作目录路径。
        ai_config_provider: AI 配置异步提供函数（可选）。
    """

    department_id: UUID
    user_id: UUID
    clock: Clock
    artifact_service: Any
    job_id: UUID
    cancel_event: asyncio.Event
    secrets: dict[str, str] = field(default_factory=dict)
    workdir: Path = field(default_factory=Path)
    ai_config_provider: Any = field(default=None)


@dataclass(frozen=True)
class ComponentResult:
    """组件执行结果（不可变）。

    组件 execute() 的返回值，由管线编排器消费。

    Attributes:
        outputs: 输出端口 → 载荷映射（键为输出端口名，
            值为 ArtifactRef 或内联数据）。
        summary: 人类可读的执行摘要（如 ``"处理 1234 行，质量检查通过"``）。
        metadata: 结构化元数据（行数、耗时、自定义指标等）。
        diagnostics: 诊断信息（警告/错误详情），无诊断时为 None。
    """

    outputs: dict[str, Any]
    summary: str
    metadata: dict[str, Any] = field(default_factory=dict)
    diagnostics: dict[str, Any] | None = None


class Component(Protocol):
    """组件实现协议。

    每个具体组件（数据接入、变换、质量检查、统计、输出、模型）实现此协议。
    管线编排器通过 ComponentRunner 调用 execute()。

    约定：
    - execute() 必须是协程，支持 asyncio.wait_for 超时与
      cancel_event 取消；
    - 应在耗时操作前检查 context.cancel_event.is_set()，及时退出；
    - 返回 ComponentResult，不可返回 None；
    - 不允许直接访问数据库或全局状态，所有依赖经 ComponentContext 注入。
    """

    async def execute(
        self,
        context: ComponentContext,
        params: dict[str, Any],
    ) -> ComponentResult:
        """执行组件逻辑。

        Args:
            context: 组件执行上下文（注入依赖与运行时信号）。
            params: 组件参数（已通过 manifest.parameters JSON Schema 校验）。

        Returns:
            ComponentResult: 执行结果。

        Raises:
            AppError: 可预期业务错误（如输入校验失败）。
            Exception: 不可预期错误（由编排器捕获并重试/标记失败）。
        """
        ...


class ComponentRunner(Protocol):
    """组件运行器协议。

    将 manifest + context + params 分派到具体组件实现
    （Python 进程内或 CLI 子进程）。

    约定：
    - run() 根据 manifest.runtime 选择执行方式；
    - 支持 asyncio.wait_for 超时控制；
    - 支持 context.cancel_event 协作式取消；
    - 返回 ComponentResult。
    """

    async def run(
        self,
        manifest: Any,
        context: ComponentContext,
        params: dict[str, Any],
    ) -> ComponentResult:
        """运行组件。

        Args:
            manifest: 组件清单（描述端口、参数、超时等）。
            context: 组件执行上下文。
            params: 组件参数。

        Returns:
            ComponentResult: 执行结果。
        """
        ...
