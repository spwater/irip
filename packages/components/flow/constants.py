"""流程运行时常量。

受保护参数白名单：外部运行 inputs 禁止覆盖文件路径类参数（F-13 安全约束）。
"""

from __future__ import annotations

#: 受保护参数白名单：外部运行 inputs 禁止覆盖这些文件路径类参数（F-13 安全约束）。
#: 防止通过流程 inputs 注入任意文件路径，绕过节点参数的安全校验。
PROTECTED_PARAMS: frozenset[str] = frozenset(
    {
        "path",
        "file_path",
        "input_path",
        "output_path",
        "file",
        "filename",
        "source_path",
        "dest_path",
        "input_file",
        "output_file",
        "data_path",
        "template_path",
        "config_path",
        "script_path",
        "executable_path",
    }
)
