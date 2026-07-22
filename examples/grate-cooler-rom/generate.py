#!/usr/bin/env python3
"""篦冷机 ROM 确定性数据集生成器（IRIP V2-T04）。

使用显式物理方程 + 固定种子噪声，生成 240 行确定性数据集。

5 输入维度：
- clinker_feed_tph（熟料给料量）: 120 - 300
- cooling_air_nm3_kg（冷却风量）: 1.6 - 3.2
- grate_speed_m_min（篦床速度）: 1.2 - 4.0
- bed_depth_mm（料层厚度）: 450 - 850
- clinker_inlet_temp_c（熟料入口温度）: 1250 - 1450

4 输出维度：
- clinker_outlet_temp_c（熟料出口温度）
- cooling_efficiency_pct（冷却效率）
- secondary_air_temp_c（二次风温度）
- fan_power_kw（风机功率）

物理方程基于篦冷机热交换机理：
- 出口温度随冷却风量、篦床速度增大而降低，随给料量、入口温度、
  料层厚度增大而升高；
- 冷却效率随冷却风量、篦床速度增大而升高，随给料量增大而降低；
- 二次风温度随入口温度升高而升高，随冷却风量增大而降低；
- 风机功率随冷却风量、料层厚度增大而增大。

用法：
    python examples/grate-cooler-rom/generate.py --output <dir> --seed 20260715
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from pathlib import Path
from typing import Any

#: 固定随机种子。
DEFAULT_SEED: int = 20260715

#: 数据集行数。
ROW_COUNT: int = 240

#: 输入维度范围（min, max）。
INPUT_RANGES: dict[str, tuple[float, float]] = {
    "clinker_feed_tph": (120.0, 300.0),
    "cooling_air_nm3_kg": (1.6, 3.2),
    "grate_speed_m_min": (1.2, 4.0),
    "bed_depth_mm": (450.0, 850.0),
    "clinker_inlet_temp_c": (1250.0, 1450.0),
}

#: 输入维度顺序。
INPUT_DIMS: list[str] = [
    "clinker_feed_tph",
    "cooling_air_nm3_kg",
    "grate_speed_m_min",
    "bed_depth_mm",
    "clinker_inlet_temp_c",
]

#: 输出维度顺序。
OUTPUT_DIMS: list[str] = [
    "clinker_outlet_temp_c",
    "cooling_efficiency_pct",
    "secondary_air_temp_c",
    "fan_power_kw",
]


def _normalize(value: float, min_val: float, max_val: float) -> float:
    """将值归一化到 [0, 1] 区间。

    Args:
        value: 原始值。
        min_val: 范围下界。
        max_val: 范围上界。

    Returns:
        float: 归一化值 [0, 1]。
    """
    span = max_val - min_val
    if span == 0:
        return 0.0
    return (value - min_val) / span


def compute_outputs(
    clinker_feed_tph: float,
    cooling_air_nm3_kg: float,
    grate_speed_m_min: float,
    bed_depth_mm: float,
    clinker_inlet_temp_c: float,
) -> dict[str, float]:
    """基于物理方程计算 4 个输出维度。

    使用归一化后的输入组合，确保输出落在合理物理范围内。

    Args:
        clinker_feed_tph: 熟料给料量（t/h）。
        cooling_air_nm3_kg: 冷却风量（Nm³/kg）。
        grate_speed_m_min: 篦床速度（m/min）。
        bed_depth_mm: 料层厚度（mm）。
        clinker_inlet_temp_c: 熟料入口温度（°C）。

    Returns:
        dict[str, float]: 4 个输出维度的值。
    """
    # 归一化输入
    feed = _normalize(clinker_feed_tph, 120.0, 300.0)
    air = _normalize(cooling_air_nm3_kg, 1.6, 3.2)
    speed = _normalize(grate_speed_m_min, 1.2, 4.0)
    depth = _normalize(bed_depth_mm, 450.0, 850.0)
    inlet = _normalize(clinker_inlet_temp_c, 1250.0, 1450.0)

    # clinker_outlet_temp_c: 入口温度越高→出口越高；冷却风/篦速越高→出口越低；
    #   给料/料层越厚→出口越高。基线 120°C，范围约 [100, 320]。
    outlet = (
        120.0
        + 180.0 * inlet
        - 70.0 * air
        - 45.0 * speed
        + 40.0 * feed
        + 30.0 * depth
    )

    # cooling_efficiency_pct: 冷却风/篦速越高→效率越高；给料越高→效率越低。
    #   范围约 [60, 95]。
    efficiency = (
        60.0
        + 30.0 * air
        + 20.0 * speed
        - 15.0 * feed
        + 5.0 * (1.0 - depth)
    )

    # secondary_air_temp_c: 入口温度越高→二次风越高；冷却风越高→二次风越低。
    #   范围约 [180, 950]。
    secondary_air = (
        180.0
        + 700.0 * inlet
        - 250.0 * air
        + 50.0 * depth
    )

    # fan_power_kw: 冷却风/料层越厚→功率越大。范围约 [55, 420]。
    fan_power = (
        55.0
        + 280.0 * air
        + 120.0 * depth
        + 40.0 * speed
    )

    return {
        "clinker_outlet_temp_c": round(outlet, 2),
        "cooling_efficiency_pct": round(efficiency, 2),
        "secondary_air_temp_c": round(secondary_air, 2),
        "fan_power_kw": round(fan_power, 2),
    }


def generate_dataset(
    seed: int = DEFAULT_SEED,
    row_count: int = ROW_COUNT,
) -> list[dict[str, float]]:
    """生成确定性数据集。

    使用 Latin-hypercube 风格的均匀采样 + 固定种子噪声，
    确保输入覆盖各维度范围且结果可复现。

    Args:
        seed: 随机种子。
        row_count: 数据行数。

    Returns:
        list[dict[str, float]]: 数据集行列表，每行含 5 输入 + 4 输出。
    """
    rng = random.Random(seed)
    rows: list[dict[str, float]] = []

    for i in range(row_count):
        # 分层采样：每行使用不同的子区间，确保覆盖均匀
        row_inputs: dict[str, float] = {}
        for dim in INPUT_DIMS:
            min_val, max_val = INPUT_RANGES[dim]
            # 使用分层 + 抖动：i/row_count 分层 + 随机抖动
            stride = 1.0 / row_count
            base = (i + 0.5) * stride
            jitter = rng.uniform(-0.4 * stride, 0.4 * stride)
            norm = max(0.0, min(1.0, base + jitter))
            value = min_val + norm * (max_val - min_val)
            # 加少量噪声进一步打散
            value += rng.uniform(-1.0, 1.0) * 0.01 * (max_val - min_val)
            value = max(min_val, min(max_val, value))
            row_inputs[dim] = round(value, 4)

        outputs = compute_outputs(**row_inputs)

        # 输出加入种子噪声（模拟测量误差）
        noisy_outputs: dict[str, float] = {}
        for out_dim, out_val in outputs.items():
            noise = rng.gauss(0.0, 0.01 * abs(out_val))
            noisy_outputs[out_dim] = round(out_val + noise, 2)

        row: dict[str, float] = {**row_inputs, **noisy_outputs}
        rows.append(row)

    return rows


def write_csv(rows: list[dict[str, float]], path: Path) -> None:
    """将数据集写入 CSV 文件。

    Args:
        rows: 数据集行列表。
        path: 输出 CSV 文件路径。
    """
    fieldnames = INPUT_DIMS + OUTPUT_DIMS
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_json(rows: list[dict[str, float]], path: Path) -> None:
    """将数据集写入 JSON 文件。

    Args:
        rows: 数据集行列表。
        path: 输出 JSON 文件路径。
    """
    payload: dict[str, Any] = {
        "seed": DEFAULT_SEED,
        "row_count": len(rows),
        "input_dimensions": INPUT_DIMS,
        "output_dimensions": OUTPUT_DIMS,
        "input_ranges": {
            k: {"min": v[0], "max": v[1]}
            for k, v in INPUT_RANGES.items()
        },
        "rows": rows,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def main() -> None:
    """命令行入口：生成数据集并写入 CSV + JSON。"""
    parser = argparse.ArgumentParser(
        description="生成篦冷机 ROM 确定性数据集"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("examples/grate-cooler-rom/data"),
        help="输出目录",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help=f"随机种子（默认 {DEFAULT_SEED}）",
    )
    parser.add_argument(
        "--rows",
        type=int,
        default=ROW_COUNT,
        help=f"数据行数（默认 {ROW_COUNT}）",
    )
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    rows = generate_dataset(seed=args.seed, row_count=args.rows)

    csv_path = args.output / "grate_cooler_dataset.csv"
    json_path = args.output / "grate_cooler_dataset.json"
    write_csv(rows, csv_path)
    write_json(rows, json_path)

    print(
        f"已生成 {len(rows)} 行数据集：\n"
        f"  CSV:  {csv_path}\n"
        f"  JSON: {json_path}\n"
        f"  种子: {args.seed}"
    )


if __name__ == "__main__":
    main()
