"""功能开关常量定义。

集中管理平台级功能模块的启停开关，通过环境变量在进程启动时读取一次。
环境变量变更需重启后端进程；前端通过 /me API 获取最新状态（登录时刷新）。

安全约定（P0 数据隔离）：
- 高风险入口开关（RESEARCH_ANALYSIS_ENABLED、LEGACY_MODEL_EXECUTION_ENABLED）
  默认关闭（false），实现 fail-closed 安全默认。
- require_feature_enabled() 在路由端点入口处守卫高风险操作，
  关闭时抛出 AppError(code=feature_disabled) → HTTP 503。
"""

import os

from packages.common.errors import AppError

#: 研究模块功能开关。默认开启（true）。
#:
#: 控制点：
#: - 后端 API 路由注册（apps/api/main.py）
#: - Composition provider 注册（apps/api/composition/__init__.py）
#: - /me 响应 feature_flags.research_module 字段
#: - 前端 LabOpsPage Tab 条件渲染
RESEARCH_MODULE_ENABLED: bool = os.getenv("RESEARCH_MODULE_ENABLED", "true").lower() == "true"

#: 研究分析高风险入口开关。默认关闭（false）。
#:
#: 控制 POST /workspaces/{id}/turns/{turn_id}/analyze 端点，
#: 关闭时该端点返回 503 feature_disabled，阻止高风险分析流程执行。
#: 不影响只读历史研究页面（timeline、turn detail 等仍可访问）。
RESEARCH_ANALYSIS_ENABLED: bool = os.getenv("RESEARCH_ANALYSIS_ENABLED", "false").lower() == "true"

#: 遗留模型执行高风险入口开关。默认关闭（false）。
#:
#: 控制 POST /api/v1/models/{model_id}/predict 端点，
#: 关闭时该端点返回 503 feature_disabled，阻止不可信模型代码在主进程中执行。
#: 不影响模型列表/详情/版本等只读操作。
LEGACY_MODEL_EXECUTION_ENABLED: bool = (
    os.getenv("LEGACY_MODEL_EXECUTION_ENABLED", "false").lower() == "true"
)


def require_feature_enabled(enabled: bool, feature: str) -> None:
    """功能开关守卫：当开关关闭时抛出 AppError(feature_disabled)。

    在高风险路由端点入口处调用，实现 fail-closed 安全默认。
    开关关闭时返回 HTTP 503（可重试），开关开启时静默放行。

    Args:
        enabled: 功能开关当前状态（True=开启，False=关闭）。
        feature: 功能名称（用于错误消息定位，如 "research_analysis"）。

    Raises:
        AppError: code="feature_disabled"，当 enabled 为 False 时。
    """
    if not enabled:
        raise AppError(
            code="feature_disabled",
            message=f"{feature} is temporarily disabled",
            retryable=True,
            fields={},
        )
