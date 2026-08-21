"""安全测试：敏感字段脱敏 + 敏感日志 sink 扫描。

验证：
1. ``redact_sensitive_fields`` 对已知敏感键递归脱敏（包括嵌套 dict/list）；
2. 源码中不存在已知敏感日志 sink（``irip-insight-debug.log``、``[PAYLOAD msg``）。
"""

from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "key",
    [
        "prompt",
        "content",
        "tool_result",
        "api_key",
        "authorization",
        "cookie",
        "database_url",
    ],
)
def test_sensitive_fields_are_redacted(key: str) -> None:
    """敏感键的值应被替换为 [REDACTED]，非敏感键保持不变。"""
    from packages.common.logging_setup import redact_sensitive_fields

    event: dict[str, object] = {key: "industrial-secret-42", "trace_id": "trace-1"}
    result = redact_sensitive_fields(None, "info", event)
    assert result[key] == "[REDACTED]"
    assert result["trace_id"] == "trace-1"


def test_redact_handles_nested_dicts() -> None:
    """嵌套 dict 中的敏感键也应被脱敏。"""
    from packages.common.logging_setup import redact_sensitive_fields

    event: dict[str, object] = {
        "outer": {
            "prompt": "secret-prompt",
            "safe_key": "safe-value",
        },
        "content": "top-level-secret",
    }
    result = redact_sensitive_fields(None, "info", event)
    assert result["content"] == "[REDACTED]"
    outer = result["outer"]
    assert isinstance(outer, dict)
    assert outer["prompt"] == "[REDACTED]"
    assert outer["safe_key"] == "safe-value"


def test_redact_handles_lists() -> None:
    """list 内嵌套 dict 中的敏感键也应被脱敏。"""
    from packages.common.logging_setup import redact_sensitive_fields

    event: dict[str, object] = {
        "messages": [
            {"role": "user", "content": "user-secret"},
            {"role": "assistant", "content": "assistant-secret"},
        ],
    }
    result = redact_sensitive_fields(None, "info", event)
    assert result["messages"] == "[REDACTED]"


def test_redact_preserves_non_sensitive_values() -> None:
    """非敏感键的值应原样保留，包括数字、布尔值等。"""
    from packages.common.logging_setup import redact_sensitive_fields

    event: dict[str, object] = {
        "trace_id": "trace-1",
        "status_code": 200,
        "duration_ms": 42.5,
        "success": True,
        "model": "gpt-4o",
    }
    result = redact_sensitive_fields(None, "info", event)
    assert result == event


def test_no_known_sensitive_debug_sinks() -> None:
    """源码中不应存在已知敏感日志 sink。"""
    roots = [Path("apps"), Path("packages")]
    source = "\n".join(p.read_text(errors="ignore") for root in roots for p in root.rglob("*.py"))
    assert "irip-insight-debug.log" not in source
    assert "[PAYLOAD msg" not in source
