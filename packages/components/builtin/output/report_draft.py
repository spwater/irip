"""Markdown 报告草稿组件。

根据各组件的统计结果与诊断信息生成 Markdown 格式的报告草稿。

参数：
- title: 报告标题（必填）。
- sections: 报告章节列表，每项含 heading 和 content
  （content 为字符串或结构化数据）（必填）。
- metadata: 报告元数据字典（可选）。
"""

from typing import Any

from packages.components.builtin.types import DiagnosticReport
from packages.components.sdk import ComponentContext, ComponentResult


class ReportDraft:
    """Markdown 报告草稿生成组件。"""

    async def execute(
        self,
        context: ComponentContext,
        params: dict[str, Any],
    ) -> ComponentResult:
        """生成 Markdown 报告草稿。"""
        title: str = params["title"]
        sections: list[dict[str, Any]] = params["sections"]
        meta: dict[str, Any] = params.get("metadata", {})

        lines: list[str] = []
        lines.append(f"# {title}")
        lines.append("")

        # 元数据
        if meta:
            lines.append("## 元信息")
            lines.append("")
            for key, val in meta.items():
                lines.append(f"- **{key}**: {val}")
            lines.append("")

        # 各章节
        for section in sections:
            heading: str = section.get("heading", "未命名章节")
            level: int = int(section.get("level", 2))
            content = section.get("content", "")

            lines.append(f"{'#' * level} {heading}")
            lines.append("")

            if isinstance(content, str):
                lines.append(content)
            elif isinstance(content, dict):
                # 结构化数据 → 表格
                lines.append(self._dict_to_markdown(content))
            elif isinstance(content, list):
                lines.append(self._list_to_markdown(content))
            else:
                lines.append(str(content))
            lines.append("")

        report_text = "\n".join(lines)

        report = DiagnosticReport(
            component="report_draft",
            input_rows=0,
            output_rows=len(sections),
            warnings=(),
            row_annotations=(),
        )
        return ComponentResult(
            outputs={
                "report": report_text,
                "diagnostics": report,
            },
            summary=f"报告草稿: {len(sections)} 个章节",
            metadata={
                "title": title,
                "section_count": len(sections),
                "char_count": len(report_text),
            },
        )

    def _dict_to_markdown(self, data: dict[str, Any]) -> str:
        """将字典转换为 Markdown 表格。"""
        lines = ["| 字段 | 值 |", "|------|-----|"]
        for key, val in data.items():
            lines.append(f"| {key} | {val} |")
        return "\n".join(lines)

    def _list_to_markdown(self, data: list[Any]) -> str:
        """将列表转换为 Markdown 项目符号列表。"""
        lines: list[str] = []
        for item in data:
            if isinstance(item, dict):
                for k, v in item.items():
                    lines.append(f"- **{k}**: {v}")
            else:
                lines.append(f"- {item}")
        return "\n".join(lines)
