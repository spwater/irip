"""研究域包初始化。

研究域（Research Domain）负责科研分析工作空间的创建、证据引用管理、
证据快照冻结等功能。通过 CoreFactProvider 只读适配接口访问核心事实数据，
不直接导入 packages/facts 的内部模块。
"""
