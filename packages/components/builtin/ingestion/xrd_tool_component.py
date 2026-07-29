"""XRD RAS_RAW 文件确定性解析组件。

调用 xrd_converter.convert_xrd_file_to_json 工具，
将 Rigaku SmartLab RAS_RAW 文件解析为 {metadata, points, series}。

不依赖 LLM，纯 Python 确定性解析，适用于已知格式的 XRD 原始文件。

参数（全部在 manifest 定义）：
- path: 文件路径（必填），支持两种格式：
  - artifact:{artifact_id} — 从 MinIO 下载工件到临时文件再处理
  - 文件系统路径 — 直接读取本地文件
- tool_name: 使用的解析工具名称（默认 convert_xrd_file_to_json）
"""

import os
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from packages.common.errors import AppError
from packages.components.builtin.types import ObservationTable
from packages.components.sdk import ComponentContext, ComponentResult


class XrdToolComponent:
    """XRD RAS_RAW 文件确定性解析组件。

    调用 xrd_converter.convert_xrd_file_to_json 工具，
    将 Rigaku SmartLab RAS_RAW 文件解析为 {metadata, points, series}。
    """

    async def execute(
        self,
        context: ComponentContext,
        params: dict[str, Any],
    ) -> ComponentResult:
        """读取 XRD 原始文件 → 调用确定性解析器 → 返回 ObservationTable。

        Args:
            context: 组件执行上下文（提供 artifact_service 等）。
            params: 组件参数，至少包含 path。

        Returns:
            ComponentResult: 包含 observations（ObservationTable）、
            summary 和 metadata（含 header/points/series）。
        """
        path_str: str = params["path"]

        # 支持 artifact:{artifact_id} 格式：从 MinIO 下载到临时文件
        # 兼容旧格式：直接使用文件系统路径
        if path_str.startswith("artifact:"):
            file_path = await self._download_artifact(context, path_str[len("artifact:") :])
        else:
            file_path = Path(path_str)

        # 调用 XRD 解析工具（同步 I/O，用 to_thread 避免阻塞事件循环）
        import asyncio

        from packages.components.builtin.ingestion.xrd_converter.convert import (
            convert_xrd_file_to_json,
        )

        result: dict[str, Any] = await asyncio.to_thread(convert_xrd_file_to_json, str(file_path))

        # 解析结果
        points: list[dict[str, Any]] = result.get("points", [])
        series: list[dict[str, Any]] = result.get("series", [])
        header: dict[str, Any] = result.get("metadata", {})

        # 构建 ObservationTable（points 作为行，columns 固定 name/value/unit）
        if points:
            columns: tuple[str, ...] = ("name", "value", "unit")
        else:
            columns = ()

        rows: tuple[dict[str, Any], ...] = tuple(points)

        # 构建 source_locations
        source_locs: list[dict[str, Any]] = [
            {"file": file_path.name, "row": idx} for idx in range(1, len(rows) + 1)
        ]

        table = ObservationTable(
            columns=columns,
            rows=rows,
            source_locations=tuple(source_locs),
        )

        return ComponentResult(
            outputs={"observations": table},
            summary=f"XRD解析: {len(points)} 个指标, {len(series)} 组序列, metadata {len(header)} 项",  # noqa: E501
            metadata={
                "row_count": len(points),
                "header": header,
                "points": points,
                "series": series,
            },
        )

    @staticmethod
    async def _download_artifact(
        context: ComponentContext,
        artifact_id_str: str,
    ) -> Path:
        """从 MinIO 下载工件到临时文件，返回临时文件路径。

        通过 context.artifact_service.get_bytes() 下载工件内容，
        写入临时文件，返回 Path 对象。临时文件由调用方负责清理。

        Args:
            context: 组件执行上下文（提供 artifact_service）。
            artifact_id_str: 工件 ID 字符串。

        Returns:
            Path: 临时文件路径。

        Raises:
            AppError: code="missing_dependency"，当 artifact_service 未注入。
            AppError: code="not_found"，当工件不存在。
        """
        artifact_service = context.artifact_service
        if artifact_service is None:
            raise AppError(
                code="missing_dependency",
                message="artifact_service 未注入，无法下载 artifact",
                retryable=False,
                fields={},
            )

        artifact_id = UUID(artifact_id_str)
        data: bytes = await artifact_service.get_bytes(artifact_id)

        # 写入临时文件（XRD 文件为文本格式，不需要特殊后缀）
        fd, temp_path = tempfile.mkstemp(suffix=".txt")
        try:
            os.write(fd, data)
        finally:
            os.close(fd)

        return Path(temp_path)
