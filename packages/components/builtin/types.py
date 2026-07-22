"""IRIP 内置组件共享数据类型。

定义组件间传递的核心不可变数据结构：
- ObservationTable: 观测数据表（列 + 行 + 来源定位）；
- DiagnosticReport: 诊断报告（行级注解 + 警告）；
- ParameterCandidate: L3 参数候选（值 + 单位 + 置信度 + 排除原因）。

设计要点（IRIP V2-T02）：
- 所有类型为 frozen dataclass，保证组件间传递的不可变性；
- rows/source_locations/row_annotations 使用 tuple 而非 list，
  确保哈希稳定与线程安全；
- ObservationTable 作为摄入组件的统一输出、变换/质量/统计组件的
  统一输入输出，降低组件间耦合。
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ObservationTable:
    """观测数据表（不可变）。

    组件间传递的核心数据载体，封装列名、行数据与来源定位信息。

    Attributes:
        columns: 列名元组（顺序固定）。
        rows: 行数据元组，每行为 dict（键为列名，值为任意类型）。
        source_locations: 每行对应的来源定位元组
            （如 {"file": "data.xlsx", "sheet": "Sheet1", "row": 5}）。
    """

    columns: tuple[str, ...] = ()
    rows: tuple[dict[str, Any], ...] = ()
    source_locations: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def row_count(self) -> int:
        """返回行数。"""
        return len(self.rows)

    def column_count(self) -> int:
        """返回列数。"""
        return len(self.columns)


@dataclass(frozen=True)
class DiagnosticReport:
    """诊断报告（不可变）。

    质量检查与统计组件的输出载体，记录输入/输出行数、警告列表
    与行级注解。

    Attributes:
        component: 产生此报告的组件名称。
        input_rows: 输入行数。
        output_rows: 输出行数。
        warnings: 警告消息元组。
        row_annotations: 行级注解元组，每项为 dict
            （如 {"row_index": 3, "status": "fail", "detail": "..."}）。
    """

    component: str = ""
    input_rows: int = 0
    output_rows: int = 0
    warnings: tuple[str, ...] = field(default_factory=tuple)
    row_annotations: tuple[dict[str, Any], ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ParameterCandidate:
    """L3 参数候选（不可变）。

    输出组件 parameter_card 的产物，描述一个参数变量的候选值。

    Attributes:
        variable_code: 变量代码（如 ``particle_d50``）。
        value: 候选值字符串表示。
        unit: 单位（可选，如 ``um``）。
        confidence: 置信度 [0.0, 1.0]。
        exclusion_reasons: 排除原因元组（为空表示未被排除）。
    """

    variable_code: str = ""
    value: str = ""
    unit: str | None = None
    confidence: float = 0.0
    exclusion_reasons: tuple[str, ...] = field(default_factory=tuple)
