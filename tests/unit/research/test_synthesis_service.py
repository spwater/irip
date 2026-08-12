"""Tests for SynthesisService: schema validation, no-noise, prompt building."""

import json

import pytest

from packages.common.errors import AppError
from packages.research.timeline.contracts import SynthesisResult, SynthesisSection
from packages.research.timeline.synthesis_service import SynthesisService


class TestValidateResult:
    """Test synthesis result parsing and validation."""

    def test_valid_all_present(self) -> None:
        data = {
            "summary": "两轮分析共同支持温度升高与收率上升有关。",
            "agreements": {"status": "present", "items": ["方向一致"]},
            "conflicts": {"status": "present", "items": ["批次差异"]},
            "limitations": {"status": "present", "items": ["样本少"]},
            "new_hypotheses": {"status": "present", "items": ["阈值效应"]},
        }
        result = SynthesisService.validate_result(data)
        assert result.summary
        assert len(result.agreements.items) == 1

    def test_conflicts_not_applicable(self) -> None:
        data = {
            "summary": "无冲突的两轮分析。",
            "agreements": {"status": "present", "items": ["一致"]},
            "conflicts": {"status": "not_applicable", "items": []},
            "limitations": {"status": "present", "items": ["样本少"]},
            "new_hypotheses": {"status": "present", "items": ["新假设"]},
        }
        result = SynthesisService.validate_result(data)
        assert result.conflicts.items == []
        assert result.conflicts.status == "not_applicable"

    def test_json_string_input(self) -> None:
        data = {
            "summary": "测试",
            "agreements": {"status": "not_applicable", "items": []},
            "conflicts": {"status": "not_applicable", "items": []},
            "limitations": {"status": "not_applicable", "items": []},
            "new_hypotheses": {"status": "not_applicable", "items": []},
        }
        result = SynthesisService.validate_result(json.dumps(data))
        assert result.summary == "测试"

    def test_invalid_json_raises(self) -> None:
        with pytest.raises(AppError, match="JSON"):
            SynthesisService.validate_result("not json")

    def test_empty_summary_raises(self) -> None:
        data = {
            "summary": "",
            "agreements": {"status": "not_applicable", "items": []},
            "conflicts": {"status": "not_applicable", "items": []},
            "limitations": {"status": "not_applicable", "items": []},
            "new_hypotheses": {"status": "not_applicable", "items": []},
        }
        with pytest.raises(AppError, match="校验失败"):
            SynthesisService.validate_result(data)

    def test_present_without_items_raises(self) -> None:
        data = {
            "summary": "测试",
            "agreements": {"status": "present", "items": []},
            "conflicts": {"status": "not_applicable", "items": []},
            "limitations": {"status": "not_applicable", "items": []},
            "new_hypotheses": {"status": "not_applicable", "items": []},
        }
        with pytest.raises(AppError):
            SynthesisService.validate_result(data)

    def test_not_applicable_with_items_raises(self) -> None:
        data = {
            "summary": "测试",
            "agreements": {"status": "not_applicable", "items": ["不应该有"]},
            "conflicts": {"status": "not_applicable", "items": []},
            "limitations": {"status": "not_applicable", "items": []},
            "new_hypotheses": {"status": "not_applicable", "items": []},
        }
        with pytest.raises(AppError):
            SynthesisService.validate_result(data)


class TestNoPlaceholderNoise:
    """Post-validation: no placeholder noise in present sections."""

    def test_no_noise_passes(self) -> None:
        result = SynthesisResult(
            summary="测试",
            agreements=SynthesisSection(status="present", items=["真实共识"]),
            conflicts=SynthesisSection(status="not_applicable", items=[]),
            limitations=SynthesisSection(status="present", items=["真实限制"]),
            new_hypotheses=SynthesisSection(status="present", items=["真实假设"]),
        )
        assert SynthesisService.validate_no_placeholder_noise(result) is result

    def test_placeholder_noise_raises(self) -> None:
        result = SynthesisResult(
            summary="测试",
            agreements=SynthesisSection(status="present", items=["无冲突"]),
            conflicts=SynthesisSection(status="not_applicable", items=[]),
            limitations=SynthesisSection(status="not_applicable", items=[]),
            new_hypotheses=SynthesisSection(status="not_applicable", items=[]),
        )
        with pytest.raises(AppError, match="占位噪音"):
            SynthesisService.validate_no_placeholder_noise(result)

    def test_not_applicable_not_checked(self) -> None:
        """not_applicable sections should not be checked for noise."""
        result = SynthesisResult(
            summary="测试",
            agreements=SynthesisSection(status="not_applicable", items=[]),
            conflicts=SynthesisSection(status="not_applicable", items=[]),
            limitations=SynthesisSection(status="not_applicable", items=[]),
            new_hypotheses=SynthesisSection(status="not_applicable", items=[]),
        )
        assert SynthesisService.validate_no_placeholder_noise(result) is result


class TestBuildSynthesisPrompt:
    """Test synthesis prompt construction."""

    def test_prompt_contains_question(self) -> None:
        prompt = SynthesisService.build_synthesis_prompt(
            "综合问题", [("结论一", "data_supported", "Run 1")]
        )
        assert "综合问题" in prompt

    def test_prompt_contains_conclusions(self) -> None:
        prompt = SynthesisService.build_synthesis_prompt(
            "问题", [("结论一", "data_supported", "Run 1")]
        )
        assert "结论一" in prompt

    def test_prompt_labels_manual_unverified(self) -> None:
        prompt = SynthesisService.build_synthesis_prompt(
            "问题", [("手工结论", "manual_unverified", "人工新增")]
        )
        assert "[manual_unverified]" in prompt
        assert "未关联分析证据" in prompt

    def test_prompt_states_history_not_verified(self) -> None:
        prompt = SynthesisService.build_synthesis_prompt(
            "问题", [("结论", "data_supported", "Run 1")]
        )
        assert "引用历史结论不等于已被最新快照验证" in prompt

    def test_prompt_instructs_no_placeholder(self) -> None:
        prompt = SynthesisService.build_synthesis_prompt(
            "问题", [("结论", "data_supported", "Run 1")]
        )
        assert "not_applicable" in prompt
        assert "占位噪音" in prompt
