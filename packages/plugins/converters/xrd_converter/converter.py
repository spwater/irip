"""XRD 解析器 —— 将 Rigaku SmartLab RAS_RAW 文件解析为 {metadata, points, series}。

输入 file_path，输出结构化 JSON。纯 Python 确定性解析，不依赖 LLM。

使用方式：
    from packages.plugins.converters.xrd_converter.converter import convert_xrd_file_to_json
    result = convert_xrd_file_to_json("/path/to/SMX1.txt")

插件调用方式（由 registry 统一调度）：
    converter = plugin_registry.get("xrd_converter")
    result = await converter.execute({"file_path": "..."})
"""

import asyncio
import json
import logging
import math
import os
import re
from typing import Any

from packages.plugins.protocol import ConverterResult

logger = logging.getLogger(__name__)

# ============================================================
# 异常定义
# ============================================================


class XRDConverterError(Exception):
    """XRD 转换器基础异常类。"""


class UnsupportedFileFormatError(XRDConverterError):
    """不支持的文件格式。"""


class FileReadError(XRDConverterError):
    """文件读取失败。"""


class InvalidRasRawStructureError(XRDConverterError):
    """RAS_RAW 文件结构不合法。"""


class XrdDataParseError(XRDConverterError):
    """XRD 数据行解析失败。"""


# ============================================================
# 类型转换（原 type_converter.py）
# ============================================================

_INT_PATTERN = re.compile(r"^-?\d+$")
_FLOAT_PATTERN = re.compile(r"^-?\d+\.\d+$")


def convert_value(raw: str) -> int | float | str | None:
    """将原始字符串值转换为合适的 JSON 类型。"""
    if raw == "":
        return None
    if _INT_PATTERN.match(raw):
        return int(raw)
    if _FLOAT_PATTERN.match(raw):
        return float(raw)
    return raw


# ============================================================
# 常量定义
# ============================================================

UNIT_MAPPING: dict[str, str] = {
    "MEAS_COND_XG_VOLTAGE": "HW_XG_VOLTAGE_UNIT",
    "MEAS_COND_XG_CURRENT": "HW_XG_CURRENT_UNIT",
    "HW_XG_WAVE_LENGTH_ALPHA1": "HW_XG_WAVE_LENGTH_UNIT",
    "HW_XG_WAVE_LENGTH_ALPHA2": "HW_XG_WAVE_LENGTH_UNIT",
    "HW_XG_WAVE_LENGTH_BETA": "HW_XG_WAVE_LENGTH_UNIT",
    "MEAS_SCAN_SPEED": "MEAS_SCAN_SPEED_UNIT",
    "MEAS_SCAN_START": "MEAS_SCAN_UNIT_X",
    "MEAS_SCAN_STOP": "MEAS_SCAN_UNIT_X",
    "MEAS_SCAN_STEP": "MEAS_SCAN_UNIT_X",
    "MEAS_SCAN_RESOLUTION_X": "MEAS_SCAN_UNIT_X",
}

MEAS_COND_AXIS_SUBFIELDS: list[str] = [
    "NAME",
    "NAME_INTERNAL",
    "OFFSET",
    "POSITION",
    "RESOLUTION",
    "STATE",
    "UNIT",
    "SPEED",
    "SPEED_UNIT",
    "SPEED_RESOLUTION",
]

_MEAS_COND_AXIS_PREFIX = "MEAS_COND_AXIS_"
_INDEX_PATTERN = re.compile(r"-(\d+)$")
_XRD_DATA_LINE_PATTERN = re.compile(r"^\d+\.?\d*\s+\d+\.?\d*$")
_STAR_LINE_PATTERN = re.compile(r'^\*(\S+)\s+"(.*)"$')
_HASH_LINE_PATTERN = re.compile(r"^#([^=]+)=(.*)$")


# ============================================================
# 文件读取
# ============================================================


def _read_file(file_path: str) -> str:
    """读取文件，支持 UTF-8（含 BOM）和 GBK 编码自动回退。"""
    if not os.path.isfile(file_path):
        raise FileReadError(f"文件不存在: {file_path}")

    for encoding in ("utf-8-sig", "gbk"):
        try:
            with open(file_path, encoding=encoding) as f:
                return f.read()
        except UnicodeDecodeError:
            continue

    raise FileReadError(f"文件 {file_path} 无法用 UTF-8 或 GBK 编码读取")


# ============================================================
# 行解析
# ============================================================


def _parse_star_line(line: str) -> tuple[str, str] | None:
    """解析 *KEY "VALUE" 格式的行。"""
    match = _STAR_LINE_PATTERN.match(line)
    if match:
        return match.group(1), match.group(2)
    return None


def _parse_hash_line(line: str) -> tuple[str, str] | None:
    """解析 #KEY=VALUE 格式的行。"""
    match = _HASH_LINE_PATTERN.match(line)
    if match:
        return match.group(1), match.group(2)
    return None


def _parse_xrd_data_line(line: str) -> tuple[float, float] | None:
    """解析 XRD 曲线数据行（两列数字）。"""
    if _XRD_DATA_LINE_PATTERN.match(line):
        parts = line.split()
        try:
            return float(parts[0]), float(parts[1])
        except (ValueError, IndexError) as exc:
            raise XrdDataParseError(f"XRD 数据行解析失败: {line} ({exc})") from exc
    return None


# ============================================================
# 编号字段处理
# ============================================================


def _extract_index(key: str) -> int | None:
    """从字段名末尾提取编号（如 KEY-0 → 0）。"""
    match = _INDEX_PATTERN.search(key)
    if match:
        return int(match.group(1))
    return None


def _strip_index(key: str) -> str:
    """去除字段名末尾的编号后缀。"""
    match = _INDEX_PATTERN.search(key)
    if match:
        return key[: match.start()]
    return key


def _get_group_prefix(base_name: str) -> str:
    """获取编号字段的分组前缀（去除最后一个下划线组件）。"""
    idx = base_name.rfind("_")
    if idx > 0:
        return base_name[:idx]
    return base_name


# ============================================================
# 单位查找
# ============================================================


def _lookup_unit(field_name: str, star_params: dict[str, str]) -> tuple[str, str | None]:
    """查找字段对应的单位。"""
    if field_name in UNIT_MAPPING:
        source_field = UNIT_MAPPING[field_name]
        if source_field in star_params:
            return star_params[source_field], source_field

    unit_field = field_name + "_UNIT"
    if unit_field in star_params:
        return star_params[unit_field], unit_field

    return "", None


def _collect_unit_source_fields(star_params: dict[str, str]) -> set[str]:
    """收集所有作为单位来源的字段名（不单独输出为 point）。"""
    unit_sources: set[str] = set()

    for source_field in UNIT_MAPPING.values():
        if source_field in star_params:
            unit_sources.add(source_field)

    for key in star_params:
        if key + "_UNIT" in star_params:
            unit_sources.add(key + "_UNIT")

    return unit_sources


# ============================================================
# 序列构建
# ============================================================


def _build_meas_cond_axis_series(
    numbered_fields: dict[str, dict[int, str]],
) -> dict[str, Any] | None:
    """构建 MEAS_COND_AXIS 多列序列。"""
    subfield_data: dict[str, dict[int, str]] = {}
    for subfield in MEAS_COND_AXIS_SUBFIELDS:
        full_key = _MEAS_COND_AXIS_PREFIX + subfield
        if full_key in numbered_fields:
            subfield_data[subfield] = numbered_fields[full_key]

    if not subfield_data:
        return None

    all_indices: set[int] = set()
    for data in subfield_data.values():
        all_indices.update(data.keys())

    if not all_indices:
        return None

    columns = ["index"] + MEAS_COND_AXIS_SUBFIELDS
    sorted_indices = sorted(all_indices)
    rows: list[list[Any]] = []
    for idx in sorted_indices:
        row: list[Any] = [idx]
        for subfield in MEAS_COND_AXIS_SUBFIELDS:
            if subfield in subfield_data and idx in subfield_data[subfield]:
                row.append(convert_value(subfield_data[subfield][idx]))
            else:
                row.append(None)
        rows.append(row)

    return {"name": "MEAS_COND_AXIS", "columns": columns, "rows": rows}


def _build_other_numbered_series(
    numbered_fields: dict[str, dict[int, str]],
) -> list[dict[str, Any]]:
    """构建非 MEAS_COND_AXIS 的编号字段序列。"""
    non_axis_fields = {
        k: v for k, v in numbered_fields.items() if not k.startswith(_MEAS_COND_AXIS_PREFIX)
    }

    groups: dict[str, list[str]] = {}
    for base_name in non_axis_fields:
        prefix = _get_group_prefix(base_name)
        groups.setdefault(prefix, []).append(base_name)

    series_list: list[dict[str, Any]] = []
    for prefix, base_names in sorted(groups.items()):
        base_names = sorted(base_names)

        if len(base_names) == 1:
            base_name = base_names[0]
            data = non_axis_fields[base_name]
            sorted_indices = sorted(data.keys())
            rows = [[idx, convert_value(data[idx])] for idx in sorted_indices]
            series_list.append({"name": base_name, "columns": ["index", "value"], "rows": rows})
        else:
            all_indices: set[int] = set()
            for bn in base_names:
                all_indices.update(non_axis_fields[bn].keys())
            sorted_indices = sorted(all_indices)
            rows = []
            for idx in sorted_indices:
                row: list[Any] = [idx]
                for bn in base_names:
                    if idx in non_axis_fields[bn]:
                        row.append(convert_value(non_axis_fields[bn][idx]))
                    else:
                        row.append(None)
                rows.append(row)
            series_list.append({"name": prefix, "columns": ["index"] + base_names, "rows": rows})

    return series_list


def _build_xrd_series(
    xrd_data: list[tuple[float, float]],
    star_params: dict[str, str],
    hash_params: dict[str, str],
) -> dict[str, Any]:
    """构建 XRD 衍射谱序列。"""
    axis_x = star_params.get("MEAS_SCAN_AXIS_X", "")
    unit_x = star_params.get("MEAS_SCAN_UNIT_X", "")
    intensity_unit = hash_params.get("Intensity_unit", "") or star_params.get(
        "MEAS_SCAN_UNIT_Y", ""
    )

    col_x = f"{axis_x} ({unit_x})" if unit_x else axis_x
    col_y = f"Intensity ({intensity_unit})" if intensity_unit else "Intensity"

    return {
        "name": "XRD衍射谱",
        "columns": [col_x, col_y],
        "rows": [[x, y] for x, y in xrd_data],
    }


# ============================================================
# 完整性校验（原 validator.py）
# ============================================================


def _check_no_nan_inf(value: Any) -> bool:
    """递归检查值中是否存在 NaN 或 Infinity。"""
    if isinstance(value, float):
        return math.isnan(value) or math.isinf(value)
    if isinstance(value, dict):
        return any(_check_no_nan_inf(v) for v in value.values())
    if isinstance(value, list):
        return any(_check_no_nan_inf(item) for item in value)
    return False


def validate_result(
    metadata: dict[str, Any],
    points: list[dict[str, Any]],
    series: list[dict[str, Any]],
    params: dict[str, Any],
) -> None:
    """校验解析结果的完整性，不通过则抛出异常。"""
    declared_count = params.get("MEAS_DATA_COUNT")
    xrd_data: list[tuple[float, float]] = params.get("xrd_data", [])
    actual_count = len(xrd_data)

    if declared_count is not None and declared_count != actual_count:
        logger.warning("数据点数量不一致：声明 %s 条，实际 %s 条", declared_count, actual_count)

    if actual_count == 0:
        return

    scan_start = params.get("MEAS_SCAN_START")
    scan_stop = params.get("MEAS_SCAN_STOP")

    if scan_start is not None and abs(xrd_data[0][0] - scan_start) > 1e-4:
        logger.warning("首条 X 值 %s 与 MEAS_SCAN_START %s 不一致", xrd_data[0][0], scan_start)

    if scan_stop is not None and abs(xrd_data[-1][0] - scan_stop) > 1e-4:
        logger.warning("末条 X 值 %s 与 MEAS_SCAN_STOP %s 不一致", xrd_data[-1][0], scan_stop)

    for i in range(1, actual_count):
        if xrd_data[i][0] <= xrd_data[i - 1][0]:
            logger.warning(
                "X 值非单调递增：第 %d 条 %s <= 第 %d 条 %s",
                i,
                xrd_data[i][0],
                i - 1,
                xrd_data[i - 1][0],
            )
            break

    unequally_spaced = params.get("MEAS_SCAN_UNEQUALY_SPACED", "False")
    scan_step = params.get("MEAS_SCAN_STEP")

    if unequally_spaced == "False" and scan_step is not None:
        mismatch_count = sum(
            1
            for i in range(1, actual_count)
            if abs(xrd_data[i][0] - xrd_data[i - 1][0] - scan_step) > 1e-4
        )
        if mismatch_count > 0:
            logger.warning(
                "步长不一致：%d 条数据步长与声明步长 %s 偏差超过容差", mismatch_count, scan_step
            )

    full_result = {"metadata": metadata, "points": points, "series": series}
    if _check_no_nan_inf(full_result):
        raise InvalidRasRawStructureError("解析结果中存在 NaN 或 Infinity，无法序列化为合法 JSON")

    try:
        json.dumps(full_result, allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise InvalidRasRawStructureError(f"结果无法序列化为合法 JSON: {exc}") from exc


# ============================================================
# 核心解析（原 ras_raw_parser.py parse_ras_raw）
# ============================================================


def parse_ras_raw(file_path: str) -> dict[str, Any]:
    """解析 RAS_RAW 文件，返回 {metadata, points, series}。"""
    content = _read_file(file_path)
    lines = content.splitlines()

    star_params: dict[str, str] = {}
    hash_params: dict[str, str] = {}
    xrd_data: list[tuple[float, float]] = []

    for line_num, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
            continue

        star_result = _parse_star_line(line)
        if star_result is not None:
            star_params[star_result[0]] = star_result[1]
            continue

        hash_result = _parse_hash_line(line)
        if hash_result is not None:
            hash_params[hash_result[0]] = hash_result[1]
            continue

        xrd_result = _parse_xrd_data_line(line)
        if xrd_result is not None:
            xrd_data.append(xrd_result)
            continue

        logger.warning("第 %d 行无法识别: %s", line_num, line)

    # 文件格式识别
    file_type = star_params.get("FILE_TYPE", "")
    file_system = star_params.get("FILE_SYSTEM_NAME", "")
    if file_type != "RAS_RAW" or file_system != "SmartLabXE":
        raise UnsupportedFileFormatError(
            f"不支持的文件格式: FILE_TYPE={file_type!r}, FILE_SYSTEM_NAME={file_system!r}，"
            f"仅支持 RAS_RAW / SmartLabXE"
        )

    logger.info(
        "格式识别通过: 星号 %d 项, 井号 %d 项, XRD 数据 %d 条",
        len(star_params),
        len(hash_params),
        len(xrd_data),
    )

    # metadata
    metadata: dict[str, Any] = {}
    for key, raw_value in star_params.items():
        if key.startswith("FILE_"):
            metadata[key] = convert_value(raw_value)
    metadata["filename"] = os.path.basename(file_path)

    # 收集单位来源字段
    unit_source_fields = _collect_unit_source_fields(star_params)

    # 分离编号字段
    numbered_fields: dict[str, dict[int, str]] = {}
    for key, raw_value in star_params.items():
        if key.startswith("FILE_"):
            continue
        index = _extract_index(key)
        if index is not None:
            base_name = _strip_index(key)
            numbered_fields.setdefault(base_name, {})[index] = raw_value

    # points
    points: list[dict[str, Any]] = []
    for key, raw_value in star_params.items():
        if key.startswith("FILE_"):
            continue
        if _extract_index(key) is not None:
            continue
        if key in unit_source_fields:
            continue
        value = convert_value(raw_value)
        unit, _ = _lookup_unit(key, star_params)
        points.append({"name": key, "value": value, "unit": unit})

    for key, raw_value in hash_params.items():
        points.append({"name": key, "value": convert_value(raw_value), "unit": ""})

    # series
    series: list[dict[str, Any]] = []

    meas_axis_series = _build_meas_cond_axis_series(numbered_fields)
    if meas_axis_series is not None:
        series.append(meas_axis_series)

    series.extend(_build_other_numbered_series(numbered_fields))
    series.append(_build_xrd_series(xrd_data, star_params, hash_params))

    logger.info(
        "解析完成: metadata %d 项, points %d 项, series %d 项",
        len(metadata),
        len(points),
        len(series),
    )

    # 完整性校验
    validate_params: dict[str, Any] = {
        "MEAS_DATA_COUNT": convert_value(star_params.get("MEAS_DATA_COUNT", "")) or None,
        "MEAS_SCAN_START": convert_value(star_params.get("MEAS_SCAN_START", "")) or None,
        "MEAS_SCAN_STOP": convert_value(star_params.get("MEAS_SCAN_STOP", "")) or None,
        "MEAS_SCAN_STEP": convert_value(star_params.get("MEAS_SCAN_STEP", "")) or None,
        "MEAS_SCAN_UNEQUALY_SPACED": star_params.get("MEAS_SCAN_UNEQUALY_SPACED", "False"),
        "xrd_data": xrd_data,
    }
    validate_result(metadata, points, series, validate_params)

    return {"metadata": metadata, "points": points, "series": series}


# ============================================================
# 入口函数（原 convert.py）
# ============================================================


def convert_xrd_file_to_json(file_path: str) -> dict[str, Any]:
    """读取 XRD 原始文件，返回 {metadata, points, series} 结构化数据。"""
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
    """入口函数（字符串版本）：返回 JSON 格式字符串。"""
    return json.dumps(convert_xrd_file_to_json(file_path), ensure_ascii=False, allow_nan=False)


# ============================================================
# 插件接口（原 plugin.py）
# ============================================================


class XrdConverter:
    """XRD RAS/RAW 文件确定性解析器，实现 ConverterProtocol。"""

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        """解析 XRD 原始文件，返回结构化数据。

        Args:
            params: 参数字典，包含 file_path（必填）。

        Returns:
            {"metadata": {...}, "points": [...], "series": [...]}
        """
        file_path = params["file_path"]
        result: dict[str, Any] = await asyncio.to_thread(convert_xrd_file_to_json, str(file_path))
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
            "用法: python -m packages.plugins.converters.xrd_converter.converter <RAS_RAW 文件路径>"
        )
        sys.exit(1)

    try:
        result = convert_xrd_file_to_json(sys.argv[1])
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
    except XRDConverterError as exc:
        logger.error("转换失败: %s", exc)
        sys.exit(1)
