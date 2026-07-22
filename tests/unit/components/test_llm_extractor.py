"""LLM 数据提取组件单元测试。

测试覆盖：
- 正常提取（mock LLM 返回 {"rows": [...]}）
- Markdown 包裹的 JSON 提取
- 空文件处理
- AI 未配置报错
- 类型转换（string → number/integer/boolean）
- source_locations 来源定位
- 超长文件截断（50000 字符上限）
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from packages.common.errors import AppError
from packages.components.builtin.ingestion.llm_extractor import LLMExtractor
from packages.components.builtin.types import ObservationTable

from tests.unit.components.conftest import make_test_context

#: Mock AI 配置。
_MOCK_CONFIG: dict[str, object] = {
    "base_url": "http://mock-llm.example.com/v1",
    "api_key": "test-api-key",
    "model_name": "test-model",
    "thinking_enabled": False,
}


def _make_mock_response(
    content: str, status_code: int = 200
) -> MagicMock:
    """构建 mock httpx.Response。

    Args:
        content: LLM 返回的文本内容（放入 choices[0].message.content）。
        status_code: HTTP 状态码。

    Returns:
        MagicMock: 模拟的 httpx.Response 对象。
    """
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = {
        "choices": [{"message": {"content": content}}]
    }
    resp.text = content
    return resp


def _make_mock_client(resp: MagicMock) -> AsyncMock:
    """构建 mock httpx.AsyncClient。

    Args:
        resp: mock httpx.Response（post 返回值）。

    Returns:
        AsyncMock: 模拟的 httpx.AsyncClient，支持 async with。
    """
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.post.return_value = resp
    return mock_client


class TestLLMExtractor:
    """LLM 数据提取组件测试。"""

    async def test_extract_normal(self, tmp_path: Path):
        """正常提取，mock LLM 返回 {"rows": [...]}，验证 ObservationTable 列/行正确。"""
        path = tmp_path / "data.csv"
        path.write_text(
            "sample_id,D50_um\ns1,14.57\ns2,12.34\n", encoding="utf-8"
        )

        llm_content = json.dumps({
            "rows": [
                {"sample_id": "s1", "D50_um": "14.57"},
                {"sample_id": "s2", "D50_um": "12.34"},
            ]
        })
        mock_resp = _make_mock_response(llm_content)
        mock_client = _make_mock_client(mock_resp)

        extractor = LLMExtractor()
        ctx = make_test_context()

        with patch(
            "packages.components.builtin.ingestion.llm_extractor.get_active_ai_config",
            new_callable=AsyncMock,
            return_value=_MOCK_CONFIG,
        ):
            with patch("httpx.AsyncClient", return_value=mock_client):
                result = await extractor.execute(ctx, {
                    "path": str(path),
                    "prompt": "提取粒度数据",
                    "schema": [
                        {"name": "sample_id", "type": "string"},
                        {"name": "D50_um", "type": "number"},
                    ],
                })

        table = result.outputs["observations"]
        assert isinstance(table, ObservationTable)
        assert table.columns == ("sample_id", "D50_um")
        assert table.row_count() == 2
        assert table.rows[0]["sample_id"] == "s1"
        assert table.rows[0]["D50_um"] == 14.57
        assert table.rows[1]["sample_id"] == "s2"
        assert table.rows[1]["D50_um"] == 12.34

    async def test_extract_with_markdown_wrapper(self, tmp_path: Path):
        """mock LLM 返回 ```json {...} ```，验证 JSON 提取。"""
        path = tmp_path / "data.csv"
        path.write_text(
            "sample_id,D50_um\ns1,14.57\n", encoding="utf-8"
        )

        llm_content = (
            '```json\n'
            '{"rows": [{"sample_id": "s1", "D50_um": "14.57"}]}\n'
            '```'
        )
        mock_resp = _make_mock_response(llm_content)
        mock_client = _make_mock_client(mock_resp)

        extractor = LLMExtractor()
        ctx = make_test_context()

        with patch(
            "packages.components.builtin.ingestion.llm_extractor.get_active_ai_config",
            new_callable=AsyncMock,
            return_value=_MOCK_CONFIG,
        ):
            with patch("httpx.AsyncClient", return_value=mock_client):
                result = await extractor.execute(ctx, {
                    "path": str(path),
                    "prompt": "提取粒度数据",
                    "schema": [
                        {"name": "sample_id", "type": "string"},
                        {"name": "D50_um", "type": "number"},
                    ],
                })

        table = result.outputs["observations"]
        assert table.row_count() == 1
        assert table.rows[0]["sample_id"] == "s1"
        assert table.rows[0]["D50_um"] == 14.57

    async def test_extract_empty_file(self, tmp_path: Path):
        """空文件，验证返回空 ObservationTable（不调用 LLM）。"""
        path = tmp_path / "empty.csv"
        path.write_text("", encoding="utf-8")

        extractor = LLMExtractor()
        ctx = make_test_context()

        # 空文件直接返回空表，不调用 get_active_ai_config / httpx
        result = await extractor.execute(ctx, {
            "path": str(path),
            "prompt": "提取粒度数据",
            "schema": [
                {"name": "sample_id", "type": "string"},
            ],
        })

        table = result.outputs["observations"]
        assert isinstance(table, ObservationTable)
        assert table.columns == ("sample_id",)
        assert table.row_count() == 0
        assert result.metadata["row_count"] == 0
        assert result.metadata["preview_rows"] == []

    async def test_ai_not_configured(self, tmp_path: Path):
        """mock get_active_ai_config 返回 None，验证抛 AppError。"""
        path = tmp_path / "data.csv"
        path.write_text(
            "sample_id,D50_um\ns1,14.57\n", encoding="utf-8"
        )

        extractor = LLMExtractor()
        ctx = make_test_context()

        with patch(
            "packages.components.builtin.ingestion.llm_extractor.get_active_ai_config",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with pytest.raises(AppError, match="AI 大模型未配置"):
                await extractor.execute(ctx, {
                    "path": str(path),
                    "prompt": "提取粒度数据",
                    "schema": [
                        {"name": "sample_id", "type": "string"},
                    ],
                })

    async def test_type_conversion(self, tmp_path: Path):
        """schema 定义 number/integer/boolean 类型，验证类型转换。"""
        path = tmp_path / "data.csv"
        path.write_text(
            "id,value,flag\n1,12.5,true\n2,30,false\n", encoding="utf-8"
        )

        llm_content = json.dumps({
            "rows": [
                {"id": "1", "value": "12.5", "flag": "true"},
                {"id": "2", "value": "30", "flag": "false"},
            ]
        })
        mock_resp = _make_mock_response(llm_content)
        mock_client = _make_mock_client(mock_resp)

        extractor = LLMExtractor()
        ctx = make_test_context()

        with patch(
            "packages.components.builtin.ingestion.llm_extractor.get_active_ai_config",
            new_callable=AsyncMock,
            return_value=_MOCK_CONFIG,
        ):
            with patch("httpx.AsyncClient", return_value=mock_client):
                result = await extractor.execute(ctx, {
                    "path": str(path),
                    "prompt": "提取数据",
                    "schema": [
                        {"name": "id", "type": "integer"},
                        {"name": "value", "type": "number"},
                        {"name": "flag", "type": "boolean"},
                    ],
                })

        table = result.outputs["observations"]
        # integer 转换
        assert table.rows[0]["id"] == 1
        assert isinstance(table.rows[0]["id"], int)
        # number 转换
        assert table.rows[0]["value"] == 12.5
        assert isinstance(table.rows[0]["value"], float)
        # boolean 转换
        assert table.rows[0]["flag"] is True
        assert isinstance(table.rows[0]["flag"], bool)
        # 第二行验证
        assert table.rows[1]["id"] == 2
        assert table.rows[1]["value"] == 30.0
        assert isinstance(table.rows[1]["value"], float)
        assert table.rows[1]["flag"] is False

    async def test_source_locations(self, tmp_path: Path):
        """验证 source_locations 包含 file 和 row。"""
        path = tmp_path / "data.csv"
        path.write_text(
            "sample_id,D50_um\ns1,14.57\ns2,12.34\n", encoding="utf-8"
        )

        llm_content = json.dumps({
            "rows": [
                {"sample_id": "s1", "D50_um": "14.57"},
                {"sample_id": "s2", "D50_um": "12.34"},
            ]
        })
        mock_resp = _make_mock_response(llm_content)
        mock_client = _make_mock_client(mock_resp)

        extractor = LLMExtractor()
        ctx = make_test_context()

        with patch(
            "packages.components.builtin.ingestion.llm_extractor.get_active_ai_config",
            new_callable=AsyncMock,
            return_value=_MOCK_CONFIG,
        ):
            with patch("httpx.AsyncClient", return_value=mock_client):
                result = await extractor.execute(ctx, {
                    "path": str(path),
                    "prompt": "提取粒度数据",
                    "schema": [
                        {"name": "sample_id", "type": "string"},
                        {"name": "D50_um", "type": "number"},
                    ],
                })

        table = result.outputs["observations"]
        assert len(table.source_locations) == 2
        assert table.source_locations[0]["file"] == "data.csv"
        assert table.source_locations[0]["row"] == 1
        assert table.source_locations[1]["file"] == "data.csv"
        assert table.source_locations[1]["row"] == 2

    async def test_large_file_truncation(self, tmp_path: Path):
        """超长文件内容被截断到 50000 字符。"""
        path = tmp_path / "large.txt"
        path.write_text("a" * 60000, encoding="utf-8")

        mock_resp = _make_mock_response('{"rows": []}')
        mock_client = _make_mock_client(mock_resp)

        extractor = LLMExtractor()
        ctx = make_test_context()

        with patch(
            "packages.components.builtin.ingestion.llm_extractor.get_active_ai_config",
            new_callable=AsyncMock,
            return_value=_MOCK_CONFIG,
        ):
            with patch("httpx.AsyncClient", return_value=mock_client):
                result = await extractor.execute(ctx, {
                    "path": str(path),
                    "prompt": "提取数据",
                    "schema": [
                        {"name": "field", "type": "string"},
                    ],
                })

        # 验证发送给 LLM 的内容被截断到 50000 字符
        call_kwargs = mock_client.post.call_args.kwargs
        request_body = call_kwargs["json"]
        user_message = request_body["messages"][1]["content"]
        # 提取 "文件内容：\n" 之后的部分
        content_part = user_message.split("文件内容：\n", 1)[1]
        assert len(content_part) == 50000
