"""Prometheus 指标定义（F-19）。

定义全平台核心可观测性指标：
  - API 请求计数 / 延迟
  - 队列深度 / Job 状态分布
  - Worker heartbeat 时间戳
  - Outbox 积压量
  - 数据库 / MinIO 连接状态

使用约定：
  - API 进程通过 ``metrics_middleware`` 中间件自动记录请求指标；
  - Worker 进程通过 ``record_worker_heartbeat()`` 记录心跳；
  - ``/api/v1/metrics`` 端点通过 ``generate_metrics()`` 暴露 Prometheus 格式文本。
"""

import logging
import time
from typing import Any

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    Info,
    generate_latest,
)

#: 全局指标注册表（使用自定义 registry 避免全局状态冲突）。
REGISTRY: CollectorRegistry = CollectorRegistry()

#: 应用信息指标（版本、环境等）。
APP_INFO: Info = Info(
    "irip_app",
    "IRIP application metadata",
    registry=REGISTRY,
)

#: API 请求计数器（按 method / path / status 分维度）。
API_REQUEST_COUNT: Counter = Counter(
    "irip_api_requests_total",
    "Total API requests by method, path and status",
    labelnames=["method", "path", "status"],
    registry=REGISTRY,
)

#: API 请求延迟直方图（秒）。
API_REQUEST_DURATION: Histogram = Histogram(
    "irip_api_request_duration_seconds",
    "API request latency in seconds",
    labelnames=["method", "path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    registry=REGISTRY,
)

#: 队列深度指标（待处理 Job 数量）。
QUEUE_DEPTH: Gauge = Gauge(
    "irip_queue_depth",
    "Number of pending jobs in the queue",
    labelnames=["queue"],
    registry=REGISTRY,
)

#: Job 状态分布指标。
JOB_STATUS_COUNT: Gauge = Gauge(
    "irip_job_status_count",
    "Number of jobs by status",
    labelnames=["status"],
    registry=REGISTRY,
)

#: Worker heartbeat 时间戳（Unix epoch 秒）。
WORKER_HEARTBEAT_TIMESTAMP: Gauge = Gauge(
    "irip_worker_heartbeat_timestamp_seconds",
    "Unix timestamp of the last worker heartbeat",
    labelnames=["worker_id"],
    registry=REGISTRY,
)

#: Outbox 积压量（未投递事件数）。
OUTBOX_BACKLOG: Gauge = Gauge(
    "irip_outbox_backlog",
    "Number of undelivered outbox events",
    registry=REGISTRY,
)

#: 数据库连接状态（1=正常, 0=异常）。
DB_CONNECTION_STATUS: Gauge = Gauge(
    "irip_db_connection_status",
    "Database connection health (1=ok, 0=fail)",
    registry=REGISTRY,
)

#: MinIO / S3 连接状态（1=正常, 0=异常）。
MINIO_CONNECTION_STATUS: Gauge = Gauge(
    "irip_minio_connection_status",
    "MinIO/S3 connection health (1=ok, 0=fail)",
    registry=REGISTRY,
)

#: Redis 连接状态（1=正常, 0=异常）。
REDIS_CONNECTION_STATUS: Gauge = Gauge(
    "irip_redis_connection_status",
    "Redis connection health (1=ok, 0=fail)",
    registry=REGISTRY,
)

#: 组件执行计数（按 kind / status 分维度）。
COMPONENT_EXECUTION_COUNT: Counter = Counter(
    "irip_component_executions_total",
    "Total component executions by kind and result",
    labelnames=["kind", "result"],
    registry=REGISTRY,
)

#: 组件执行延迟直方图（秒）。
COMPONENT_EXECUTION_DURATION: Histogram = Histogram(
    "irip_component_execution_duration_seconds",
    "Component execution latency in seconds",
    labelnames=["kind"],
    buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0),
    registry=REGISTRY,
)


def set_app_info(version: str = "0.8.0", environment: str = "development") -> None:
    """设置应用元信息指标。

    Args:
        version: 应用版本号。
        environment: 运行环境标识。
    """
    APP_INFO.info({"version": version, "environment": environment})


def record_api_request(
    method: str,
    path: str,
    status: str,
    duration_seconds: float,
) -> None:
    """记录一次 API 请求的指标。

    Args:
        method: HTTP 方法（GET / POST / PUT / DELETE 等）。
        path: 请求路径（已去路径参数化）。
        status: HTTP 状态码字符串。
        duration_seconds: 请求耗时（秒）。
    """
    API_REQUEST_COUNT.labels(method=method, path=path, status=status).inc()
    API_REQUEST_DURATION.labels(method=method, path=path).observe(duration_seconds)


def record_worker_heartbeat(worker_id: str = "default") -> None:
    """记录 Worker 心跳时间戳。

    由 Celery Beat 调度的 ``worker.heartbeat`` 任务调用。

    Args:
        worker_id: Worker 标识（默认 "default"）。
    """
    WORKER_HEARTBEAT_TIMESTAMP.labels(worker_id=worker_id).set(time.time())


def record_component_execution(
    kind: str,
    result: str,
    duration_seconds: float,
) -> None:
    """记录一次组件执行的指标。

    Args:
        kind: 组件类型标识。
        result: 执行结果（"success" / "failure" / "timeout"）。
        duration_seconds: 执行耗时（秒）。
    """
    COMPONENT_EXECUTION_COUNT.labels(kind=kind, result=result).inc()
    COMPONENT_EXECUTION_DURATION.labels(kind=kind).observe(duration_seconds)


def generate_metrics() -> bytes:
    """生成 Prometheus 格式的指标文本。

    用于 ``/api/v1/metrics`` 端点响应体。

    Returns:
        bytes: Prometheus exposition 格式文本。
    """
    return bytes(generate_latest(REGISTRY))


async def metrics_middleware(request: Any, call_next: Any) -> Any:
    """FastAPI 中间件：自动记录 API 请求计数和延迟。

    用法（在 create_app 中注册）::

        app.add_middleware(BaseHTTPMiddleware, dispatch=metrics_middleware)

    Args:
        request: Starlette/FastAPI Request 对象。
        call_next: 下一个中间件/路由处理器。

    Returns:
        Response: 原始响应对象（指标记录不影响响应内容）。
    """
    start_time: float = time.time()
    response: Any = await call_next(request)
    duration: float = time.time() - start_time

    # 去路径参数化：将 /api/v1/jobs/123 → /api/v1/jobs/{id}
    # 使用 route path 如果可用，否则使用 raw path
    path: str = request.url.path
    try:
        route = request.scope.get("route")
        if route is not None and hasattr(route, "path"):
            path = route.path
    except Exception:
        logging.getLogger(__name__).debug("cleanup failed", exc_info=True)

    record_api_request(
        method=request.method,
        path=path,
        status=str(response.status_code),
        duration_seconds=duration,
    )
    return response
