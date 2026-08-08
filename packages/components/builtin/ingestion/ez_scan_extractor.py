"""LLM 驱动的文档提取组件。

通过 plugins.registry 调用解析器插件（llm_converter / xrd_converter），
组件层负责 artifact 下载 + AI 配置注入 + ObservationTable 构建，
解析逻辑委托给插件。
"""

import logging
import os
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from packages.common.errors import AppError
from packages.components.builtin.types import ObservationTable
from packages.components.sdk import ComponentContext, ComponentResult
from packages.plugins import registry as plugin_registry


class EZScanExtractor:
    """LLM 驱动的文档提取组件。

    通过插件注册表调用解析器：
    - llm_converter（默认）：大模型解析
    - xrd_converter：XRD 确定性解析
    """

    async def execute(
        self,
        context: ComponentContext,
        params: dict[str, Any],
    ) -> ComponentResult:
        """下载 artifact → 获取 AI 配置 → 调用插件 → 构建 ObservationTable。"""
        tool_type: str = params.get("tool_type", "llm_converter")
        path_str: str = params["path"]

        # 支持 artifact:{artifact_id} 格式
        is_temp_file = False
        if path_str.startswith("artifact:"):
            file_path = await self._download_artifact_to_temp(context, path_str[len("artifact:") :])
            is_temp_file = True
        else:
            file_path = Path(path_str)

        try:
            # 获取 AI 配置（llm_converter 需要）
            ai_config: dict[str, Any] | None = None
            if tool_type == "llm_converter":
                if context.ai_config_provider is None:
                    raise AppError(
                        code="ai_not_configured",
                        message="AI 配置提供器未注入，无法获取大模型配置",
                        retryable=False,
                    )
                ai_config = await context.ai_config_provider()
                if ai_config is None:
                    raise AppError(
                        code="ai_not_configured",
                        message="AI 大模型未配置，请在平台治理 → AI 配置中开启",
                        retryable=False,
                    )

            # 通过插件注册表调用解析器
            converter = plugin_registry.get(tool_type)
            if converter is None:
                raise AppError(
                    code="missing_dependency",
                    message=f"解析器插件 '{tool_type}' 未注册",
                    retryable=False,
                    fields={"tool_type": tool_type},
                )

            result: dict[str, Any] = await converter.execute(
                {
                    **params,
                    "file_path": str(file_path),
                    "ai_config": ai_config,
                }
            )

            # 构建 ObservationTable
            points: list[dict[str, Any]] = result.get("points", [])
            series: list[dict[str, Any]] = result.get("series", [])
            header: dict[str, Any] = result.get("metadata", {})

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
                summary=f"提取 {len(points)} 个指标，{len(series)} 组序列",
                metadata={
                    "row_count": len(points),
                    "header": header,
                    "points": points,
                    "series": series,
                },
            )
        finally:
            if is_temp_file:
                file_path.unlink(missing_ok=True)

    @staticmethod
    async def _download_artifact_to_temp(
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

        fd, temp_path = tempfile.mkstemp(suffix=_guess_suffix(data))
        try:
            os.write(fd, data)
        finally:
            os.close(fd)

        return Path(temp_path)


def _guess_suffix(data: bytes) -> str:
    """根据文件内容魔数推断文件后缀。"""
    if data[:4] == b"%PDF":
        return ".pdf"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:2] == b"\xff\xd8":
        return ".jpg"
    if data[:4] == b"PK\x03\x04":
        try:
            import io
            import zipfile

            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = zf.namelist()
                if any(n.startswith("word/") for n in names):
                    return ".docx"
                if any(n.startswith("xl/") for n in names):
                    return ".xlsx"
        except Exception:
            logging.getLogger(__name__).warning("unexpected error", exc_info=True)
        return ".xlsx"
    return ""
