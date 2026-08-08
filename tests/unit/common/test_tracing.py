"""单元测试：tracing OpenTelemetry 分布式追踪配置。

覆盖：
- init_tracing：未配置 endpoint 跳过 / 已初始化跳过 / 配置后初始化 / ImportError 优雅降级；
- instrument_fastapi：未配置 endpoint 跳过 / ImportError 跳过 / 正常注入；
- instrument_sqlalchemy：未配置 endpoint 跳过 / ImportError 跳过 / 正常注入。

通过控制环境变量和重置模块级 _otel_initialized 标志测试。
"""

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import packages.common.tracing as tracing_mod

# ============================================================
# init_tracing
# ============================================================


class TestInitTracing:
    """init_tracing 测试。"""

    def test_no_endpoint_skips(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """未配置 IRIP_OTEL_ENDPOINT 时静默跳过。"""
        monkeypatch.delenv("IRIP_OTEL_ENDPOINT", raising=False)
        tracing_mod._otel_initialized = False
        tracing_mod.init_tracing()
        # 无异常即通过

    def test_already_initialized_skips(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """已初始化时跳过。"""
        monkeypatch.setenv("IRIP_OTEL_ENDPOINT", "http://jaeger:4317")
        tracing_mod._otel_initialized = True
        tracing_mod.init_tracing()  # 不应重新初始化

    def test_import_error_graceful(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """OpenTelemetry 包未安装时优雅降级。"""
        monkeypatch.setenv("IRIP_OTEL_ENDPOINT", "http://jaeger:4317")
        tracing_mod._otel_initialized = False

        # 模拟 ImportError
        import builtins

        real_import = builtins.__import__

        def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name.startswith("opentelemetry"):
                raise ImportError("not installed")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            tracing_mod.init_tracing()  # 不应抛出异常

    def test_init_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """配置 endpoint 且包已安装时成功初始化。"""
        import sys

        monkeypatch.setenv("IRIP_OTEL_ENDPOINT", "http://jaeger:4317")
        monkeypatch.setenv("IRIP_VERSION", "1.0.0")
        monkeypatch.setenv("IRIP_ENV", "test")
        tracing_mod._otel_initialized = False

        mock_trace = MagicMock()
        mock_provider_cls = MagicMock(return_value=MagicMock())
        mock_exporter_cls = MagicMock(return_value=MagicMock())
        mock_resource_cls = MagicMock(return_value=MagicMock())
        mock_processor_cls = MagicMock(return_value=MagicMock())

        # 注入 mock 模块到 sys.modules（opentelemetry 未安装时）
        otel_root = MagicMock()
        otel_root.trace = mock_trace  # from opentelemetry import trace
        otel_modules = {
            "opentelemetry": otel_root,
            "opentelemetry.trace": mock_trace,
            "opentelemetry.exporter": MagicMock(),
            "opentelemetry.exporter.otlp": MagicMock(),
            "opentelemetry.exporter.otlp.proto": MagicMock(),
            "opentelemetry.exporter.otlp.proto.grpc": MagicMock(),
            "opentelemetry.exporter.otlp.proto.grpc.trace_exporter": MagicMock(
                OTLPSpanExporter=mock_exporter_cls
            ),
            "opentelemetry.sdk": MagicMock(),
            "opentelemetry.sdk.resources": MagicMock(Resource=mock_resource_cls),
            "opentelemetry.sdk.trace": MagicMock(TracerProvider=mock_provider_cls),
            "opentelemetry.sdk.trace.export": MagicMock(BatchSpanProcessor=mock_processor_cls),
        }
        saved = {k: sys.modules.get(k) for k in otel_modules}
        sys.modules.update(otel_modules)
        try:
            tracing_mod.init_tracing()
            mock_trace.set_tracer_provider.assert_called_once_with(mock_provider_cls.return_value)
        finally:
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v

    def teardown_method(self) -> None:
        """每个测试后重置初始化标志。"""
        tracing_mod._otel_initialized = False


# ============================================================
# instrument_fastapi
# ============================================================


class TestInstrumentFastAPI:
    """instrument_fastapi 测试。"""

    def test_no_endpoint_skips(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """未配置 endpoint 时跳过。"""
        monkeypatch.delenv("IRIP_OTEL_ENDPOINT", raising=False)
        app = MagicMock()
        tracing_mod.instrument_fastapi(app)
        # 无异常即通过

    def test_import_error_skips(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ImportError 时跳过。"""
        monkeypatch.setenv("IRIP_OTEL_ENDPOINT", "http://jaeger:4317")
        app = MagicMock()

        import builtins

        real_import = builtins.__import__

        def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if "fastapi" in name:
                raise ImportError("not installed")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            tracing_mod.instrument_fastapi(app)

    def test_instrument_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """正常注入 FastAPI 追踪。"""
        import sys

        monkeypatch.setenv("IRIP_OTEL_ENDPOINT", "http://jaeger:4317")
        app = MagicMock()

        mock_instrumentor = MagicMock()
        otel_modules = {
            "opentelemetry": MagicMock(),
            "opentelemetry.instrumentation": MagicMock(),
            "opentelemetry.instrumentation.fastapi": MagicMock(
                FastAPIInstrumentor=mock_instrumentor
            ),
        }
        saved = {k: sys.modules.get(k) for k in otel_modules}
        sys.modules.update(otel_modules)
        try:
            tracing_mod.instrument_fastapi(app)
            mock_instrumentor.instrument_app.assert_called_once_with(app)
        finally:
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v


# ============================================================
# instrument_sqlalchemy
# ============================================================


class TestInstrumentSqlAlchemy:
    """instrument_sqlalchemy 测试。"""

    def test_no_endpoint_skips(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """未配置 endpoint 时跳过。"""
        monkeypatch.delenv("IRIP_OTEL_ENDPOINT", raising=False)
        engine = MagicMock()
        tracing_mod.instrument_sqlalchemy(engine)

    def test_import_error_skips(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ImportError 时跳过。"""
        monkeypatch.setenv("IRIP_OTEL_ENDPOINT", "http://jaeger:4317")
        engine = MagicMock()

        import builtins

        real_import = builtins.__import__

        def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if "sqlalchemy" in name and "instrumentation" in name:
                raise ImportError("not installed")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            tracing_mod.instrument_sqlalchemy(engine)

    def test_instrument_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """正常注入 SQLAlchemy 追踪。"""
        import sys

        monkeypatch.setenv("IRIP_OTEL_ENDPOINT", "http://jaeger:4317")
        engine = MagicMock()

        mock_instrumentor = MagicMock()
        otel_modules = {
            "opentelemetry": MagicMock(),
            "opentelemetry.instrumentation": MagicMock(),
            "opentelemetry.instrumentation.sqlalchemy": MagicMock(
                SQLAlchemyInstrumentor=mock_instrumentor
            ),
        }
        saved = {k: sys.modules.get(k) for k in otel_modules}
        sys.modules.update(otel_modules)
        try:
            tracing_mod.instrument_sqlalchemy(engine)
            mock_instrumentor.instrument.assert_called_once_with(engine=engine)
        finally:
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v
