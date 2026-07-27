"""structlog JSON 日志配置 + correlation ID 中间件（F-19）。

提供：
  - ``configure_logging()``：配置 structlog 输出 JSON 格式日志；
  - ``CorrelationIdMiddleware``：FastAPI 中间件，从请求头提取或生成
    ``X-Request-ID``，注入到 structlog context 和响应头；
  - ``get_correlation_id()``：获取当前请求的 correlation ID。

使用约定：
  - API 进程在 lifespan 或 create_app 中调用 ``configure_logging()``；
  - Worker 进程在入口模块调用 ``configure_logging(service_name="worker")``；
  - 所有日志调用使用 ``structlog.get_logger()`` 获取绑定 logger。
"""

import sys
import uuid
from typing import Any

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

#: 请求头中 correlation ID 的键名。
CORRELATION_ID_HEADER: str = "X-Request-ID"

#: 上下文变量键名（用于 structlog contextvar 绑定）。
CORRELATION_ID_CONTEXT_KEY: str = "correlation_id"


def configure_logging(
    service_name: str = "api",
    log_level: str = "INFO",
    json_output: bool = True,
) -> None:
    """配置 structlog 全局日志输出。

    生产环境使用 JSON 格式输出，便于日志聚合系统（ELK/Loki）解析。
    开发环境可通过 ``json_output=False`` 切换为人类可读的 Console 格式。

    Args:
        service_name: 服务名标识（如 "api" / "worker"），注入到每条日志。
        log_level: 日志级别字符串（"DEBUG" / "INFO" / "WARNING" / "ERROR"）。
        json_output: True 输出 JSON 格式，False 输出 Console 格式。
    """
    # 共享处理器链
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    if json_output:
        # 生产环境：JSON 格式
        renderer: Any = structlog.processors.JSONRenderer()
    else:
        # 开发环境：彩色 Console 格式
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())

    # 将日志级别字符串转换为整数（与 logging 模块一致）
    import logging

    level_map: dict[str, int] = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }
    level_int: int = level_map.get(log_level.upper(), logging.INFO)

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level_int),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )

    # 注入服务名到全局上下文
    structlog.contextvars.bind_contextvars(service=service_name)


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """FastAPI 中间件：为每个请求注入 correlation ID。

    行为：
      1. 从请求头 ``X-Request-ID`` 提取 correlation ID；
      2. 若请求头中不存在，则生成新的 UUID；
      3. 将 correlation ID 注入 structlog contextvar；
      4. 在响应头 ``X-Request-ID`` 中回传 correlation ID；
      5. 请求结束后清除 contextvar，避免跨请求泄漏。
    """

    def __init__(self, app: ASGIApp, header_name: str = CORRELATION_ID_HEADER) -> None:
        """初始化中间件。

        Args:
            app: ASGI 应用实例。
            header_name: correlation ID 请求头名称。
        """
        super().__init__(app)
        self._header_name: str = header_name

    async def dispatch(
        self,
        request: Request,
        call_next: Any,
    ) -> Response:
        """为请求注入 correlation ID 并在响应中回传。

        Args:
            request: 当前 HTTP 请求。
            call_next: 下一个中间件/路由处理器。

        Returns:
            Response: 带有 X-Request-ID 响应头的 HTTP 响应。
        """
        # 从请求头提取或生成 correlation ID
        correlation_id: str = request.headers.get(self._header_name, "")
        if not correlation_id:
            correlation_id = str(uuid.uuid4())

        # 注入 structlog contextvar
        structlog.contextvars.bind_contextvars(
            **{CORRELATION_ID_CONTEXT_KEY: correlation_id}
        )

        try:
            response: Response = await call_next(request)
        finally:
            # 清除 contextvar，避免跨请求泄漏
            structlog.contextvars.clear_contextvars()

        # 在响应头中回传 correlation ID
        response.headers[self._header_name] = correlation_id
        return response


def get_correlation_id() -> str | None:
    """获取当前请求的 correlation ID（从 structlog contextvar）。

    Returns:
        str | None: 当前 correlation ID，无请求上下文时返回 None。
    """
    ctx: dict[str, Any] = structlog.contextvars.get_contextvars()
    return ctx.get(CORRELATION_ID_CONTEXT_KEY)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """获取 structlog 绑定 logger。

    Args:
        name: logger 名称（通常为模块名）。

    Returns:
        BoundLogger: 已配置的 structlog logger 实例。
    """
    return structlog.get_logger(name)
