"""服务端生成不可伪造的结构化 citation。

CitationGenerator 生成 HMAC 签名的工具调用引用，证明 AI 回答基于
真实的工具执行结果而非幻觉。签名防止前端或中间层伪造引用。

安全约定：
- 签名密钥从环境变量 ``IRIP_JWT_SECRET`` 读取（复用 JWT 密钥，无需额外配置）；
- citation 包含工具名、查询参数摘要、结果摘要、时间戳和 HMAC-SHA256 签名；
- 验证签名时重新计算 HMAC 并与 citation 中的签名比较（常量时间比较）。

用法::

    generator = CitationGenerator()
    citation = generator.generate(
        tool_name="search_facts",
        query_params={"query": "粒度"},
        result_summary="找到 3 条事实",
    )
    assert citation.signature  # HMAC-SHA256 签名
    assert CitationGenerator.verify(citation.to_dict())  # 验证签名
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from packages.common.secret_files import read_secret


def _get_signing_secret() -> str:
    """从环境变量读取签名密钥。

    Returns:
        str: 签名密钥。

    Raises:
        RuntimeError: 当环境变量未设置时。
    """
    secret = read_secret("IRIP_JWT_SECRET", required=False) or ""
    if not secret:
        # 开发环境回退（生产环境应通过 compose.yaml 强制设置）
        secret = os.getenv("IRIP_CITATION_SECRET", "irip-citation-dev-key")
    return secret


@dataclass(frozen=True)
class SignedCitation:
    """HMAC 签名的工具调用引用（不可变值对象）。

    Attributes:
        tool_name: 执行的工具名称（如 ``"search_facts"``）。
        query_params: 查询参数摘要（JSON 字符串，已截断到 500 字符）。
        result_summary: 工具执行结果摘要（如 ``"找到 3 条事实"``）。
        timestamp: 生成时间（ISO 8601 UTC）。
        signature: HMAC-SHA256 签名（hex 编码）。
    """

    tool_name: str
    query_params: str
    result_summary: str
    timestamp: str
    signature: str

    def to_dict(self) -> dict[str, str]:
        """序列化为 JSON 可存储的字典。"""
        return asdict(self)


class CitationGenerator:
    """服务端 citation 生成器，使用 HMAC-SHA256 签名防止伪造。

    Attributes:
        _secret: HMAC 签名密钥。
    """

    def __init__(self, secret: str | None = None) -> None:
        """初始化 citation 生成器。

        Args:
            secret: 签名密钥。None 时从环境变量 ``IRIP_JWT_SECRET`` 读取。
        """
        self._secret: str = secret if secret is not None else _get_signing_secret()

    def generate(
        self,
        tool_name: str,
        query_params: dict[str, object],
        result_summary: str,
    ) -> SignedCitation:
        """生成 HMAC 签名的 citation。

        Args:
            tool_name: 执行的工具名称。
            query_params: 查询参数字典（会被截断到 500 字符的 JSON 字符串）。
            result_summary: 工具执行结果摘要。

        Returns:
            SignedCitation: 带签名的 citation。
        """
        # 参数序列化为 JSON 字符串并截断
        params_json = json.dumps(
            query_params,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        if len(params_json) > 500:
            params_json = params_json[:500]

        timestamp = datetime.now(UTC).isoformat()

        # 构造签名消息：tool_name|params|summary|timestamp
        message = f"{tool_name}|{params_json}|{result_summary}|{timestamp}"
        signature = hmac.new(
            self._secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        return SignedCitation(
            tool_name=tool_name,
            query_params=params_json,
            result_summary=result_summary,
            timestamp=timestamp,
            signature=signature,
        )

    def verify(self, citation: dict[str, str]) -> bool:
        """验证 citation 的签名是否有效。

        Args:
            citation: 序列化的 citation 字典。

        Returns:
            bool: 签名有效返回 True，否则 False。
        """
        try:
            tool_name = str(citation.get("tool_name", ""))
            query_params = str(citation.get("query_params", ""))
            result_summary = str(citation.get("result_summary", ""))
            timestamp = str(citation.get("timestamp", ""))
            signature = str(citation.get("signature", ""))

            message = f"{tool_name}|{query_params}|{result_summary}|{timestamp}"
            expected_sig = hmac.new(
                self._secret.encode("utf-8"),
                message.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()

            return hmac.compare_digest(signature, expected_sig)
        except (TypeError, AttributeError):
            return False


def verify_citation(citation: dict[str, str]) -> bool:
    """模块级便捷函数：验证 citation 签名。

    Args:
        citation: 序列化的 citation 字典。

    Returns:
        bool: 签名有效返回 True，否则 False。
    """
    return CitationGenerator().verify(citation)
