"""TaskSender 协议：依赖注入接口，解除 packages→apps 依赖。

Phase 3 架构收敛（T3-3）：``packages/`` 层不得直接 import ``apps/`` 层。
此前 ``packages/jobs/dispatcher.py`` 与 ``packages/jobs/outbox.py`` 在函数内部
lazy import ``apps.worker.celery_app.celery_app``，违反分层架构原则。

本模块定义 ``TaskSender`` Protocol（结构化子类型 / duck typing）：
- ``packages/`` 层仅依赖此 Protocol，不感知具体实现；
- ``apps/`` 组装层（Celery Beat 任务入口）注入真实的 ``Celery`` 实例；
- 测试中注入测试替身（如 ``RecordingTaskSender``）。

任何具备 ``send_task(name, args, queue)`` 方法的对象均满足此协议，
例如 Celery 的 ``Celery`` 应用实例。
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class TaskSender(Protocol):
    """任务发送者协议（结构化子类型）。

    ``packages.jobs`` 通过此协议声明对"任务投递通道"的依赖，
    由组装层注入具体实现，从而切断对 ``apps.worker`` 的直接引用。

    实现者只需提供 ``send_task`` 方法，签名为
    ``(name: str, args: list, queue: str) -> Any``。
    """

    def send_task(
        self,
        name: str,
        args: list,
        queue: str,
    ) -> object:
        """发送任务到指定队列。

        Args:
            name: 任务名称（如 ``"jobs.execute"``）。
            args: 任务位置参数列表。
            queue: 目标队列名称（如 ``"irip-jobs"``）。

        Returns:
            实现者自定义的投递结果（通常忽略）。
        """
        ...
