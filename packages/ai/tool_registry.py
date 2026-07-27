"""AI 工具注册表（已合并到 packages/ai/tools.py）。

此文件保留向后兼容：从 ``packages.ai.tools`` 重新导出
``ToolDefinition``、``ToolInvocation`` 和 ``ToolRegistry``。

原有 ``tool_registry.py`` 的安全功能（白名单验证、候选工具确认、
参数脱敏、权限隔离）已合并到 ``tools.py`` 的 ``ToolRegistry`` 类中。

请新代码直接从 ``packages.ai.tools`` 导入。
"""

# 重新导出合并后的类（向后兼容）
from packages.ai.tools import (
    ToolDefinition,
    ToolInvocation,
    ToolRegistry,
)

__all__ = [
    "ToolDefinition",
    "ToolInvocation",
    "ToolRegistry",
]
