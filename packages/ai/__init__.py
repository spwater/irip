"""AI 工具注册表包。

提供 AI 助手工具的安全控制层：
- ToolDefinition / ToolInvocation / ToolRegistry

安全属性（V3-T04）：
1. 未知工具名拒绝执行；
2. 候选工具需显式确认后执行（不能自动执行）；
3. 工具参数中的秘密被脱敏；
4. 用户权限范围外的操作被拒绝。
"""
