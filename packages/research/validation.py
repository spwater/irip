"""三段式数据校验器：校验 + field_manifest 自动推断 + content_hash 计算。

ThreeSegmentValidator 负责校验 DerivedDatasetVersion 的三段式数据结构：
- metadata: dict，报告级描述
- points: list of {name, value, unit}，独立单值指标
- series: list of {name, columns, rows}，普通表格/时间序列/曲线/多批次

空 series 或空 points 允许。校验失败返回字段级错误信息。

类型推断使用简单 Python 类型（int/float/str/bool/null）（Q5）。

参照 PRD 6.8 节 / 设计文档 8.3 节。
"""

import hashlib
import json
from typing import Any

from packages.research.models import FieldManifestEntry, ThreeSegmentData, ValidationResult


class ThreeSegmentValidator:
    """三段式数据校验 + field_manifest 自动推断 + content_hash 计算。

    所有方法均为静态方法，无实例状态。
    """

    @staticmethod
    def validate(data: dict | bytes) -> ValidationResult:
        """校验三段式数据结构，返回校验结果 + field_manifest。

        Args:
            data: 原始数据（dict 或 bytes JSON）。

        Returns:
            ValidationResult: 校验结果（含解析后的三段式数据 + field_manifest）。
        """
        # 如果传入 bytes，尝试解析为 JSON
        if isinstance(data, (bytes, bytearray)):
            try:
                data = json.loads(data.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                return ValidationResult(
                    valid=False,
                    errors=[f"无法解析 JSON: {str(exc)}"],
                )
            except Exception as exc:
                return ValidationResult(
                    valid=False,
                    errors=[f"数据解析异常: {str(exc)}"],
                )

        if not isinstance(data, dict):
            return ValidationResult(
                valid=False,
                errors=["数据根节点必须为 dict（对象）"],
            )

        errors: list[str] = []
        metadata = data.get("metadata", {})
        points = data.get("points", [])
        series = data.get("series", [])

        # 校验 metadata
        if not isinstance(metadata, dict):
            errors.append("metadata 必须为 dict（对象）")
            metadata = {}

        # 校验 points
        if not isinstance(points, list):
            errors.append("points 必须为 list（数组）")
            points = []
        else:
            for i, pt in enumerate(points):
                if not isinstance(pt, dict):
                    errors.append(f"points[{i}] 必须为 dict（对象）")
                    continue
                if "name" not in pt or "value" not in pt:
                    errors.append(f"points[{i}] 缺少必填字段 name 或 value")

        # 校验 series
        if not isinstance(series, list):
            errors.append("series 必须为 list（数组）")
            series = []
        else:
            for i, sr in enumerate(series):
                if not isinstance(sr, dict):
                    errors.append(f"series[{i}] 必须为 dict（对象）")
                    continue
                if "name" not in sr:
                    errors.append(f"series[{i}] 缺少必填字段 name")
                if "rows" not in sr:
                    errors.append(f"series[{i}] 缺少必填字段 rows")
                # columns 可选（有 rows 时可推断列名）

        if errors:
            return ValidationResult(valid=False, errors=errors)

        three_segment = ThreeSegmentData(
            metadata=metadata,
            points=points,
            series=series,
        )

        field_manifest = ThreeSegmentValidator.infer_field_manifest(points, series)

        return ValidationResult(
            valid=True,
            errors=[],
            data=three_segment,
            field_manifest=field_manifest,
        )

    @staticmethod
    def infer_field_manifest(points: list, series: list) -> list[dict]:
        """自动推断 field_manifest。

        返回 [{field_name, inferred_type, unit, description, source_step, column_order, shape}]
        类型推断使用 int/float/str/bool/null（Q5 简单类型推断）。

        Args:
            points: 独立单值指标列表。
            series: 普通表格/时间序列列表。

        Returns:
            list[dict]: 字段清单条目列表。
        """
        entries: list[dict] = []
        column_order = 0

        # 从 points 推断字段（name + value + unit）
        for pt in points:
            if not isinstance(pt, dict):
                continue
            name = pt.get("name", "")
            if not name:
                continue
            value = pt.get("value")
            unit = pt.get("unit", "")
            entries.append(
                FieldManifestEntry(
                    field_name=str(name),
                    inferred_type=ThreeSegmentValidator._infer_type(value),
                    unit=str(unit) if unit else "",
                    description="",
                    source_step="",
                    column_order=column_order,
                    shape="",
                ).__dict__
            )
            column_order += 1

        # 从 series 推断字段（columns + rows 推断类型）
        for sr in series:
            if not isinstance(sr, dict):
                continue
            sr_name = sr.get("name", "")
            columns = sr.get("columns", [])
            rows = sr.get("rows", [])
            row_count = len(rows)

            # 如果 columns 未定义，从 rows 推断
            if not columns and rows and isinstance(rows[0], dict):
                columns = list(rows[0].keys())
            elif not columns and rows and isinstance(rows[0], list):
                columns = [f"col_{i}" for i in range(len(rows[0]))]

            col_count = len(columns)
            shape = f"{row_count}x{col_count}"

            for col_idx, col_name in enumerate(columns):
                # 从 rows 中采样推断类型
                sample_value: Any = None
                for row in rows:
                    if isinstance(row, dict) and col_name in row:
                        sample_value = row[col_name]
                        break
                    elif isinstance(row, list) and col_idx < len(row):
                        sample_value = row[col_idx]
                        break

                entries.append(
                    FieldManifestEntry(
                        field_name=str(col_name),
                        inferred_type=ThreeSegmentValidator._infer_type(sample_value),
                        unit="",
                        description="",
                        source_step=str(sr_name),
                        column_order=column_order,
                        shape=shape,
                    ).__dict__
                )
                column_order += 1

        return entries

    @staticmethod
    def compute_content_hash(metadata: dict, points: list, series: list) -> str:
        """计算三段式数据 SHA-256。

        序列化 JSON (sort_keys=True, ensure_ascii=False, separators=(",",":"))
        → hashlib.sha256 → 64 字符十六进制。

        Args:
            metadata: 报告级描述。
            points: 独立单值指标列表。
            series: 普通表格/时间序列列表。

        Returns:
            str: 64 字符十六进制 SHA-256 哈希。
        """
        payload = {
            "metadata": metadata,
            "points": points,
            "series": series,
        }
        json_bytes = json.dumps(
            payload,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(json_bytes).hexdigest()

    @staticmethod
    def _infer_type(value: Any) -> str:
        """推断值的 Python 类型。

        简单类型推断：int / float / str / bool / null（Q5）。

        Args:
            value: 待推断的值。

        Returns:
            str: 类型字符串。
        """
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "bool"
        if isinstance(value, int):
            return "int"
        if isinstance(value, float):
            return "float"
        if isinstance(value, str):
            return "str"
        # 尝试转换字符串为数值
        if isinstance(value, str):
            try:
                int(value)
                return "int"
            except ValueError:
                pass
            try:
                float(value)
                return "float"
            except ValueError:
                pass
        return "str"
