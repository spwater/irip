"""功能开关常量定义。

集中管理平台级功能模块的启停开关，通过环境变量在进程启动时读取一次。
环境变量变更需重启后端进程；前端通过 /me API 获取最新状态（登录时刷新）。
"""

import os

#: 研究模块功能开关。默认开启（true）。
#:
#: 控制点：
#: - 后端 API 路由注册（apps/api/main.py）
#: - Composition provider 注册（apps/api/composition/__init__.py）
#: - /me 响应 feature_flags.research_module 字段
#: - 前端 LabOpsPage Tab 条件渲染
RESEARCH_MODULE_ENABLED: bool = os.getenv("RESEARCH_MODULE_ENABLED", "true").lower() == "true"
