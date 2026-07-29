"""
XRD 转换工具入口 —— 供 IRIP 组件及外部调用的入口函数。

使用方式：
    from packages.components.builtin.ingestion.xrd_converter.convert import convert_xrd_file_to_json
    result = convert_xrd_file_to_json("/path/to/SMX1.txt")
"""

import json
import logging
from typing import Any

from packages.components.builtin.ingestion.xrd_converter.ras_raw_parser import parse_ras_raw
from packages.components.builtin.ingestion.xrd_converter.validator import XRDConverterError

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def convert_xrd_file_to_json(file_path: str) -> dict[str, Any]:
    """LLM Tool 入口：读取 XRD 原始文件，返回 {metadata, points, series} 结构化数据。

    本函数为确定性 Python 解析，不依赖 LLM 进行文件内容解析。
    LLM 只需调用本函数并传入文件路径即可。

    Args:
        file_path: XRD 原始文件（RAS_RAW 格式）的路径。

    Returns:
        结构化数据字典，包含三个顶层键：
        - metadata: 文件/样品级元数据（FILE_* 字段 + filename）
        - points: 非编号单值参数列表，每项 {name, value, unit}
        - series: 序列数据列表，每项 {name, columns, rows}
          （含 XRD 衍射谱、MEAS_COND_AXIS 多列序列等）

    Raises:
        UnsupportedFileFormatError: 文件格式不支持。
        FileReadError: 文件读取失败。
        InvalidRasRawStructureError: 文件结构不合法。
        XrdDataCountMismatchError: 数据点数量不一致。
        XrdDataParseError: XRD 数据行解析失败。
    """
    logger.info("开始转换 XRD 文件: %s", file_path)

    result = parse_ras_raw(file_path)

    logger.info(
        "转换完成: metadata=%d 项, points=%d 项, series=%d 项",
        len(result["metadata"]),
        len(result["points"]),
        len(result["series"]),
    )

    return result


def convert_xrd_file_to_json_string(file_path: str) -> str:
    """LLM Tool 入口（字符串版本）：返回 JSON 格式字符串。

    Args:
        file_path: XRD 原始文件路径。

    Returns:
        JSON 格式字符串。

    Raises:
        同 convert_xrd_file_to_json。
    """
    result = convert_xrd_file_to_json(file_path)
    return json.dumps(result, ensure_ascii=False, allow_nan=False)


# ============================================================
# 命令行入口
# ============================================================

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print(
            "用法: python -m packages.components.builtin.ingestion.xrd_converter.convert"
            " <RAS_RAW 文件路径>"
        )
        sys.exit(1)

    file_path = sys.argv[1]
    try:
        result = convert_xrd_file_to_json(file_path)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
    except XRDConverterError as exc:
        logger.error("转换失败: %s", exc)
        sys.exit(1)
