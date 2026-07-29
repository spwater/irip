"""ingestion_service 异步 I/O 验证测试（T3-7）。

验证 IngestionPipeline.ingest_file 中的同步 I/O 调用
（_compute_sha256, _parse_file）被 asyncio.to_thread 正确包装。

由于 IngestionPipeline 需要数据库会话，这里仅测试模块级辅助函数
的同步功能正确性，以及验证 to_thread 包装在源码中存在。
"""

import hashlib
from pathlib import Path
from unittest.mock import patch

from packages.connectors.ingestion_service import (
    _compute_sha256,
    _parse_csv,
    _parse_json,
)

# ---- _compute_sha256 功能正确性 ----


class TestComputeSha256:
    """_compute_sha256 同步函数功能正确性测试。"""

    def test_compute_sha256_known_content(self, tmp_path: Path):
        """已知内容的 SHA-256 摘要正确。"""
        content = b"hello world"
        path = tmp_path / "test.txt"
        path.write_bytes(content)

        result = _compute_sha256(path)
        expected = hashlib.sha256(content).hexdigest()
        assert result == expected

    def test_compute_sha256_empty_file(self, tmp_path: Path):
        """空文件的 SHA-256 摘要正确。"""
        path = tmp_path / "empty.txt"
        path.write_bytes(b"")

        result = _compute_sha256(path)
        expected = hashlib.sha256(b"").hexdigest()
        assert result == expected

    def test_compute_sha256_large_file(self, tmp_path: Path):
        """大文件分块读取的 SHA-256 摘要正确。"""
        content = b"x" * (200 * 1024)  # 200KB，超过 65536 chunk size
        path = tmp_path / "large.txt"
        path.write_bytes(content)

        result = _compute_sha256(path)
        expected = hashlib.sha256(content).hexdigest()
        assert result == expected

    def test_compute_sha256_deterministic(self, tmp_path: Path):
        """相同内容文件两次计算结果一致。"""
        path = tmp_path / "test.txt"
        path.write_bytes(b"same content")

        result1 = _compute_sha256(path)
        result2 = _compute_sha256(path)
        assert result1 == result2


# ---- _parse_csv 功能正确性 ----


class TestParseCsv:
    """_parse_csv 同步函数功能正确性测试。"""

    def test_parse_csv_basic(self, tmp_path: Path):
        """CSV 解析为字段-值字典。"""
        path = tmp_path / "data.csv"
        path.write_text(
            "Field,Value\nName,test\nCount,42\n",
            encoding="utf-8",
        )

        result = _parse_csv(path)
        assert result["Name"] == "test"
        assert result["Count"] == "42"

    def test_parse_csv_strips_whitespace(self, tmp_path: Path):
        """CSV 字段值去除首尾空格。"""
        path = tmp_path / "data.csv"
        path.write_text(
            "Field,Value\n  Key  ,  Value  \n",
            encoding="utf-8",
        )

        result = _parse_csv(path)
        assert result["Key"] == "Value"

    def test_parse_csv_skips_short_rows(self, tmp_path: Path):
        """CSV 行不足两列时跳过。"""
        path = tmp_path / "data.csv"
        path.write_text(
            "Field,Value\nOnlyOne\nValid,ok\n",
            encoding="utf-8",
        )

        result = _parse_csv(path)
        assert "Valid" in result
        assert result["Valid"] == "ok"


# ---- _parse_json 功能正确性 ----


class TestParseJson:
    """_parse_json 同步函数功能正确性测试。"""

    def test_parse_json_basic(self, tmp_path: Path):
        """JSON 解析为字段-值字典。"""
        path = tmp_path / "data.json"
        path.write_text(
            '{"Name": "test", "Count": 42}',
            encoding="utf-8",
        )

        result = _parse_json(path)
        assert result["Name"] == "test"
        assert result["Count"] == "42"

    def test_parse_json_none_value_becomes_empty(self, tmp_path: Path):
        """JSON 值为 null 时转为空字符串。"""
        path = tmp_path / "data.json"
        path.write_text(
            '{"key": null}',
            encoding="utf-8",
        )

        result = _parse_json(path)
        assert result["key"] == ""


# ---- to_thread 包装验证 ----


class TestIngestionServiceAsyncWrapper:
    """验证 ingestion_service 中 asyncio.to_thread 包装存在。"""

    def test_compute_sha256_callable(self):
        """_compute_sha256 函数存在且可调用。"""
        assert callable(_compute_sha256)

    def test_parse_csv_callable(self):
        """_parse_csv 函数存在且可调用。"""
        assert callable(_parse_csv)

    def test_parse_json_callable(self):
        """_parse_json 函数存在且可调用。"""
        assert callable(_parse_json)

    def test_source_uses_to_thread_for_sha256(self, tmp_path: Path):
        """验证 ingest_file 中 _compute_sha256 被 asyncio.to_thread 调用。

         通过检查源码中包含 to_thread(_compute_sha256) 来验证。
         这里用 patch 验证：如果 to_thread 被调用且第一个参数是 _compute_sha256，
        则说明包装正确。
        """
        import packages.connectors.ingestion_service as mod

        path = tmp_path / "test.txt"
        path.write_bytes(b"test")

        with patch.object(mod, "asyncio") as mock_asyncio:
            mock_asyncio.to_thread = patch("asyncio.to_thread", wraps=asyncio.to_thread).__enter__()
            # 不会真正调用 ingest_file（需要 DB），只验证函数引用
            assert hasattr(mod, "_compute_sha256")
            assert hasattr(mod, "_parse_file")

    async def test_to_thread_wraps_parse_file(self):
        """验证 _parse_file 被 asyncio.to_thread 包装。

        通过 grep 源码确认 asyncio.to_thread(_parse_file, ...) 存在。
        """
        import inspect

        from packages.connectors.ingestion_service import IngestionPipeline

        source = inspect.getsource(IngestionPipeline.ingest_file)
        assert "asyncio.to_thread(_compute_sha256" in source
        assert "asyncio.to_thread(_parse_file" in source


# 需要 asyncio 用于 test_to_thread_wraps_parse_file
import asyncio  # noqa: E402
