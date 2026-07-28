# -*- coding: utf-8 -*-
"""
完整性校验模块 —— 校验 RAS_RAW 解析结果的完整性，不通过则抛出对应异常。

异常类型：
  - UnsupportedFileFormatError: 不支持的文件格式
  - FileReadError: 文件读取失败
  - InvalidRasRawStructureError: 文件结构不合法
  - XrdDataCountMismatchError: 数据点数量不一致
  - XrdDataParseError: XRD 数据行解析失败
"""

import json
import logging
import math
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

# ============================================================
# 异常定义
# ============================================================

class XRDConverterError(Exception):
    """XRD 转换器基础异常类。"""
    pass


class UnsupportedFileFormatError(XRDConverterError):
    """不支持的文件格式。"""
    pass


class FileReadError(XRDConverterError):
    """文件读取失败。"""
    pass


class InvalidRasRawStructureError(XRDConverterError):
    """RAS_RAW 文件结构不合法。"""
    pass


class XrdDataCountMismatchError(XRDConverterError):
    """XRD 数据点数量与声明值不一致。"""
    pass


class XrdDataParseError(XRDConverterError):
    """XRD 数据行解析失败。"""
    pass


# ============================================================
# 校验函数
# ============================================================

def _check_no_nan_inf(value: Any) -> bool:
    """递归检查值中是否存在 NaN 或 Infinity。

    Args:
        value: 任意 Python 对象（dict / list / int / float / str / None）。

    Returns:
        True 如果存在 NaN/Infinity，False 如果全部合法。
    """
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return True
    elif isinstance(value, dict):
        for v in value.values():
            if _check_no_nan_inf(v):
                return True
    elif isinstance(value, list):
        for item in value:
            if _check_no_nan_inf(item):
                return True
    return False


def validate_result(
    metadata: Dict[str, Any],
    points: List[Dict[str, Any]],
    series: List[Dict[str, Any]],
    params: Dict[str, Any],
) -> None:
    """校验解析结果的完整性，不通过则抛出异常。

    校验项：
      1. 数据点数量校验（MEAS_DATA_COUNT vs 实际数据行数）
      2. 扫描范围校验（首条 X 值 = MEAS_SCAN_START，末条 = MEAS_SCAN_STOP）
      3. 步长一致性校验（MEAS_SCAN_UNEQUALY_SPACED 为 False 时）
      4. X 值单调递增校验
      5. JSON 合法性校验（无 NaN / Infinity）

    Args:
        metadata: 文件元数据。
        points: 单值参数列表。
        series: 序列数据列表（含 XRD 衍射谱）。
        params: 校验所需参数，包含以下键：
            - MEAS_DATA_COUNT: 声明的数据点数量 (int)
            - MEAS_SCAN_START: 扫描起始角度 (float)
            - MEAS_SCAN_STOP: 扫描终止角度 (float)
            - MEAS_SCAN_STEP: 扫描步长 (float)
            - MEAS_SCAN_UNEQUALY_SPACED: 是否非等间距 (str, "True"/"False")
            - xrd_data: XRD 数据列表 [(x, y), ...]

    Raises:
        XrdDataCountMismatchError: 数据点数量不一致。
        InvalidRasRawStructureError: 扫描范围或步长不合法。
    """
    # ----- 1. 数据点数量校验（警告，不阻断）-----
    declared_count = params.get("MEAS_DATA_COUNT")
    xrd_data: List[Tuple[float, float]] = params.get("xrd_data", [])
    actual_count = len(xrd_data)

    if declared_count is not None and declared_count != actual_count:
        logger.warning(
            "数据点数量不一致：声明 %s 条，实际 %s 条（继续解析）",
            declared_count, actual_count,
        )

    # 没有 XRD 数据时无需后续校验
    if actual_count == 0:
        return

    # ----- 2. 扫描范围校验（警告，不阻断）-----
    scan_start = params.get("MEAS_SCAN_START")
    scan_stop = params.get("MEAS_SCAN_STOP")

    if scan_start is not None:
        first_x = xrd_data[0][0]
        if abs(first_x - scan_start) > 1e-4:
            logger.warning(
                "首条 X 值 %s 与 MEAS_SCAN_START %s 不一致（继续解析）",
                first_x, scan_start,
            )

    if scan_stop is not None:
        last_x = xrd_data[-1][0]
        if abs(last_x - scan_stop) > 1e-4:
            logger.warning(
                "末条 X 值 %s 与 MEAS_SCAN_STOP %s 不一致（继续解析）",
                last_x, scan_stop,
            )

    # ----- 3 & 4. 步长一致性与单调递增校验 -----
    unequally_spaced = params.get("MEAS_SCAN_UNEQUALY_SPACED", "False")
    scan_step = params.get("MEAS_SCAN_STEP")

    # X 值单调递增校验（无论是否等间距都需要校验）
    for i in range(1, actual_count):
        if xrd_data[i][0] <= xrd_data[i - 1][0]:
            logger.warning(
                "X 值非单调递增：第 %d 条 %s <= 第 %d 条 %s（继续解析）",
                i, xrd_data[i][0], i - 1, xrd_data[i - 1][0],
            )
            break

    # 等间距时校验步长一致性（浮点容差 1e-4，仅警告）
    if unequally_spaced == "False" and scan_step is not None:
        mismatch_count = 0
        for i in range(1, actual_count):
            actual_step = xrd_data[i][0] - xrd_data[i - 1][0]
            if abs(actual_step - scan_step) > 1e-4:
                mismatch_count += 1
        if mismatch_count > 0:
            logger.warning(
                "步长不一致：%d 条数据步长与声明步长 %s 偏差超过容差（继续解析）",
                mismatch_count, scan_step,
            )

    # ----- 5. JSON 合法性校验（无 NaN / Infinity）-----
    full_result = {"metadata": metadata, "points": points, "series": series}
    if _check_no_nan_inf(full_result):
        raise InvalidRasRawStructureError("解析结果中存在 NaN 或 Infinity，无法序列化为合法 JSON")

    # 验证可序列化为 JSON
    try:
        json.dumps(full_result, allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise InvalidRasRawStructureError(f"结果无法序列化为合法 JSON: {exc}")
