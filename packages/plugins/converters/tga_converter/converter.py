"""TGA/STA 解析器 —— 将 NETZSCH 同步热分析仪导出文件解析为 {metadata, points, series}。

输入 file_path，输出结构化 JSON。纯 Python 确定性解析，不依赖 LLM。

支持格式：NETZSCH STA 系列导出的 PrnDat TXT 文件（UTF-16LE 编码）。
文件结构：
  - 标头区：`键: 值` 格式的元信息（仪器、文件名、日期、样品等）
  - 分隔线后为列名行 + 单位行
  - 数据行：6 列空格分隔（温度/时间/DSC/TG/DTG/灵敏度）

使用方式：
    from packages.plugins.converters.tga_converter.converter import convert_tga_file_to_json
    result = convert_tga_file_to_json("/path/to/PrnDat_xxx.txt")

插件调用方式（由 registry 统一调度）：
    converter = plugin_registry.get("tga_converter")
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


class TGAConverterError(Exception):
    """TGA 转换器基础异常类。"""


class FileReadError(TGAConverterError):
    """文件读取失败。"""


class NoDataError(TGAConverterError):
    """文件中没有找到有效数据行。"""


class InvalidFormatError(TGAConverterError):
    """文件格式不符合预期。"""


# ============================================================
# 文件读取
# ============================================================


def _read_file(file_path: str) -> str:
    """读取文件，自动检测 UTF-16LE / UTF-8(BOM) / GBK 编码。"""
    if not os.path.isfile(file_path):
        raise FileReadError(f"文件不存在: {file_path}")

    # 先检测 BOM 判断编码
    with open(file_path, "rb") as f:
        bom = f.read(4)

    if bom[:2] == b"\xff\xfe":
        encoding = "utf-16-le"
    elif bom[:3] == b"\xef\xbb\xbf":
        encoding = "utf-8-sig"
    else:
        encoding = None

    if encoding:
        try:
            with open(file_path, encoding=encoding) as f:
                content = f.read()
            # 去掉可能残留的 BOM（utf-16 读取时可能保留 \ufeff）
            if content and content[0] == "\ufeff":
                content = content[1:]
            return content
        except UnicodeDecodeError:
            pass

    for enc in ("utf-8-sig", "gbk", "latin-1"):
        try:
            with open(file_path, encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue

    raise FileReadError(f"文件 {file_path} 无法用常见编码读取")


# ============================================================
# 标头解析
# ============================================================

# 匹配 `键: 值` 格式（中文冒号或英文冒号，键后可有空格）
_HEADER_LINE_PATTERN = re.compile(r"^(.+?)[:：]\s*(.*)$")

# 匹配分隔线（全部由 - 组成）
_SEPARATOR_PATTERN = re.compile(r"^-{10,}$")

# 匹配数据行：以数字开头，多个空格分隔的数值
_DATA_LINE_PATTERN = re.compile(
    r"^-?\d+\.?\d*(?:[eE][-+]?\d+)?"
    r"(?:\s+[-+]?\d+\.?\d*(?:[eE][-+]?\d+)?)+\s*$"
)


def _parse_header_line(line: str) -> tuple[str, str] | None:
    """解析 `键: 值` 格式的标头行。"""
    match = _HEADER_LINE_PATTERN.match(line)
    if match:
        key = match.group(1).strip()
        value = match.group(2).strip()
        if key:
            return key, value
    return None


def _parse_data_line(line: str) -> list[float] | None:
    """解析数值数据行，返回浮点数列表。"""
    line = line.strip()
    if not line:
        return None
    if _DATA_LINE_PATTERN.match(line):
        parts = line.split()
        try:
            return [float(p) for p in parts]
        except (ValueError, IndexError):
            return None
    return None


# ============================================================
# 值类型转换
# ============================================================


def _convert_value(raw: str) -> int | float | str | None:
    """将原始字符串值转换为合适的 JSON 类型。"""
    if raw == "" or raw == "--" or raw == "不可能":
        return None
    try:
        if "." not in raw and "e" not in raw and "E" not in raw:
            return int(raw)
        return float(raw)
    except ValueError:
        return raw


# ============================================================
# 标头键映射（中文 → 英文/标准化键名）
# ============================================================

_HEADER_KEY_MAP: dict[str, str] = {
    "仪器": "instrument",
    "项目": "project",
    "文件名": "source_file",
    "所使用的方法": "method_file",
    "日期 / 时间": "start_time",
    "结束日期 / 时间": "end_time",
    "实验室": "laboratory",
    "操作者": "operator",
    "测量模式": "measurement_mode",
    "测量类型": "measurement_type",
    "修正文件": "correction_file",
    "温度校正文件": "temp_correction_file",
    "灵敏度文件": "sensitivity_file",
    "坩埚类型": "crucible_type",
    "样品编号": "sample_number",
    "样品名称": "sample_name",
    "样品质量": "sample_mass",
    "坩埚质量": "crucible_mass",
    "参比名称": "reference_name",
    "参比质量": "reference_mass",
    "参比坩埚质量": "reference_crucible_mass",
    "样品材料": "sample_material",
    "样品测定模式": "sample_mode",
    "剩余的测量": "remaining_measurements",
    "气氛": "atmosphere",
    "段": "segment",
    "范围": "temperature_range",
    "修正 / 测量范围": "correction_range",
    "备注": "remarks",
}


# 列名映射（中文 → 英文+单位）
def _build_column_names(col_line: str, unit_line: str) -> list[str]:
    """根据列名行和单位行构建标准列名。

    Args:
        col_line: 列名行，如 "温度     时间    DSC       TG     DTG   灵敏度"
        unit_line: 单位行，如 "℃      min   mW/mg     %      %/min μV/mW"

    Returns:
        标准列名列表，如 ["温度 (℃)", "时间 (min)", "DSC (mW/mg)",
        "TG (%)", "DTG (%/min)", "灵敏度 (μV/mW)"]
    """
    col_names = col_line.split()
    unit_names = unit_line.split()

    columns: list[str] = []
    for i, name in enumerate(col_names):
        unit = unit_names[i] if i < len(unit_names) else ""
        if unit:
            columns.append(f"{name} ({unit})")
        else:
            columns.append(name)
    return columns


# ============================================================
# 核心解析
# ============================================================


def parse_tga(file_path: str) -> dict[str, Any]:
    """解析 NETZSCH STA 同步热分析文件，返回 {metadata, points, series}。

    文件格式：
    - 标头区：键值对（`键: 值`），含仪器型号、样品信息、实验条件等
    - 分隔线后为列名行 + 单位行 + 分隔线
    - 数据区：6 列数值（温度/时间/DSC/TG/DTG/灵敏度）
    """
    content = _read_file(file_path)
    lines = content.splitlines()

    # ============================================================
    # 阶段 1：解析标头区
    # ============================================================
    header_params: dict[str, str] = {}
    separator_indices: list[int] = []
    col_line: str | None = None
    unit_line: str | None = None

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue

        # 分隔线
        if _SEPARATOR_PATTERN.match(stripped):
            separator_indices.append(i)
            continue

        # 标头键值对
        header_result = _parse_header_line(stripped)
        if header_result is not None and not _DATA_LINE_PATTERN.match(stripped):
            key, value = header_result
            header_params[key] = value
            continue

        # 列名行：第一个分隔线之后、非纯数字的行
        if separator_indices and col_line is None and not _DATA_LINE_PATTERN.match(stripped):
            col_line = stripped
            continue

        # 单位行：列名行之后的非纯数字行
        if (
            separator_indices
            and col_line is not None
            and unit_line is None
            and not _DATA_LINE_PATTERN.match(stripped)
        ):
            unit_line = stripped
            continue

    if not header_params:
        raise InvalidFormatError("未找到任何标头信息，可能不是 NETZSCH STA 文件")

    # 构建列名
    if col_line and unit_line:
        columns = _build_column_names(col_line, unit_line)
    else:
        # 回退默认列名
        columns = [
            "温度 (℃)",
            "时间 (min)",
            "DSC (mW/mg)",
            "TG (%)",
            "DTG (%/min)",
            "灵敏度 (μV/mW)",
        ]

    logger.info(
        "TGA 标头解析: %d 项, 列: %s",
        len(header_params),
        columns,
    )

    # ============================================================
    # 阶段 2：解析数据区
    # ============================================================
    data_rows: list[list[float]] = []
    skipped_lines = 0

    # 数据行从最后一个分隔线之后开始
    data_start = separator_indices[-1] if separator_indices else 0

    for i, line in enumerate(lines):
        if i <= data_start:
            continue
        result = _parse_data_line(line)
        if result is not None:
            if len(result) >= 2:
                data_rows.append(result[: len(columns)])
            else:
                skipped_lines += 1
        else:
            stripped = line.strip()
            if stripped and not _SEPARATOR_PATTERN.match(stripped):
                skipped_lines += 1
                logger.debug("第 %d 行跳过: %s", i + 1, stripped[:80])

    if not data_rows:
        raise NoDataError(f"文件 {file_path} 中未找到有效数据行")

    logger.info(
        "TGA 数据解析完成: %s, 有效数据 %d 条, 跳过 %d 行",
        os.path.basename(file_path),
        len(data_rows),
        skipped_lines,
    )

    # ============================================================
    # 构建 metadata
    # ============================================================
    metadata: dict[str, Any] = {
        "filename": os.path.basename(file_path),
        "instrument": header_params.get("仪器", ""),
        "measurement_mode": header_params.get("测量模式", ""),
        "sample_name": header_params.get("样品名称", ""),
        "sample_mass": header_params.get("样品质量", ""),
        "atmosphere": header_params.get("气氛", ""),
        "temperature_range": header_params.get("范围", ""),
        "start_time": header_params.get("日期 / 时间", ""),
        "end_time": header_params.get("结束日期 / 时间", ""),
        "crucible_type": header_params.get("坩埚类型", ""),
        "data_points": len(data_rows),
    }

    # ============================================================
    # 构建 points（无独立单值指标）
    # ============================================================
    points: list[dict[str, Any]] = []

    # ============================================================
    # 构建 series
    # ============================================================
    series: list[dict[str, Any]] = [
        {
            "name": "热分析曲线",
            "columns": columns,
            "rows": data_rows,
        }
    ]

    return {"metadata": metadata, "points": points, "series": series}


# ============================================================
# 入口函数
# ============================================================


def convert_tga_file_to_json(file_path: str) -> dict[str, Any]:
    """读取同步热分析原始文件，返回 {metadata, points, series} 结构化数据。"""
    logger.info("开始转换 TGA 文件: %s", file_path)
    result = parse_tga(file_path)
    logger.info(
        "转换完成: metadata=%d 项, points=%d 项, series=%d 项, 数据点=%d",
        len(result["metadata"]),
        len(result["points"]),
        len(result["series"]),
        len(result["series"][0]["rows"]) if result["series"] else 0,
    )
    return result


def convert_tga_file_to_json_string(file_path: str) -> str:
    """入口函数（字符串版本）：返回 JSON 格式字符串。"""
    return json.dumps(convert_tga_file_to_json(file_path), ensure_ascii=False, allow_nan=False)


# ============================================================
# 插件接口
# ============================================================


class TgaConverter:
    """TGA/STA 同步热分析文件确定性解析器，实现 ConverterProtocol。"""

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        """解析同步热分析原始文件，返回结构化数据。

        Args:
            params: 参数字典，包含 file_path（必填）。

        Returns:
            {"metadata": {...}, "points": [...], "series": [...]}
        """
        file_path = params["file_path"]
        result: dict[str, Any] = await asyncio.to_thread(convert_tga_file_to_json, str(file_path))
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
            "用法: python -m packages.plugins.converters.tga_converter.converter"
            " <NETZSCH STA 导出文件路径>"
        )
        sys.exit(1)

    try:
        result = convert_tga_file_to_json(sys.argv[1])
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
    except TGAConverterError as exc:
        logger.error("转换失败: %s", exc)
        sys.exit(1)
