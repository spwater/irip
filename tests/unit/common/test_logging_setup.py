"""单元测试：logging_setup structlog 日志配置 + correlation ID 中间件。

覆盖：
- configure_logging：JSON / Console 格式 / 日志级别 / 服务名绑定；
- CorrelationIdMiddleware：从请求头提取 / 自动生成 / 响应头回传 / contextvar 清理；
- get_correlation_id：有/无上下文；
- get_logger：返回 BoundLogger。
"""

from unittest.mock import AsyncMock, MagicMock

import structlog

from packages.common.logging_setup import (
    CORRELATION_ID_CONTEXT_KEY,
    CORRELATION_ID_HEADER,
    SENSITIVE_LOG_KEYS,
    CorrelationIdMiddleware,
    configure_logging,
    get_correlation_id,
    get_logger,
    redact_sensitive_fields,
)


class TestConfigureLogging:
    """configure_logging 测试。"""

    def test_json_output(self) -> None:
        """JSON 格式配置不抛异常。"""
        configure_logging(service_name="api", log_level="INFO", json_output=True)
        logger = structlog.get_logger("test")
        logger.info("test message")

    def test_console_output(self) -> None:
        """Console 格式配置不抛异常。"""
        configure_logging(service_name="worker", log_level="DEBUG", json_output=False)
        logger = structlog.get_logger("test")
        logger.debug("debug message")

    def test_different_log_levels(self) -> None:
        """不同日志级别配置。"""
        for level in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
            configure_logging(log_level=level)

    def test_binds_service_name(self) -> None:
        """服务名绑定到 contextvar。"""
        configure_logging(service_name="my-service")
        ctx = structlog.contextvars.get_contextvars()
        assert ctx.get("service") == "my-service"


class TestCorrelationIdMiddleware:
    """CorrelationIdMiddleware 测试。"""

    async def test_extract_from_header(self) -> None:
        """从请求头提取 correlation ID。"""
        app = MagicMock()
        middleware = CorrelationIdMiddleware(app)

        request = MagicMock()
        request.headers = {CORRELATION_ID_HEADER: "req-123"}

        response = MagicMock()
        response.headers = {}
        call_next = AsyncMock(return_value=response)

        result = await middleware.dispatch(request, call_next)

        assert result is response
        assert response.headers[CORRELATION_ID_HEADER] == "req-123"

    async def test_generate_when_missing(self) -> None:
        """请求头无 correlation ID 时自动生成。"""
        app = MagicMock()
        middleware = CorrelationIdMiddleware(app)

        request = MagicMock()
        request.headers = {}

        response = MagicMock()
        response.headers = {}
        call_next = AsyncMock(return_value=response)

        result = await middleware.dispatch(request, call_next)

        assert result is response
        assert CORRELATION_ID_HEADER in response.headers
        assert len(response.headers[CORRELATION_ID_HEADER]) > 0

    async def test_clears_contextvar_after(self) -> None:
        """请求结束后清除 contextvar。"""
        app = MagicMock()
        middleware = CorrelationIdMiddleware(app)

        request = MagicMock()
        request.headers = {CORRELATION_ID_HEADER: "req-456"}

        response = MagicMock()
        response.headers = {}
        call_next = AsyncMock(return_value=response)

        await middleware.dispatch(request, call_next)

        ctx = structlog.contextvars.get_contextvars()
        assert CORRELATION_ID_CONTEXT_KEY not in ctx

    async def test_custom_header_name(self) -> None:
        """自定义 header 名称。"""
        app = MagicMock()
        middleware = CorrelationIdMiddleware(app, header_name="X-Trace-ID")

        request = MagicMock()
        request.headers = {"X-Trace-ID": "trace-789"}

        response = MagicMock()
        response.headers = {}
        call_next = AsyncMock(return_value=response)

        await middleware.dispatch(request, call_next)
        assert response.headers["X-Trace-ID"] == "trace-789"


class TestGetCorrelationId:
    """get_correlation_id 测试。"""

    def test_returns_none_without_context(self) -> None:
        """无请求上下文时返回 None。"""
        structlog.contextvars.clear_contextvars()
        assert get_correlation_id() is None

    def test_returns_id_with_context(self) -> None:
        """有上下文时返回 correlation ID。"""
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(**{CORRELATION_ID_CONTEXT_KEY: "test-id"})
        assert get_correlation_id() == "test-id"
        structlog.contextvars.clear_contextvars()


class TestGetLogger:
    """get_logger 测试。"""

    def test_returns_logger(self) -> None:
        """返回 structlog logger。"""
        configure_logging()
        logger = get_logger("my.module")
        assert logger is not None

    def test_returns_logger_without_name(self) -> None:
        """无名称时也能返回 logger。"""
        configure_logging()
        logger = get_logger()
        assert logger is not None


class TestConstants:
    """常量测试。"""

    def test_correlation_id_header(self) -> None:
        """CORRELATION_ID_HEADER 值正确。"""
        assert CORRELATION_ID_HEADER == "X-Request-ID"

    def test_correlation_id_context_key(self) -> None:
        """CORRELATION_ID_CONTEXT_KEY 值正确。"""
        assert CORRELATION_ID_CONTEXT_KEY == "correlation_id"


class TestSensitiveLogKeys:
    """SENSITIVE_LOG_KEYS 常量测试。"""

    def test_is_frozenset(self) -> None:
        """SENSITIVE_LOG_KEYS 应为 frozenset。"""
        assert isinstance(SENSITIVE_LOG_KEYS, frozenset)

    def test_contains_known_sensitive_keys(self) -> None:
        """应包含已知敏感键。"""
        expected = {
            "prompt", "content", "messages", "tool_result", "analysis_markdown",
            "statement", "api_key", "authorization", "cookie", "token",
            "password", "database_url", "secret_key",
        }
        assert expected.issubset(SENSITIVE_LOG_KEYS)


class TestRedactSensitiveFields:
    """redact_sensitive_fields 处理器测试。"""

    def test_redacts_top_level_sensitive_key(self) -> None:
        """顶层敏感键被脱敏。"""
        event: dict[str, object] = {"prompt": "secret", "trace_id": "t1"}
        result = redact_sensitive_fields(None, "info", event)
        assert result["prompt"] == "[REDACTED]"
        assert result["trace_id"] == "t1"

    def test_redacts_nested_dict(self) -> None:
        """嵌套 dict 中的敏感键被脱敏。"""
        event: dict[str, object] = {
            "data": {"content": "secret-content", "id": "safe"},
        }
        result = redact_sensitive_fields(None, "info", event)
        data = result["data"]
        assert isinstance(data, dict)
        assert data["content"] == "[REDACTED]"
        assert data["id"] == "safe"

    def test_redacts_list_of_dicts(self) -> None:
        """list 中的 dict 敏感键被脱敏。"""
        event: dict[str, object] = {
            "items": [{"api_key": "key1"}, {"token": "tok2"}],
        }
        result = redact_sensitive_fields(None, "info", event)
        items = result["items"]
        assert isinstance(items, list)
        assert items[0]["api_key"] == "[REDACTED]"
        assert items[1]["token"] == "[REDACTED]"

    def test_case_insensitive_key_matching(self) -> None:
        """键名匹配应大小写不敏感。"""
        event: dict[str, object] = {"API_KEY": "secret", "Password": "p"}
        result = redact_sensitive_fields(None, "info", event)
        assert result["API_KEY"] == "[REDACTED]"
        assert result["Password"] == "[REDACTED]"

    def test_preserves_non_sensitive_keys(self) -> None:
        """非敏感键原样保留。"""
        event: dict[str, object] = {
            "trace_id": "t1",
            "service": "api",
            "status": 200,
        }
        result = redact_sensitive_fields(None, "info", event)
        assert result == event

    def test_returns_dict_type(self) -> None:
        """返回值应为 dict。"""
        event: dict[str, object] = {"prompt": "x"}
        result = redact_sensitive_fields(None, "info", event)
        assert isinstance(result, dict)

    def test_empty_event_dict(self) -> None:
        """空 event_dict 不抛异常。"""
        result = redact_sensitive_fields(None, "info", {})
        assert result == {}
