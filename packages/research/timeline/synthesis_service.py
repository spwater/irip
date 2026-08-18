"""Synthesis service: validates and stores synthesis results.

Synthesis turns use the same Plan/Run/Extraction lifecycle as analysis turns,
but produce a SynthesisResult with a stable, type-safe structure that allows
empty (not_applicable) sections without placeholder noise.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.errors import AppError
from packages.research.timeline.contracts import (
    SynthesisResult,
    SynthesisSection,
)

logger = logging.getLogger("research.synthesis")


class SynthesisService:
    """Service for validating and storing synthesis results.

    The synthesis Turn is created by TurnService.create_synthesis_turn()
    and goes through the same Plan/Run lifecycle.  This service handles:
      - validate_and_store_result: Parse model output into SynthesisResult
      - Synthesis prompt construction with provenance labels
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self._factory = session_factory

    @staticmethod
    def validate_result(raw_output: str | dict[str, Any]) -> SynthesisResult:
        """Parse and validate raw model output into SynthesisResult.

        Uses Pydantic v2 validation with strict cross-field checks:
          - summary must be non-empty (min_length=1)
          - Each section: present requires >=1 item, not_applicable requires []
          - No placeholder noise like present: ["无冲突"]

        Args:
            raw_output: Raw JSON string or dict from the model.

        Returns:
            Validated SynthesisResult.

        Raises:
            AppError: If structured output parsing fails.
        """
        if isinstance(raw_output, str):
            try:
                data = json.loads(raw_output)
            except json.JSONDecodeError as exc:
                raise AppError(
                    code="validation_failed",
                    message="综合结果 JSON 解析失败",
                    retryable=True,
                    fields={},
                ) from exc
        else:
            data = raw_output

        try:
            return SynthesisResult.model_validate(data)
        except Exception as exc:
            raise AppError(
                code="validation_failed",
                message=f"综合结果校验失败: {exc}",
                retryable=True,
                fields={},
            ) from exc

    @staticmethod
    def build_synthesis_prompt(
        question_text: str,
        conclusion_texts: list[tuple[str, str, str]],
    ) -> str:
        """Build the synthesis prompt with provenance labels.

        Each conclusion is labeled with its source snapshot and evidence
        status. The prompt explicitly states that citing historical
        conclusions does NOT mean they have been verified by the latest
        snapshot.

        Args:
            question_text: The synthesis question (from turn snapshot).
            conclusion_texts: List of (statement, evidence_status, source_label).

        Returns:
            The prompt text for the model.
        """
        parts = [
            f"研究问题: {question_text}",
            "",
            "以下是需要综合的历史结论。每条标注了证据状态和来源。",
            "引用历史结论不等于已被最新快照验证。",
            "",
        ]

        for i, (statement, evidence_status, source_label) in enumerate(conclusion_texts, 1):
            if evidence_status == "manual_unverified":
                parts.append(
                    f"[{i}] [manual_unverified] {statement}"
                    f"\n    来源: {source_label}"
                    f"\n    证据状态: 未关联分析证据，尚未基于当前快照复核"
                )
            else:
                parts.append(
                    f"[{i}] {statement}\n    来源: {source_label}\n    证据状态: 有分析证据支持"
                )

        parts.extend(
            [
                "",
                "请综合以上结论，输出以下结构：",
                "- summary: 综合判断（必须非空）",
                "- agreements: 共识（有则 present + items，无则 not_applicable + []）",
                "- conflicts: 冲突（有则 present + items，无则 not_applicable + []）",
                "- limitations: 限制（有则 present + items，无则 not_applicable + []）",
                "- new_hypotheses: 待验证的新假设"
                "（有则 present + items，无则 not_applicable + []）",
                "",
                '不得用 present: ["无冲突"] 代替 not_applicable: []。',
                "不得生成占位噪音。",
            ]
        )

        return "\n".join(parts)

    @staticmethod
    def validate_no_placeholder_noise(result: SynthesisResult) -> SynthesisResult:
        """Post-validation check: no section should contain placeholder noise.

        This catches cases where the model says "present" with items like
        "无冲突" or "无明显冲突" — these should be not_applicable instead.

        Args:
            result: The validated SynthesisResult.

        Returns:
            The same result if no noise detected.

        Raises:
            AppError: If placeholder noise is detected.
        """
        placeholder_patterns = {"无冲突", "无明显冲突", "无限制", "无共识", "无"}

        for section_name in ("agreements", "conflicts", "limitations", "new_hypotheses"):
            section: SynthesisSection = getattr(result, section_name)
            if section.status == "present":
                for item in section.items:
                    if item.strip() in placeholder_patterns:
                        raise AppError(
                            code="validation_failed",
                            message=f"综合结果分区 '{section_name}' 包含占位噪音: '{item}'",
                            retryable=True,
                            fields={"section": section_name, "item": item},
                        )

        return result
