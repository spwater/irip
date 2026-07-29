"""大模型解析器插件（占位）。

用于大模型对数据的解析。当前为占位实现，
后续将集成 LLM 调用逻辑。
"""

from typing import Any

from packages.common.errors import AppError


class LlmConverter:
    """大模型解析器（占位）。"""

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        """调用大模型解析数据（占位实现）。

        Args:
            params: 参数字典，包含 file_path 和 prompt。

        Returns:
            dict: ``{"metadata": {...}, "points": [...], "series": [...]}``

        Raises:
            AppError: 当前未实现。
        """
        raise AppError(
            code="not_implemented",
            message="大模型解析器尚未实现，敬请期待",
            retryable=False,
            fields={},
        )
