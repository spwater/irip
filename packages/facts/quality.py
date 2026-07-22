"""质量评估引擎（IRIP Task 16）。

定义质量等级（Q0-Q3）、质量评估结果与质量评估引擎。

质量等级：
- Q0: 基本格式校验（必填字段存在性等）；
- Q1: 范围/单位校验（正值校验、湿度范围等）；
- Q2: 交叉一致性校验（D10 < D50 < D90 排序等）；
- Q3: 业务规则校验（行业特定规则）。

QualityEngine 注册规则集，对标准化观察值字典批量评估，
返回 QualityAssessment（含多条 QualityResult、总体状态与统计摘要）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from packages.facts.quality_rules import QualityRule


class QualityLevel(StrEnum):
    """质量等级（Q0-Q3）。

    Attributes:
        Q0: 基本格式校验（必填字段存在性）。
        Q1: 范围/单位校验（正值、湿度范围）。
        Q2: 交叉一致性校验（D10 < D50 < D90 排序）。
        Q3: 业务规则校验（行业特定规则）。
    """

    Q0 = "q0"
    Q1 = "q1"
    Q2 = "q2"
    Q3 = "q3"


@dataclass(frozen=True)
class QualityResult:
    """单条质量规则评估结果。

    Attributes:
        level: 质量等级（Q0-Q3）。
        code: 规则代码，如 ``"particle_quantile_order"``。
        status: 评估状态（passed / warning / blocked）。
        evidence: 证据详情，如 ``{"d10": 20, "d50": 18, "d90": 40}``。
    """

    level: QualityLevel
    code: str
    status: Literal["passed", "warning", "blocked"]
    evidence: dict[str, object]


@dataclass(frozen=True)
class QualityAssessment:
    """完整质量评估结果（多条规则）。

    Attributes:
        results: 所有规则评估结果元组。
        overall_status: 总体状态（blocked > warning > passed）。
        summary: 统计摘要 ``{"passed": N, "warning": N, "blocked": N}``。
    """

    results: tuple[QualityResult, ...]
    overall_status: Literal["passed", "warning", "blocked"]
    summary: dict[str, object]


class QualityEngine:
    """质量评估引擎：注册规则集，对观察值批量评估。

    使用默认规则集（粒子粒度相关规则）或自定义规则集。
    对每个规则调用 evaluate(observations)，收集非 None 结果，
    汇总为 QualityAssessment。

    Attributes:
        _rules: 质量规则元组。
    """

    def __init__(self, rules: tuple[QualityRule, ...] = ()) -> None:
        """初始化质量评估引擎。

        Args:
            rules: 质量规则元组。为空时使用默认规则集。
        """
        if not rules:
            from packages.facts.quality_rules import _default_rules

            rules = _default_rules()
        self._rules: tuple[QualityRule, ...] = rules

    def evaluate(self, observations: dict[str, object]) -> QualityAssessment:
        """对所有规则评估，返回综合结果。

        遍历所有已注册规则，对每条规则调用 evaluate(observations)。
        规则返回 None 表示不适用（跳过），否则收集评估结果。

        总体状态优先级：blocked > warning > passed。

        Args:
            observations: 字段名→值的字典，如
                ``{"d10_um": 1.23, "d50_um": 12.45, ...}``。

        Returns:
            QualityAssessment: 完整质量评估结果。
        """
        results: list[QualityResult] = []
        for rule in self._rules:
            result = rule.evaluate(observations)
            if result is not None:
                results.append(result)

        statuses = [r.status for r in results]
        if "blocked" in statuses:
            overall: Literal["passed", "warning", "blocked"] = "blocked"
        elif "warning" in statuses:
            overall = "warning"
        else:
            overall = "passed"

        summary: dict[str, object] = {
            "passed": sum(1 for s in statuses if s == "passed"),
            "warning": sum(1 for s in statuses if s == "warning"),
            "blocked": sum(1 for s in statuses if s == "blocked"),
        }

        return QualityAssessment(
            results=tuple(results),
            overall_status=overall,
            summary=summary,
        )
