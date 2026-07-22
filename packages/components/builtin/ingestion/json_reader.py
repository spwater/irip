"""JSON 读取组件。

读取 JSON 文件（对象或数组），展平为 ObservationTable。

参数：
- path: JSON 文件路径（必填）。
- json_path: JSONPath 式点号路径，定位数组节点（可选，如 "data.records"）。
- record_key: 若顶层为对象且需提取某键作为记录数组（可选）。
"""

import json
from pathlib import Path
from typing import Any

from packages.components.builtin.types import ObservationTable
from packages.components.sdk import ComponentContext, ComponentResult


class JSONReader:
    """JSON 文件读取组件，将 JSON 对象/数组展平为 ObservationTable。"""

    async def execute(
        self,
        context: ComponentContext,
        params: dict[str, Any],
    ) -> ComponentResult:
        """读取 JSON 文件并输出 ObservationTable。"""
        path_str: str = params["path"]
        json_path: str | None = params.get("json_path")

        with open(Path(path_str), encoding="utf-8") as f:
            data: Any = json.load(f)

        # 沿 json_path 点号路径定位
        if json_path:
            for key in json_path.split("."):
                if isinstance(data, dict):
                    data = data.get(key)
                elif isinstance(data, list) and key.isdigit():
                    data = data[int(key)]
                else:
                    break

        records: list[dict[str, Any]]
        if isinstance(data, list):
            records = [
                r if isinstance(r, dict) else {"value": r}
                for r in data
            ]
        elif isinstance(data, dict):
            # 单条对象 → 单行
            records = [data]
        else:
            records = [{"value": data}]

        if not records:
            return ComponentResult(
                outputs={},
                summary="空 JSON 数据",
                metadata={"row_count": 0},
            )

        # 收集所有列名（保持首次出现顺序）
        col_set: list[str] = []
        for rec in records:
            for k in rec:
                if k not in col_set:
                    col_set.append(k)
        columns: tuple[str, ...] = tuple(col_set)

        source_locs: list[dict[str, Any]] = [
            {"file": Path(path_str).name, "index": i}
            for i in range(len(records))
        ]

        table = ObservationTable(
            columns=columns,
            rows=tuple(records),
            source_locations=tuple(source_locs),
        )
        return ComponentResult(
            outputs={"observations": table},
            summary=f"从 {Path(path_str).name} 读取 {table.row_count()} 行",
            metadata={
                "row_count": table.row_count(),
                "column_count": table.column_count(),
            },
        )
