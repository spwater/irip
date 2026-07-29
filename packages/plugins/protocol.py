"""插件标准接口定义。

所有解析器插件实现 ``ConverterProtocol``，统一输入输出格式。
插件不依赖组件框架（ComponentContext/ComponentResult），
只负责"输入文件路径 → 输出结构化数据"。
"""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ConverterProtocol(Protocol):
    """解析器插件标准接口。

    每个插件需实现 ``execute`` 方法：
    - 输入：参数字典（至少含 file_path）
    - 输出：``{metadata: dict, points: list, series: list}``

    插件应为异步可调用对象或提供 ``execute`` 异步方法。
    """

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        """执行解析，返回结构化数据。

        Args:
            params: 参数字典，至少包含 ``file_path``（文件路径或 artifact: 前缀）。

        Returns:
            dict: ``{"metadata": {...}, "points": [...], "series": [...]}``

        Raises:
            AppError: 解析失败时抛出。
        """
        ...


class ConverterResult:
    """解析器返回的标准化数据结构。

    与 IRIP 数据接口的三类固定结构对齐：
    - metadata: 单值标头（字典）
    - points: 单点数据（列表，每行一条）
    - series: 序列数据（列表，整组一条）
    """

    __slots__ = ("metadata", "points", "series")

    def __init__(
        self,
        metadata: dict[str, Any] | None = None,
        points: list[dict[str, Any]] | None = None,
        series: list[dict[str, Any]] | None = None,
    ) -> None:
        self.metadata = metadata or {}
        self.points = points or []
        self.series = series or []

    def to_dict(self) -> dict[str, Any]:
        """转为字典格式。"""
        return {
            "metadata": self.metadata,
            "points": self.points,
            "series": self.series,
        }
