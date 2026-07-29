"""曲线拟合组件。

使用 scipy 对数据进行曲线拟合，支持线性、多项式、指数等模型。

参数：
- observations: 输入 ObservationTable（必填）。
- x_column: X 轴列名（必填）。
- y_column: Y 轴列名（必填）。
- model: 模型类型（linear/polynomial/exponential/power，必填）。
- degree: 多项式阶数（polynomial 模型时使用，可选，默认 2）。
"""

from typing import Any

import numpy as np
from scipy.optimize import curve_fit

from packages.components.builtin.types import (
    DiagnosticReport,
    ObservationTable,
)
from packages.components.sdk import ComponentContext, ComponentResult


def _linear(x: Any, a: float, b: float) -> Any:
    """线性模型 y = a*x + b。"""
    return a * x + b


def _polynomial(x: Any, *coeffs: float) -> Any:
    """多项式模型。"""
    return sum(c * x**i for i, c in enumerate(coeffs))


def _exponential(x: Any, a: float, b: float) -> Any:
    """指数模型 y = a * exp(b*x)。"""
    return a * np.exp(b * x)


def _power(x: Any, a: float, b: float) -> Any:
    """幂律模型 y = a * x^b。"""
    return a * np.power(x, b)


#: 模型函数映射。
_MODELS: dict[str, Any] = {
    "linear": _linear,
    "polynomial": _polynomial,
    "exponential": _exponential,
    "power": _power,
}


class CurveFit:
    """曲线拟合组件。"""

    async def execute(
        self,
        context: ComponentContext,
        params: dict[str, Any],
    ) -> ComponentResult:
        """执行曲线拟合。"""
        table: ObservationTable = params["observations"]
        x_column: str = params["x_column"]
        y_column: str = params["y_column"]
        model: str = params["model"]
        degree: int = int(params.get("degree", 2))

        warnings: list[str] = []

        if model not in _MODELS:
            return ComponentResult(
                outputs={},
                summary=f"不支持的模型: {model}",
                metadata={},
                diagnostics={"warnings": [f"unsupported_model:{model}"]},
            )

        xs: list[float] = []
        ys: list[float] = []
        for row in table.rows:
            xv = row.get(x_column)
            yv = row.get(y_column)
            if (
                xv is not None
                and yv is not None
                and isinstance(xv, (int, float))
                and isinstance(yv, (int, float))
            ):
                xs.append(float(xv))
                ys.append(float(yv))

        if len(xs) < 3:
            warnings.append("有效数据点不足 3 个")
            report = DiagnosticReport(
                component="curve_fit",
                input_rows=table.row_count(),
                output_rows=0,
                warnings=tuple(warnings),
                row_annotations=(),
            )
            return ComponentResult(
                outputs={"model_params": {}, "diagnostics": report},
                summary="数据不足",
                metadata={"point_count": len(xs)},
            )

        x_arr = np.array(xs)
        y_arr = np.array(ys)

        func = _MODELS[model]

        try:
            if model == "polynomial":
                p0 = [1.0] * (degree + 1)
                popt, pcov = curve_fit(func, x_arr, y_arr, p0=p0, maxfev=10000)
                param_names = [f"c{i}" for i in range(degree + 1)]
            elif model == "linear":
                popt, pcov = curve_fit(func, x_arr, y_arr, maxfev=10000)
                param_names = ["slope", "intercept"]
            else:
                popt, pcov = curve_fit(func, x_arr, y_arr, maxfev=10000)
                param_names = ["a", "b"]

            perr = np.sqrt(np.diag(pcov))

            # 计算 R²
            y_pred = func(x_arr, *popt)
            ss_res = np.sum((y_arr - y_pred) ** 2)
            ss_tot = np.sum((y_arr - np.mean(y_arr)) ** 2)
            r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

            model_params: dict[str, Any] = {
                "model": model,
                "parameters": {
                    name: {
                        "value": float(popt[i]),
                        "std_error": float(perr[i]),
                    }
                    for i, name in enumerate(param_names)
                },
                "r_squared": float(r_squared),
                "rmse": float(np.sqrt(ss_res / len(xs))),
                "point_count": len(xs),
            }

        except (RuntimeError, ValueError) as exc:
            warnings.append(f"拟合失败: {exc}")
            report = DiagnosticReport(
                component="curve_fit",
                input_rows=table.row_count(),
                output_rows=0,
                warnings=tuple(warnings),
                row_annotations=(),
            )
            return ComponentResult(
                outputs={"model_params": {}, "diagnostics": report},
                summary="拟合失败",
                metadata={"error": str(exc)},
            )

        report = DiagnosticReport(
            component="curve_fit",
            input_rows=table.row_count(),
            output_rows=1,
            warnings=tuple(warnings),
            row_annotations=(),
        )
        return ComponentResult(
            outputs={
                "model_params": model_params,
                "diagnostics": report,
            },
            summary=f"曲线拟合（{model}）: R²={r_squared:.4f}",
            metadata={
                "model": model,
                "r_squared": float(r_squared),
                "point_count": len(xs),
            },
        )
