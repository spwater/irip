"""IRIP 模型适用域检查器（V2-T04）。

ApplicabilityChecker 验证输入参数的每个维度值是否落在
模型契约声明的适用域 [min, max] 范围内。超出适用域的输入
应在预测前被拒绝，避免外推产生不可靠结果。

设计要点：
- 适用域以 ``{dimension: {"min": float, "max": float}}`` 形式声明；
- 检查时仅校验适用域中声明的维度，缺失维度视为通过（不报错）；
- 超出范围时返回 valid=False，errors 含 "outside_applicability_domain"。
"""

from typing import Any

from packages.models.contracts import ValidationResult


class ApplicabilityChecker:
    """模型适用域检查器。

    校验输入字典中各维度的值是否落在适用域 [min, max] 范围内。

    适用域格式::

        {
            "clinker_feed_tph": {"min": 120.0, "max": 300.0},
            "grate_speed_m_min": {"min": 1.2, "max": 4.0}
        }
    """

    def check(
        self,
        inputs: dict[str, Any],
        domain: dict[str, Any],
    ) -> ValidationResult:
        """校验输入是否在适用域范围内。

        遍历适用域中声明的每个维度，检查 inputs 中对应值是否在
        [min, max] 范围内。缺失的维度视为通过（不报错），
        超出范围的维度收集为错误。

        Args:
            inputs: 输入参数字典（维度名 → 值）。
            domain: 适用域字典（维度名 → {"min": float, "max": float}）。

        Returns:
            ValidationResult: 校验结果。全部通过返回 valid=True；
            任一维度超出范围返回 valid=False，errors 含
            "outside_applicability_domain" 及具体维度信息。
        """
        errors: list[str] = []

        for dimension, bounds in domain.items():
            value = inputs.get(dimension)
            # 缺失维度不校验（由 input_schema 校验必填性）
            if value is None:
                continue
            # 非数值类型跳过（由 input_schema 校验类型）
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue

            min_val = bounds.get("min")
            max_val = bounds.get("max")
            if min_val is None or max_val is None:
                continue

            numeric_value = float(value)
            if numeric_value < float(min_val) or numeric_value > float(max_val):
                errors.append("outside_applicability_domain")
                errors.append(
                    f"dimension '{dimension}' value {numeric_value} outside [{min_val}, {max_val}]"
                )

        if errors:
            return ValidationResult(valid=False, errors=tuple(errors))
        return ValidationResult(valid=True, errors=())
