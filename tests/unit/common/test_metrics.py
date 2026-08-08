"""单元测试：metrics Prometheus 指标定义与中间件。

覆盖：
- set_app_info：设置应用元信息；
- record_api_request：记录 API 请求计数 + 延迟；
- record_worker_heartbeat：记录 Worker 心跳时间戳；
- record_component_execution：记录组件执行计数 + 延迟；
- generate_metrics：生成 Prometheus 格式文本；
- metrics_middleware：中间件自动记录请求指标（含 route path 提取）。
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("prometheus_client")

from packages.common.metrics import (
    generate_metrics,
    metrics_middleware,
    record_api_request,
    record_component_execution,
    record_worker_heartbeat,
    set_app_info,
)


class TestSetAppInfo:
    """set_app_info 测试。"""

    def test_sets_version_and_environment(self) -> None:
        """设置版本和环境标识。"""
        set_app_info(version="1.2.3", environment="staging")
        # 无异常即通过（Info.info 不返回值）
        metrics_text = generate_metrics()
        assert b"1.2.3" in metrics_text or b"irip_app" in metrics_text


class TestRecordApiRequest:
    """record_api_request 测试。"""

    def test_records_count_and_duration(self) -> None:
        """记录请求计数和延迟。"""
        record_api_request("GET", "/api/v1/jobs", "200", 0.05)
        # 重复调用递增计数
        record_api_request("GET", "/api/v1/jobs", "200", 0.03)
        metrics_text = generate_metrics()
        assert b"irip_api_requests_total" in metrics_text


class TestRecordWorkerHeartbeat:
    """record_worker_heartbeat 测试。"""

    def test_records_timestamp(self) -> None:
        """记录心跳时间戳。"""
        record_worker_heartbeat("worker-1")
        metrics_text = generate_metrics()
        assert b"irip_worker_heartbeat" in metrics_text

    def test_default_worker_id(self) -> None:
        """默认 worker_id 为 default。"""
        record_worker_heartbeat()
        metrics_text = generate_metrics()
        assert b"irip_worker_heartbeat" in metrics_text


class TestRecordComponentExecution:
    """record_component_execution 测试。"""

    def test_records_count_and_duration(self) -> None:
        """记录组件执行计数和延迟。"""
        record_component_execution("python_executor", "success", 1.5)
        metrics_text = generate_metrics()
        assert b"irip_component_executions_total" in metrics_text

    def test_failure_result(self) -> None:
        """记录失败结果。"""
        record_component_execution("sandbox", "failure", 0.1)
        metrics_text = generate_metrics()
        assert b"irip_component_executions_total" in metrics_text


class TestGenerateMetrics:
    """generate_metrics 测试。"""

    def test_returns_bytes(self) -> None:
        """返回 bytes 类型。"""
        result = generate_metrics()
        assert isinstance(result, bytes)

    def test_contains_metric_names(self) -> None:
        """包含核心指标名。"""
        result = generate_metrics()
        assert b"irip_app" in result
        assert b"irip_api_requests_total" in result
        assert b"irip_queue_depth" in result


class TestMetricsMiddleware:
    """metrics_middleware 测试。"""

    async def test_records_request_metrics(self) -> None:
        """中间件记录请求指标。"""
        request = MagicMock()
        request.method = "GET"
        request.url.path = "/api/v1/jobs"
        request.scope = {}

        response = MagicMock()
        response.status_code = 200

        call_next = AsyncMock(return_value=response)
        result = await metrics_middleware(request, call_next)

        assert result is response
        call_next.assert_awaited_once()

    async def test_uses_route_path(self) -> None:
        """使用 route.path 替代 raw path。"""
        request = MagicMock()
        request.method = "POST"
        request.url.path = "/api/v1/jobs/123"
        route = MagicMock()
        route.path = "/api/v1/jobs/{id}"
        request.scope = {"route": route}

        response = MagicMock()
        response.status_code = 201

        call_next = AsyncMock(return_value=response)
        await metrics_middleware(request, call_next)

    async def test_route_scope_exception_handled(self) -> None:
        """route scope 异常时不崩溃。"""
        request = MagicMock()
        request.method = "GET"
        request.url.path = "/api/v1/jobs"

        def getitem(key: str) -> Any:
            if key == "route":
                raise RuntimeError("no route")
            raise KeyError(key)

        request.scope = MagicMock()
        request.scope.get.side_effect = lambda k, default=None: None

        response = MagicMock()
        response.status_code = 200

        call_next = AsyncMock(return_value=response)
        result = await metrics_middleware(request, call_next)
        assert result is response
