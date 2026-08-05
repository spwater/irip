"""Raman 解析器 —— 将拉曼光谱 TXT 文件解析为 {metadata, points, series}。

输入 file_path，输出结构化 JSON。纯 Python 确定性解析，不依赖 LLM。

支持格式：两列数值（Tab 或空格分隔），第一列拉曼位移 (cm⁻¹)，第二列光谱强度。
忽略空行和非数据行（如表头、注释）。

使用方式：
    from packages.plugins.converters.raman_converter.converter import convert_raman_file_to_json
    result = convert_raman_file_to_json("/path/to/BL-1.txt")

插件调用方式（由 registry 统一调度）：
    converter = plugin_registry.get("raman_converter")
    result = await converter.execute({"file_path": "..."})
"""

import asyncio
import json
import logging
import os
import re
from typing import Any

from packages.plugins.protocol import ConverterResult

logger = logging.getLogger(__name__)

# ============================================================
# 异常定义
# ============================================================


class RamanConverterError(Exception):
    """Raman 转换器基础异常类。"""


class FileReadError(RamanConverterError):
    """文件读取失败。"""


class NoDataError(RamanConverterError):
    """文件中没有找到有效数据行。"""


# ============================================================
# 文件读取
# ============================================================


def _read_file(file_path: str) -> str:
    """读取文件，支持 UTF-8（含 BOM）和 GBK 编码自动回退。"""
    if not os.path.isfile(file_path):
        raise FileReadError(f"文件不存在: {file_path}")

    for encoding in ("utf-8-sig", "gbk", "latin-1"):
        try:
            with open(file_path, encoding=encoding) as f:
                return f.read()
        except UnicodeDecodeError:
            continue

    raise FileReadError(f"文件 {file_path} 无法用 UTF-8 / GBK / Latin-1 编码读取")


# ============================================================
# 数据行解析
# ============================================================

# 匹配两列数值（整数或浮点数，可带正负号），用 Tab 或空格分隔
_DATA_LINE_PATTERN = re.compile(r"^-?\d+\.?\d*(?:[eE][-+]?\d+)?\s+[-+]?\d+\.?\d*(?:[eE][-+]?\d+)?$")


def _parse_data_line(line: str) -> tuple[float, float] | None:
    """解析两列数值数据行，返回 (x, y) 或 None。"""
    line = line.strip()
    if not line:
        return None
    if _DATA_LINE_PATTERN.match(line):
        parts = line.split()
        try:
            return float(parts[0]), float(parts[1])
        except (ValueError, IndexError):
            return None
    return None


# ============================================================
# 核心解析
# ============================================================


def parse_raman(file_path: str) -> dict[str, Any]:
    """解析拉曼光谱 TXT 文件，返回 {metadata, points, series}。

    文件格式：两列数值（Tab 或空格分隔）
    - 第一列：拉曼位移 (cm⁻¹)
    - 第二列：光谱强度（原始计数值或任意强度单位）

    忽略空行和无法解析的非数据行。
    """
    content = _read_file(file_path)
    lines = content.splitlines()

    data_points: list[tuple[float, float]] = []
    skipped_lines = 0

    for line_num, raw_line in enumerate(lines, start=1):
        result = _parse_data_line(raw_line)
        if result is not None:
            data_points.append(result)
        else:
            stripped = raw_line.strip()
            if stripped:  # 非空行但无法解析
                skipped_lines += 1
                logger.debug("第 %d 行跳过: %s", line_num, stripped[:80])

    if not data_points:
        raise NoDataError(f"文件 {file_path} 中未找到有效数据行")

    logger.info(
        "Raman 解析完成: %s, 有效数据 %d 条, 跳过 %d 行",
        os.path.basename(file_path),
        len(data_points),
        skipped_lines,
    )

    # metadata: 文件级标头信息
    metadata: dict[str, Any] = {
        "filename": os.path.basename(file_path),
        "data_points": len(data_points),
        "x_min": data_points[0][0] if data_points else None,
        "x_max": data_points[-1][0] if data_points else None,
        "x_unit": "cm⁻¹",
        "y_description": "光谱强度",
    }

    # points: 无独立单值结果
    points: list[dict[str, Any]] = []

    # series: 全部两列数据作为一组连续拉曼光谱序列
    # 格式与 xrd_converter 一致：{name, columns, rows}
    col_x = "拉曼位移 (cm⁻¹)"
    col_y = "光谱强度"
    series: list[dict[str, Any]] = [
        {
            "name": "拉曼光谱",
            "columns": [col_x, col_y],
            "rows": [[x, y] for x, y in data_points],
        }
    ]

    return {"metadata": metadata, "points": points, "series": series}


# ============================================================
# 入口函数
# ============================================================


def convert_raman_file_to_json(file_path: str) -> dict[str, Any]:
    """读取拉曼光谱原始文件，返回 {metadata, points, series} 结构化数据。"""
    logger.info("开始转换 Raman 文件: %s", file_path)
    result = parse_raman(file_path)
    logger.info(
        "转换完成: metadata=%d 项, points=%d 项, series=%d 项, 数据点=%d",
        len(result["metadata"]),
        len(result["points"]),
        len(result["series"]),
        len(result["series"][0]["rows"]) if result["series"] else 0,
    )
    return result


def convert_raman_file_to_json_string(file_path: str) -> str:
    """入口函数（字符串版本）：返回 JSON 格式字符串。"""
    return json.dumps(convert_raman_file_to_json(file_path), ensure_ascii=False, allow_nan=False)


# ============================================================
# 插件接口
# ============================================================


class RamanConverter:
    """Raman 拉曼光谱 TXT 文件确定性解析器，实现 ConverterProtocol。"""

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        """解析拉曼光谱原始文件，返回结构化数据。

        Args:
            params: 参数字典，包含 file_path（必填）。

        Returns:
            {"metadata": {...}, "points": [...], "series": [...]}
        """
        file_path = params["file_path"]
        result: dict[str, Any] = await asyncio.to_thread(convert_raman_file_to_json, str(file_path))
        return ConverterResult(
            metadata=result.get("metadata", {}),
            points=result.get("points", []),
            series=result.get("series", []),
        ).to_dict()


# ============================================================
# 命令行入口
# ============================================================

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print(
            "用法: python -m packages.plugins.converters.raman_converter.converter"
            " <拉曼光谱 TXT 文件路径>"
        )
        sys.exit(1)

    try:
        result = convert_raman_file_to_json(sys.argv[1])
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
    except RamanConverterError as exc:
        logger.error("转换失败: %s", exc)
        sys.exit(1)
