"""XRD RAS_RAW 文件确定性解析组件。

通过 plugins.registry 调用 xrd_converter 插件，
将 Rigaku SmartLab RAS_RAW 文件解析为 {metadata, points, series}。

组件层负责 artifact 下载 + ObservationTable 构建，
解析逻辑委托给插件。
"""

import os
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from packages.common.errors import AppError
from packages.components.builtin.types import ObservationTable
from packages.components.sdk import ComponentContext, ComponentResult
from packages.plugins import registry as plugin_registry


class XrdToolComponent:
    """XRD RAS_RAW 文件确定性解析组件。"""

    async def execute(
        self,
        context: ComponentContext,
        params: dict[str, Any],
    ) -> ComponentResult:
        """读取 XRD 原始文件 → 调用插件解析 → 返回 ObservationTable。"""
        path_str: str = params["path"]

        # 支持 artifact:{artifact_id} 格式
        if path_str.startswith("artifact:"):
            file_path = await self._download_artifact(context, path_str[len("artifact:") :])
        else:
            file_path = Path(path_str)

        # 通过插件注册表调用解析器
        converter = plugin_registry.get("xrd_converter")
        if converter is None:
            raise AppError(
                code="missing_dependency",
                message="xrd_converter 插件未注册",
                retryable=False,
                fields={},
            )

        result: dict[str, Any] = await converter.execute(
            {
                "file_path": str(file_path),
                "tool_name": params.get("tool_name", "convert_xrd_file_to_json"),
            }
        )

        # 解析结果
        points: list[dict[str, Any]] = result.get("points", [])
        series: list[dict[str, Any]] = result.get("series", [])
        header: dict[str, Any] = result.get("metadata", {})

        # 构建 ObservationTable
        columns: tuple[str, ...] = ("name", "value", "unit") if points else ()
        rows: tuple[dict[str, Any], ...] = tuple(points)
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
        """从 MinIO 下载工件到临时文件。"""
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

        fd, temp_path = tempfile.mkstemp(suffix=".txt")
        try:
            os.write(fd, data)
        finally:
            os.close(fd)

        return Path(temp_path)
