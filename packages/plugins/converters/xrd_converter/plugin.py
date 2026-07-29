"""XRD 解析器插件。

调用 xrd_converter.convert_xrd_file_to_json 工具，
将 Rigaku SmartLab RAS_RAW 文件解析为 {metadata, points, series}。

不依赖 LLM，纯 Python 确定性解析。
"""

import asyncio
from typing import Any

from packages.plugins.protocol import ConverterResult


class XrdConverter:
    """XRD RAS/RAW 文件确定性解析器。"""

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        """解析 XRD 原始文件，返回结构化数据。

        Args:
            params: 参数字典，包含:
                - file_path: 文件路径（必填）
                - tool_name: 使用的解析函数名（默认 convert_xrd_file_to_json）

        Returns:
            dict: ``{"metadata": {...}, "points": [...], "series": [...]}``
        """
        file_path = params["file_path"]
        params.get("tool_name", "convert_xrd_file_to_json")

        from packages.plugins.converters.xrd_converter.convert import (
            convert_xrd_file_to_json,
        )

        # 同步 I/O，用 to_thread 避免阻塞事件循环
        result: dict[str, Any] = await asyncio.to_thread(convert_xrd_file_to_json, str(file_path))

        return ConverterResult(
            metadata=result.get("metadata", {}),
            points=result.get("points", []),
            series=result.get("series", []),
        ).to_dict()
