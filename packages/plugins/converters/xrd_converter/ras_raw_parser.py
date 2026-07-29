"""
RAS_RAW 文件核心解析器 —— 将 Rigaku SmartLabXE 导出的 RAS_RAW 原始文件解析为
{metadata, points, series} 结构化数据。

主要函数：parse_ras_raw(file_path: str) -> dict
"""

import logging
import os
import re
from typing import Any

from .type_converter import convert_value
from .validator import (
    FileReadError,
    UnsupportedFileFormatError,
    XrdDataParseError,
    validate_result,
)

logger = logging.getLogger(__name__)

# ============================================================
# 常量定义
# ============================================================

# 显式单位映射表：字段名 → 单位来源字段名
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

# MEAS_COND_AXIS 子字段列表（按指定顺序）
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

# MEAS_COND_AXIS 前缀
_MEAS_COND_AXIS_PREFIX = "MEAS_COND_AXIS_"

# 提取编号后缀的正则：匹配 KEY 末尾的 -数字
_INDEX_PATTERN = re.compile(r"-(\d+)$")

# XRD 曲线数据行正则：两列数字（角度 + 强度）
_XRD_DATA_LINE_PATTERN = re.compile(r"^\d+\.?\d*\s+\d+\.?\d*$")

# 星号行正则：*KEY "VALUE"
_STAR_LINE_PATTERN = re.compile(r'^\*(\S+)\s+"(.*)"$')

# 井号行正则：#KEY=VALUE
_HASH_LINE_PATTERN = re.compile(r"^#([^=]+)=(.*)$")


# ============================================================
# 文件读取
# ============================================================


def _read_file(file_path: str) -> str:
    """读取文件内容，支持 UTF-8（含 BOM）和 GBK 编码自动识别回退。

    Args:
        file_path: 文件路径。

    Returns:
        文件内容字符串。

    Raises:
        FileReadError: 文件不存在或所有编码均无法解码。
    """
    if not os.path.isfile(file_path):
        raise FileReadError(f"文件不存在: {file_path}")

    # 尝试编码列表：UTF-8（自动处理 BOM）→ GBK 回退
    encodings = ["utf-8-sig", "gbk"]
    last_error: Exception | None = None

    for encoding in encodings:
        try:
            with open(file_path, encoding=encoding) as f:
                content = f.read()
            logger.debug("文件 %s 使用 %s 编码读取成功", file_path, encoding)
            return content
        except UnicodeDecodeError as exc:
            last_error = exc
            logger.debug("文件 %s 使用 %s 编码解码失败，尝试下一个编码", file_path, encoding)
            continue

    raise FileReadError(f"文件 {file_path} 无法用 UTF-8 或 GBK 编码读取: {last_error}")


# ============================================================
# 行解析
# ============================================================


def _parse_star_line(line: str) -> tuple[str, str] | None:
    """解析 *KEY "VALUE" 格式的行。

    Args:
        line: 去除首尾空白后的单行文本。

    Returns:
        (key, value) 元组，value 已去除双引号。如果不匹配则返回 None。
    """
    match = _STAR_LINE_PATTERN.match(line)
    if match:
        key = match.group(1)
        value = match.group(2)
        return key, value
    return None


def _parse_hash_line(line: str) -> tuple[str, str] | None:
    """解析 #KEY=VALUE 格式的行。

    Args:
        line: 去除首尾空白后的单行文本。

    Returns:
        (key, value) 元组，value 不做引号处理。如果不匹配则返回 None。
    """
    match = _HASH_LINE_PATTERN.match(line)
    if match:
        key = match.group(1)
        value = match.group(2)
        return key, value
    return None


def _parse_xrd_data_line(line: str) -> tuple[float, float] | None:
    """解析 XRD 曲线数据行（两列数字）。

    Args:
        line: 去除首尾空白后的单行文本。

    Returns:
        (x, y) 浮点数元组。如果不匹配则返回 None。

    Raises:
        XrdDataParseError: 数据行匹配但数值解析失败。
    """
    if _XRD_DATA_LINE_PATTERN.match(line):
        parts = line.split()
        try:
            x = float(parts[0])
            y = float(parts[1])
            return x, y
        except (ValueError, IndexError) as exc:
            raise XrdDataParseError(f"XRD 数据行解析失败: {line} ({exc})") from exc
    return None


# ============================================================
# 编号字段处理
# ============================================================


def _extract_index(key: str) -> int | None:
    """从字段名末尾提取编号。

    使用正则匹配字段末尾的编号后缀（如 -0、-100）。

    Args:
        key: 字段名（已去除 * 前缀）。

    Returns:
        编号整数，如果无编号后缀则返回 None。
    """
    match = _INDEX_PATTERN.search(key)
    if match:
        return int(match.group(1))
    return None


def _strip_index(key: str) -> str:
    """去除字段名末尾的编号后缀，返回基础字段名。

    Args:
        key: 字段名（已去除 * 前缀）。

    Returns:
        去除 -N 后缀的基础字段名。
    """
    match = _INDEX_PATTERN.search(key)
    if match:
        return key[: match.start()]
    return key


def _get_group_prefix(base_name: str) -> str:
    """获取编号字段的分组前缀（去除最后一个下划线分隔的组件）。

    例如：HW_COUNTER_ID → HW_COUNTER
          HW_COUNTER_NAME → HW_COUNTER
          HW_GONIOMETER_RADIUS → HW_GONIOMETER

    Args:
        base_name: 去除编号后缀的基础字段名。

    Returns:
        分组前缀。
    """
    idx = base_name.rfind("_")
    if idx > 0:
        return base_name[:idx]
    return base_name


# ============================================================
# 单位查找
# ============================================================


def _lookup_unit(
    field_name: str,
    star_params: dict[str, str],
) -> tuple[str, str | None]:
    """查找字段对应的单位。

    查找顺序：
      1. 显式映射表（UNIT_MAPPING）
      2. 同名 _UNIT 字段
      3. 空字符串

    Args:
        field_name: 字段名。
        star_params: 所有星号参数的原始键值映射。

    Returns:
        (unit, unit_source_field) 元组：
        - unit: 找到的单位字符串，未找到则为空字符串
        - unit_source_field: 单位来源字段名，未找到则为 None
    """
    # 1. 显式映射
    if field_name in UNIT_MAPPING:
        source_field = UNIT_MAPPING[field_name]
        if source_field in star_params:
            return star_params[source_field], source_field

    # 2. 同名 _UNIT 字段
    unit_field = field_name + "_UNIT"
    if unit_field in star_params:
        return star_params[unit_field], unit_field

    # 3. 空字符串
    return "", None


def _collect_unit_source_fields(star_params: dict[str, str]) -> set:
    """收集所有作为单位来源的字段名。

    这些字段不会被单独输出为 point。

    Args:
        star_params: 所有星号参数的原始键值映射。

    Returns:
        单位来源字段名集合。
    """
    unit_sources: set = set()

    # 从显式映射表中收集
    for source_field in UNIT_MAPPING.values():
        if source_field in star_params:
            unit_sources.add(source_field)

    # 从同名 _UNIT 字段中收集
    for key in star_params:
        unit_field = key + "_UNIT"
        if unit_field in star_params:
            unit_sources.add(unit_field)

    return unit_sources


# ============================================================
# 序列构建
# ============================================================


def _build_meas_cond_axis_series(
    numbered_fields: dict[str, dict[int, str]],
) -> dict[str, Any] | None:
    """构建 MEAS_COND_AXIS 多列序列。

    将 10 种子字段（NAME, NAME_INTERNAL, OFFSET, ...）按编号重组为多列序列。
    index 为编号（0-100），按编号排序，缺失填 null。

    Args:
        numbered_fields: 编号字段映射 {base_name: {index: raw_value}}。

    Returns:
        序列字典 {name, columns, rows}，无数据则返回 None。
    """
    # 收集所有 MEAS_COND_AXIS 子字段
    subfield_data: dict[str, dict[int, str]] = {}
    for subfield in MEAS_COND_AXIS_SUBFIELDS:
        full_key = _MEAS_COND_AXIS_PREFIX + subfield
        if full_key in numbered_fields:
            subfield_data[subfield] = numbered_fields[full_key]

    if not subfield_data:
        return None

    # 收集所有出现的编号
    all_indices: set = set()
    for data in subfield_data.values():
        all_indices.update(data.keys())

    if not all_indices:
        return None

    # 构建列名
    columns = ["index"] + MEAS_COND_AXIS_SUBFIELDS

    # 按编号排序，构建行
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

    return {
        "name": "MEAS_COND_AXIS",
        "columns": columns,
        "rows": rows,
    }


def _build_other_numbered_series(
    numbered_fields: dict[str, dict[int, str]],
) -> list[dict[str, Any]]:
    """构建非 MEAS_COND_AXIS 的编号字段序列。

    分组规则：按分组前缀（去除最后一个下划线组件）分组。
    - 单个基础字段名 → 单列序列 {columns: ["index", "value"]}
    - 多个基础字段名共享编号 → 多列序列

    Args:
        numbered_fields: 编号字段映射 {base_name: {index: raw_value}}。

    Returns:
        序列字典列表。
    """
    # 排除 MEAS_COND_AXIS 字段
    non_axis_fields: dict[str, dict[int, str]] = {}
    for base_name, data in numbered_fields.items():
        if not base_name.startswith(_MEAS_COND_AXIS_PREFIX):
            non_axis_fields[base_name] = data

    # 按分组前缀分组
    groups: dict[str, list[str]] = {}
    for base_name in non_axis_fields:
        prefix = _get_group_prefix(base_name)
        if prefix not in groups:
            groups[prefix] = []
        groups[prefix].append(base_name)

    series_list: list[dict[str, Any]] = []

    for prefix, base_names in sorted(groups.items()):
        # 对基础字段名排序，保证列顺序稳定
        base_names = sorted(base_names)

        if len(base_names) == 1:
            # 单列序列：columns = ["index", "value"]
            base_name = base_names[0]
            data = non_axis_fields[base_name]
            sorted_indices = sorted(data.keys())
            rows = [[idx, convert_value(data[idx])] for idx in sorted_indices]
            series_list.append(
                {
                    "name": base_name,
                    "columns": ["index", "value"],
                    "rows": rows,
                }
            )
        else:
            # 多列序列：columns = ["index", base_name1, base_name2, ...]
            all_indices: set = set()
            for bn in base_names:
                all_indices.update(non_axis_fields[bn].keys())

            sorted_indices = sorted(all_indices)
            rows: list[list[Any]] = []
            for idx in sorted_indices:
                row: list[Any] = [idx]
                for bn in base_names:
                    if idx in non_axis_fields[bn]:
                        row.append(convert_value(non_axis_fields[bn][idx]))
                    else:
                        row.append(None)
                rows.append(row)

            series_list.append(
                {
                    "name": prefix,
                    "columns": ["index"] + base_names,
                    "rows": rows,
                }
            )

    return series_list


def _build_xrd_series(
    xrd_data: list[tuple[float, float]],
    star_params: dict[str, str],
    hash_params: dict[str, str],
) -> dict[str, Any]:
    """构建 XRD 衍射谱序列。

    Args:
        xrd_data: XRD 数据列表 [(x, y), ...]。
        star_params: 星号参数映射。
        hash_params: 井号参数映射。

    Returns:
        序列字典 {name, columns, rows}。
    """
    # X 轴名称和单位
    axis_x = star_params.get("MEAS_SCAN_AXIS_X", "")
    unit_x = star_params.get("MEAS_SCAN_UNIT_X", "")

    # 强度单位：优先取 #Intensity_unit，回退到 MEAS_SCAN_UNIT_Y
    intensity_unit = hash_params.get("Intensity_unit", "")
    if not intensity_unit:
        intensity_unit = star_params.get("MEAS_SCAN_UNIT_Y", "")

    # 构建列名
    col_x = f"{axis_x} ({unit_x})" if unit_x else axis_x
    col_y = f"Intensity ({intensity_unit})" if intensity_unit else "Intensity"
    columns = [col_x, col_y]

    # 构建行数据（不截断不抽样）
    rows = [[x, y] for x, y in xrd_data]

    return {
        "name": "XRD衍射谱",
        "columns": columns,
        "rows": rows,
    }


# ============================================================
# 主解析函数
# ============================================================


def parse_ras_raw(file_path: str) -> dict:
    """解析 RAS_RAW 格式的 XRD 原始文件，返回结构化数据。

    Args:
        file_path: RAS_RAW 文件路径。

    Returns:
        {"metadata": {...}, "points": [...], "series": [...]} 结构字典。

    Raises:
        FileReadError: 文件读取失败。
        UnsupportedFileFormatError: 文件格式不支持。
        InvalidRasRawStructureError: 文件结构不合法。
        XrdDataCountMismatchError: 数据点数量不一致。
        XrdDataParseError: XRD 数据行解析失败。
    """
    # ----- 1. 读取文件 -----
    content = _read_file(file_path)
    lines = content.splitlines()

    # ----- 2. 逐行解析 -----
    star_params: dict[str, str] = {}  # *KEY → 原始值
    hash_params: dict[str, str] = {}  # #KEY → 原始值
    xrd_data: list[tuple[float, float]] = []

    for line_num, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
            continue

        # 尝试 *KEY "VALUE" 格式
        star_result = _parse_star_line(line)
        if star_result is not None:
            key, value = star_result
            star_params[key] = value
            continue

        # 尝试 #KEY=VALUE 格式
        hash_result = _parse_hash_line(line)
        if hash_result is not None:
            key, value = hash_result
            hash_params[key] = value
            continue

        # 尝试 XRD 数据行
        xrd_result = _parse_xrd_data_line(line)
        if xrd_result is not None:
            xrd_data.append(xrd_result)
            continue

        # 无法识别的非空行，记录日志
        logger.warning("第 %d 行无法识别: %s", line_num, line)

    # ----- 3. 文件格式识别 -----
    file_type = star_params.get("FILE_TYPE", "")
    file_system = star_params.get("FILE_SYSTEM_NAME", "")

    if file_type != "RAS_RAW" or file_system != "SmartLabXE":
        raise UnsupportedFileFormatError(
            f"不支持的文件格式: FILE_TYPE={file_type!r}, FILE_SYSTEM_NAME={file_system!r}，"
            f"仅支持 RAS_RAW / SmartLabXE"
        )

    logger.info(
        "文件格式识别通过: FILE_TYPE=RAS_RAW, FILE_SYSTEM_NAME=SmartLabXE, "
        "星号参数 %d 项, 井号参数 %d 项, XRD 数据 %d 条",
        len(star_params),
        len(hash_params),
        len(xrd_data),
    )

    # ----- 4. 构建 metadata -----
    metadata: dict[str, Any] = {}
    for key, raw_value in star_params.items():
        if key.startswith("FILE_"):
            metadata[key] = convert_value(raw_value)
    # 添加 filename
    metadata["filename"] = os.path.basename(file_path)

    # ----- 5. 收集单位来源字段（不单独输出为 point）-----
    unit_source_fields = _collect_unit_source_fields(star_params)

    # ----- 6. 分离编号字段与非编号字段 -----
    numbered_fields: dict[str, dict[int, str]] = {}  # base_name → {index → raw_value}

    for key, raw_value in star_params.items():
        # FILE_* 字段已进入 metadata，跳过
        if key.startswith("FILE_"):
            continue

        # 检查是否有编号后缀
        index = _extract_index(key)
        if index is not None:
            base_name = _strip_index(key)
            if base_name not in numbered_fields:
                numbered_fields[base_name] = {}
            numbered_fields[base_name][index] = raw_value

    # ----- 7. 构建 points -----
    points: list[dict[str, Any]] = []

    for key, raw_value in star_params.items():
        # FILE_* 字段已进入 metadata，跳过
        if key.startswith("FILE_"):
            continue

        # 编号字段进入 series，跳过
        if _extract_index(key) is not None:
            continue

        # 单位来源字段不单独输出，跳过
        if key in unit_source_fields:
            continue

        value = convert_value(raw_value)
        unit, _ = _lookup_unit(key, star_params)
        points.append({"name": key, "value": value, "unit": unit})

    # 井号参数也作为 points 输出
    for key, raw_value in hash_params.items():
        value = convert_value(raw_value)
        points.append({"name": key, "value": value, "unit": ""})

    # ----- 8. 构建 series -----
    series: list[dict[str, Any]] = []

    # MEAS_COND_AXIS 多列序列
    meas_axis_series = _build_meas_cond_axis_series(numbered_fields)
    if meas_axis_series is not None:
        series.append(meas_axis_series)

    # 其他编号字段序列
    other_series = _build_other_numbered_series(numbered_fields)
    series.extend(other_series)

    # XRD 衍射谱序列
    xrd_series = _build_xrd_series(xrd_data, star_params, hash_params)
    series.append(xrd_series)

    logger.info(
        "解析完成: metadata %d 项, points %d 项, series %d 项",
        len(metadata),
        len(points),
        len(series),
    )

    # ----- 9. 完整性校验 -----
    # 提取校验所需参数
    meas_data_count_raw = star_params.get("MEAS_DATA_COUNT", "")
    meas_scan_start_raw = star_params.get("MEAS_SCAN_START", "")
    meas_scan_stop_raw = star_params.get("MEAS_SCAN_STOP", "")
    meas_scan_step_raw = star_params.get("MEAS_SCAN_STEP", "")
    meas_scan_unequally_spaced = star_params.get("MEAS_SCAN_UNEQUALY_SPACED", "False")

    validate_params: dict[str, Any] = {
        "MEAS_DATA_COUNT": convert_value(meas_data_count_raw) if meas_data_count_raw else None,
        "MEAS_SCAN_START": convert_value(meas_scan_start_raw) if meas_scan_start_raw else None,
        "MEAS_SCAN_STOP": convert_value(meas_scan_stop_raw) if meas_scan_stop_raw else None,
        "MEAS_SCAN_STEP": convert_value(meas_scan_step_raw) if meas_scan_step_raw else None,
        "MEAS_SCAN_UNEQUALY_SPACED": meas_scan_unequally_spaced,
        "xrd_data": xrd_data,
    }

    validate_result(metadata, points, series, validate_params)

    # ----- 10. 返回结果 -----
    return {
        "metadata": metadata,
        "points": points,
        "series": series,
    }
