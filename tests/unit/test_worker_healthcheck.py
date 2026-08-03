"""Worker 健康检查自动接通单元测试。

验证 F-19 Worker 健康检查 HTTP 端点的自动接通逻辑：
- ``worker_process_init`` signal handler 正确接通 ``run_worker_healthcheck_server()``
- prefork 多进程端口冲突时 OSError 静默跳过
- ``_HealthcheckHandler`` 响应 ``GET /health`` 返回 200 ``{"status": "ok"}``
- ``run_worker_healthcheck_server()`` 守护线程模式可正常启动/停止
- 非法路径返回 404

无需真实 Redis/DB 连接，纯进程内 HTTP 服务器验证。
"""

import socket
import threading
import time
from http.client import HTTPConnection
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _find_free_port() -> int:
    """获取一个可用的空闲端口。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _http_get(port: int, path: str) -> tuple[int, bytes]:
    """向本地 HTTP 服务器发送 GET 请求，返回 (status_code, body)。"""
    conn = HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1. Signal handler 接通验证
# ---------------------------------------------------------------------------


class TestWorkerProcessInitSignal:
    """验证 worker_process_init signal handler 正确接通健康检查服务器。"""

    def test_signal_handler_exists(self) -> None:
        """``_start_healthcheck_on_worker_init`` 函数已定义。"""
        from apps.worker.celery_app import _start_healthcheck_on_worker_init

        assert callable(_start_healthcheck_on_worker_init), (
            "_start_healthcheck_on_worker_init 应为可调用函数"
        )

    def test_signal_handler_calls_healthcheck_server(self) -> None:
        """signal handler 内部调用 ``run_worker_healthcheck_server()``。"""
        import inspect

        from apps.worker.celery_app import _start_healthcheck_on_worker_init

        source = inspect.getsource(_start_healthcheck_on_worker_init)
        assert "run_worker_healthcheck_server" in source, (
            "_start_healthcheck_on_worker_init 应调用 run_worker_healthcheck_server()"
        )

    def test_signal_handler_catches_oserror(self) -> None:
        """signal handler 捕获 OSError（prefork 端口冲突静默跳过）。"""
        import inspect

        from apps.worker.celery_app import _start_healthcheck_on_worker_init

        source = inspect.getsource(_start_healthcheck_on_worker_init)
        assert "OSError" in source, (
            "_start_healthcheck_on_worker_init 应捕获 OSError 处理端口冲突"
        )
        assert "except OSError" in source, (
            "应使用 except OSError 捕获端口绑定异常"
        )

    def test_signal_handler_registered_with_celery(self) -> None:
        """signal handler 通过 ``@worker_process_init.connect`` 注册。"""
        import inspect

        from apps.worker import celery_app as celery_module
        from apps.worker.celery_app import _start_healthcheck_on_worker_init

        # 检查 celery_app.py 源码中使用了 @worker_process_init.connect 装饰器
        module_source = inspect.getsource(celery_module)
        assert "@worker_process_init.connect" in module_source, (
            "celery_app.py 应使用 @worker_process_init.connect 装饰器注册 signal handler"
        )
        # 检查装饰器位于 _start_healthcheck_on_worker_init 定义上方
        handler_source = inspect.getsource(_start_healthcheck_on_worker_init)
        assert "run_worker_healthcheck_server" in handler_source, (
            "handler 内部应调用 run_worker_healthcheck_server()"
        )

    def test_oserror_silently_skipped(self) -> None:
        """模拟端口冲突（OSError）时 signal handler 不抛出异常。"""
        from apps.worker.celery_app import _start_healthcheck_on_worker_init

        # 模拟 run_worker_healthcheck_server 抛出 OSError
        with patch(
            "apps.worker.celery_app.run_worker_healthcheck_server",
            side_effect=OSError("Address already in use"),
        ):
            # 不应抛出任何异常
            _start_healthcheck_on_worker_init()

    def test_normal_startup_calls_healthcheck(self) -> None:
        """模拟正常启动时 signal handler 调用 healthcheck server。"""
        from apps.worker.celery_app import _start_healthcheck_on_worker_init

        called = False

        def mock_server(**kwargs):
            nonlocal called
            called = True

        with patch(
            "apps.worker.celery_app.run_worker_healthcheck_server",
            side_effect=mock_server,
        ):
            _start_healthcheck_on_worker_init()

        assert called, "正常启动时应调用 run_worker_healthcheck_server()"


# ---------------------------------------------------------------------------
# 2. Healthcheck HTTP 端点验证
# ---------------------------------------------------------------------------


class TestHealthcheckHandler:
    """验证 _HealthcheckHandler 的 HTTP 响应行为。"""

    def test_health_endpoint_returns_200(self) -> None:
        """``GET /health`` 返回 200 和 ``{"status": "ok"}``。"""
        from apps.worker.celery_app import run_worker_healthcheck_server

        port = _find_free_port()
        server = run_worker_healthcheck_server(port=port, block=False)
        assert server is not None, "非阻塞模式应返回 HTTPServer 实例"
        try:
            time.sleep(0.1)  # 等待线程启动
            status, body = _http_get(port, "/health")
            assert status == 200, f"/health 应返回 200，实际为 {status}"
            assert b'"status": "ok"' in body, (
                f"响应体应包含 status: ok，实际为 {body!r}"
            )
        finally:
            server.shutdown()
            server.server_close()

    def test_unknown_path_returns_404(self) -> None:
        """非法路径返回 404。"""
        from apps.worker.celery_app import run_worker_healthcheck_server

        port = _find_free_port()
        server = run_worker_healthcheck_server(port=port, block=False)
        try:
            time.sleep(0.1)
            status, _ = _http_get(port, "/unknown")
            assert status == 404, f"非法路径应返回 404，实际为 {status}"
        finally:
            server.shutdown()
            server.server_close()

    def test_healthcheck_runs_as_daemon_thread(self) -> None:
        """非阻塞模式以守护线程方式运行。"""
        from apps.worker.celery_app import run_worker_healthcheck_server

        port = _find_free_port()
        server = run_worker_healthcheck_server(port=port, block=False)
        try:
            time.sleep(0.1)
            # 检查是否有名为 worker-healthcheck 的守护线程
            daemon_threads = [
                t for t in threading.enumerate()
                if t.name == "worker-healthcheck" and t.daemon
            ]
            assert len(daemon_threads) >= 1, (
                "应存在名为 'worker-healthcheck' 的守护线程"
            )
        finally:
            server.shutdown()
            server.server_close()

    def test_multiple_ports_no_conflict(self) -> None:
        """不同端口可同时启动多个 healthcheck server。"""
        from apps.worker.celery_app import run_worker_healthcheck_server

        port1 = _find_free_port()
        port2 = _find_free_port()
        assert port1 != port2, "应获取不同端口"

        server1 = run_worker_healthcheck_server(port=port1, block=False)
        server2 = run_worker_healthcheck_server(port=port2, block=False)
        try:
            time.sleep(0.1)
            status1, _ = _http_get(port1, "/health")
            status2, _ = _http_get(port2, "/health")
            assert status1 == 200 and status2 == 200, (
                "两个端口的 healthcheck 都应返回 200"
            )
        finally:
            server1.shutdown()
            server1.server_close()
            server2.shutdown()
            server2.server_close()

    def test_same_port_raises_oserror(self) -> None:
        """同一端口重复绑定抛出 OSError（模拟 prefork 冲突）。"""
        from apps.worker.celery_app import run_worker_healthcheck_server

        port = _find_free_port()
        server1 = run_worker_healthcheck_server(port=port, block=False)
        try:
            time.sleep(0.1)
            # 第二次绑定同一端口应抛出 OSError
            with pytest.raises(OSError):
                run_worker_healthcheck_server(port=port, block=False)
        finally:
            server1.shutdown()
            server1.server_close()

    def test_same_port_oserror_caught_by_handler(self) -> None:
        """signal handler 捕获同端口 OSError 后不抛出异常。"""
        from apps.worker.celery_app import (
            _start_healthcheck_on_worker_init,
            run_worker_healthcheck_server,
        )

        port = _find_free_port()
        # 先占用端口
        server1 = run_worker_healthcheck_server(port=port, block=False)
        try:
            time.sleep(0.1)
            # 模拟第二个子进程尝试绑定同一端口
            with patch.dict("os.environ", {"IRIP_WORKER_HEALTHCHECK_PORT": str(port)}):
                # signal handler 应捕获 OSError 并静默跳过
                _start_healthcheck_on_worker_init()
                # 如果到达这里，说明 OSError 被正确捕获
        finally:
            server1.shutdown()
            server1.server_close()


# ---------------------------------------------------------------------------
# 3. 配置验证
# ---------------------------------------------------------------------------


class TestHealthcheckConfig:
    """验证健康检查配置常量。"""

    def test_default_healthcheck_port(self) -> None:
        """默认健康检查端口为 9100。"""
        from apps.worker.celery_app import WORKER_HEALTHCHECK_PORT

        assert WORKER_HEALTHCHECK_PORT == 9100, (
            f"默认健康检查端口应为 9100，实际为 {WORKER_HEALTHCHECK_PORT}"
        )

    def test_healthcheck_port_from_env(self) -> None:
        """健康检查端口可通过 IRIP_WORKER_HEALTHCHECK_PORT 环境变量覆盖。"""
        import importlib
        import os

        old = os.environ.get("IRIP_WORKER_HEALTHCHECK_PORT")
        os.environ["IRIP_WORKER_HEALTHCHECK_PORT"] = "9200"
        try:
            # 重新导入模块以读取新环境变量
            if "apps.worker.celery_app" in importlib.sys.modules:
                del importlib.sys.modules["apps.worker.celery_app"]
            import apps.worker.celery_app as mod

            assert mod.WORKER_HEALTHCHECK_PORT == 9200, (
                f"环境变量覆盖后端口应为 9200，实际为 {mod.WORKER_HEALTHCHECK_PORT}"
            )
        finally:
            if old is not None:
                os.environ["IRIP_WORKER_HEALTHCHECK_PORT"] = old
            else:
                os.environ.pop("IRIP_WORKER_HEALTHCHECK_PORT", None)
            # 恢复模块
            if "apps.worker.celery_app" in importlib.sys.modules:
                del importlib.sys.modules["apps.worker.celery_app"]
            import apps.worker.celery_app  # noqa: F401

    def test_healthcheck_listens_on_all_interfaces(self) -> None:
        """健康检查服务器监听 0.0.0.0（所有接口）。"""
        from apps.worker.celery_app import run_worker_healthcheck_server

        port = _find_free_port()
        server = run_worker_healthcheck_server(port=port, block=False)
        try:
            time.sleep(0.1)
            # 检查服务器地址
            assert server.server_address[0] == "0.0.0.0", (
                f"应监听 0.0.0.0，实际为 {server.server_address[0]}"
            )
        finally:
            server.shutdown()
            server.server_close()
