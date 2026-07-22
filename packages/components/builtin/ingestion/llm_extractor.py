"""大模型驱动数据提取组件。

使用自然语言 prompt 从文件中提取结构化数据，通过大模型
（OpenAI 兼容 API）解析文件内容并返回 ObservationTable。

参数：
- path: 文件路径（必填）。
- prompt: 提取指令（必填），如"提取粒度数据"。
- schema: 目标字段定义（必填），
  如 ``[{"name": "sample_id", "type": "string"}, {"name": "D50_um", "type": "number"}]``。
- timeout: LLM 调用超时秒数（可选，默认 60）。
"""

import json
import re
from pathlib import Path
from typing import Any

import httpx

from apps.api.routers.ai_config import get_active_ai_config
from packages.common.errors import AppError
from packages.components.builtin.types import ObservationTable
from packages.components.sdk import ComponentContext, ComponentResult

#: 文件内容最大字符数（截断防止超长输入）。
_MAX_CONTENT_CHARS: int = 50000

#: System message 模板。
_SYSTEM_MESSAGE: str = (
    "你是一个数据提取助手。根据用户提供的文件内容和提取指令，"
    '提取结构化数据。返回JSON格式：'
    '{"rows": [{"字段名": "值", ...}, ...]}。只返回JSON，不要解释。'
)


class LLMExtractor:
    """大模型驱动数据提取组件。

    通过调用 OpenAI 兼容的大模型 API，将自然语言提取指令与
    文件内容结合，提取结构化数据并输出为 ObservationTable。
    """

    async def execute(
        self,
        context: ComponentContext,
        params: dict[str, Any],
    ) -> ComponentResult:
        """读取文件内容并调用大模型提取结构化数据。

        Args:
            context: 组件执行上下文。
            params: 组件参数，包含 path/prompt/schema/timeout。

        Returns:
            ComponentResult: 包含 observations 输出端口的执行结果。

        Raises:
            AppError: 当 AI 未配置、调用超时或 JSON 解析失败时。
        """
        path_str: str = params["path"]
        prompt: str = params["prompt"]
        schema: list[dict[str, Any]] = params["schema"]
        timeout: int = params.get("timeout", 60)

        file_path: Path = Path(path_str)

        # 1. 读取文件内容（UTF-8，截断到 50000 字符防止超长）
        content: str = file_path.read_text(encoding="utf-8")
        if len(content) > _MAX_CONTENT_CHARS:
            content = content[:_MAX_CONTENT_CHARS]

        # 2. 空文件直接返回空表（不调用 LLM，节省开销）
        columns: tuple[str, ...] = tuple(
            field["name"] for field in schema
        )
        if not content.strip():
            table = ObservationTable(
                columns=columns,
                rows=(),
                source_locations=(),
            )
            return ComponentResult(
                outputs={"observations": table},
                summary="LLM 提取 0 行数据（空文件）",
                metadata={
                    "row_count": 0,
                    "preview_rows": [],
                },
            )

        # 3. 获取 AI 配置
        config: dict[str, Any] | None = await get_active_ai_config()
        if config is None:
            raise AppError(
                code="ai_not_configured",
                message="AI 大模型未配置，请在平台治理 → AI 配置中开启",
                retryable=False,
            )

        # 4. 构建 LLM 请求体
        schema_description: str = json.dumps(
            schema, ensure_ascii=False, indent=2
        )
        user_message: str = (
            f"提取指令：{prompt}\n\n"
            f"目标字段定义（JSON Schema）：\n{schema_description}\n\n"
            f"文件内容：\n{content}"
        )

        request_body: dict[str, Any] = {
            "model": config["model_name"],
            "messages": [
                {"role": "system", "content": _SYSTEM_MESSAGE},
                {"role": "user", "content": user_message},
            ],
            "chat_template_kwargs": {
                "enable_thinking": config.get("thinking_enabled", False),
            },
        }

        # 5. 调用 LLM API（OpenAI 兼容格式）
        base_url: str = str(config["base_url"]).rstrip("/")
        url: str = f"{base_url}/chat/completions"
        headers: dict[str, str] = {
            "Authorization": f"Bearer {config['api_key']}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(
                timeout=float(timeout), proxy=None
            ) as client:
                resp = await client.post(
                    url, headers=headers, json=request_body
                )
        except httpx.TimeoutException:
            raise AppError(
                code="ai_timeout",
                message=f"LLM 调用超时（{timeout} 秒）",
                retryable=True,
            )
        except httpx.HTTPError as exc:
            raise AppError(
                code="ai_request_failed",
                message=f"LLM 请求失败：{str(exc)[:200]}",
                retryable=True,
            )

        if resp.status_code != 200:
            raise AppError(
                code="ai_request_failed",
                message=f"LLM API 返回 {resp.status_code}: {resp.text[:200]}",
                retryable=True,
            )

        # 6. 解析 LLM 返回的 JSON
        resp_data: dict[str, Any] = resp.json()
        choices: list[dict[str, Any]] = resp_data.get("choices", [])
        if not choices:
            raise AppError(
                code="ai_empty_response",
                message="LLM 返回空响应",
                retryable=True,
            )
        llm_content: str = choices[0]["message"]["content"]

        extracted_data: dict[str, Any] = _parse_llm_json(llm_content)
        raw_rows: list[dict[str, Any]] = extracted_data.get("rows", [])

        # 7. 按 schema 中的 type 做类型转换
        type_map: dict[str, str] = {
            field["name"]: field["type"] for field in schema
        }
        converted_rows: list[dict[str, Any]] = []
        source_locs: list[dict[str, Any]] = []
        for idx, row in enumerate(raw_rows, start=1):
            converted: dict[str, Any] = {}
            for col_name in columns:
                value: Any = row.get(col_name)
                target_type: str = type_map.get(col_name, "string")
                converted[col_name] = _convert_type(value, target_type)
            converted_rows.append(converted)
            source_locs.append({"file": file_path.name, "row": idx})

        # 8. 构建 ObservationTable 并返回
        table = ObservationTable(
            columns=columns,
            rows=tuple(converted_rows),
            source_locations=tuple(source_locs),
        )

        return ComponentResult(
            outputs={"observations": table},
            summary=f"LLM 提取 {table.row_count()} 行数据",
            metadata={
                "row_count": table.row_count(),
                "preview_rows": converted_rows[:5],
            },
        )


def _parse_llm_json(content: str) -> dict[str, Any]:
    """从 LLM 返回内容中提取 JSON 对象。

    兼容以下格式：
    - 纯 JSON: ``{"rows": [...]}``
    - Markdown 代码块包裹: ````json\\n{...}\\n````
    - 无语言标识的代码块: ````\\n{...}\\n````
    - 包含多余文本的混合内容（提取首个 { 到末尾 } 的子串）。

    Args:
        content: LLM 返回的原始文本。

    Returns:
        dict: 解析出的 JSON 对象。

    Raises:
        AppError: 当无法从内容中解析出有效 JSON 时。
    """
    # 尝试直接解析
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    # 尝试提取 markdown 代码块中的 JSON
    pattern: str = r"```(?:json)?\s*\n?(.*?)\n?```"
    match = re.search(pattern, content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # 尝试提取第一个 { 到最后一个 } 之间的内容
    start: int = content.find("{")
    end: int = content.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(content[start : end + 1])
        except json.JSONDecodeError:
            pass

    raise AppError(
        code="ai_parse_failed",
        message=f"无法从 LLM 响应中解析 JSON：{content[:200]}",
        retryable=True,
    )


def _convert_type(value: Any, target_type: str) -> Any:
    """按目标类型转换值。

    Args:
        value: 原始值（可能为字符串或其他类型）。
        target_type: 目标类型，支持 string/number/integer/boolean。

    Returns:
        Any: 转换后的值。转换失败时保留原始值，None 原样返回。
    """
    if value is None:
        return None

    if target_type == "string":
        return str(value)
    elif target_type == "number":
        try:
            return float(value)
        except (ValueError, TypeError):
            return value
    elif target_type == "integer":
        try:
            return int(value)
        except (ValueError, TypeError):
            try:
                return int(float(value))
            except (ValueError, TypeError):
                return value
    elif target_type == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lower: str = value.lower().strip()
            if lower in ("true", "1", "yes", "是"):
                return True
            if lower in ("false", "0", "no", "否"):
                return False
        return bool(value)
    else:
        return value
