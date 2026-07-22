#!/usr/bin/env python3
"""篦冷机 ROM 模型训练脚本（IRIP V2-T04）。

训练 RandomForestRegressor 多输出回归模型：
- StandardScaler + RandomForestRegressor（固定种子，n_jobs=1）；
- 80/20 训练/测试分割；
- 序列化模型（joblib）+ 契约 + 指标 + SHA-256。

输出产物：
- model.pkl: 序列化的 sklearn Pipeline（StandardScaler + RandomForest）；
- contract.json: 模型输入/输出契约（含适用域）；
- metrics.json: 验证指标（各输出维度的 R²、RMSE）；
- model_sha256.txt: 模型工件 SHA-256 摘要。

用法：
    python examples/grate-cooler-rom/train.py --data <dir> --output <dir>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

#: 固定随机种子。
RANDOM_SEED: int = 20260715

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

#: RandomForest 超参数。
N_ESTIMATORS: int = 50
MAX_DEPTH: int = 12


def load_dataset(data_dir: Path) -> tuple[np.ndarray, np.ndarray]:
    """加载数据集为特征矩阵 X 与目标矩阵 Y。

    优先读取 JSON 数据集，若不存在则调用 generate.py 生成。

    Args:
        data_dir: 数据目录。

    Returns:
        tuple[np.ndarray, np.ndarray]: (X, Y) 特征矩阵与目标矩阵。
    """
    json_path = data_dir / "grate_cooler_dataset.json"
    rows: list[dict[str, float]] = []
    if json_path.exists():
        with open(json_path, encoding="utf-8") as f:
            payload = json.load(f)
        rows = payload.get("rows", [])
    if not rows:
        # 数据不存在，调用 generate.py 生成
        import subprocess
        import sys

        data_dir.mkdir(parents=True, exist_ok=True)
        generate_script = Path(__file__).parent / "generate.py"
        subprocess.run(
            [
                sys.executable,
                str(generate_script),
                "--output",
                str(data_dir),
                "--seed",
                str(RANDOM_SEED),
                "--rows",
                "240",
            ],
            check=True,
        )
        with open(json_path, encoding="utf-8") as f:
            payload = json.load(f)
        rows = payload.get("rows", [])

    x_list: list[list[float]] = []
    y_list: list[list[float]] = []
    for row in rows:
        x_list.append([float(row[dim]) for dim in INPUT_DIMS])
        y_list.append([float(row[dim]) for dim in OUTPUT_DIMS])

    return np.array(x_list, dtype=np.float64), np.array(
        y_list, dtype=np.float64
    )


def train_model(
    x: np.ndarray,
    y: np.ndarray,
) -> tuple[Any, dict[str, Any]]:
    """训练 StandardScaler + RandomForestRegressor 多输出模型。

    80/20 分割，返回训练好的 Pipeline 与验证指标。

    Args:
        x: 特征矩阵 (n_samples, n_features)。
        y: 目标矩阵 (n_samples, n_outputs)。

    Returns:
        tuple[Any, dict[str, Any]]: (pipeline, metrics)。
    """
    from sklearn.ensemble import RandomForestRegressor
    from sklearn.metrics import mean_squared_error, r2_score
    from sklearn.model_selection import train_test_split
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    # 80/20 分割（固定种子）
    x_train, x_test, y_train, y_test = train_test_split(
        x,
        y,
        test_size=0.2,
        random_state=RANDOM_SEED,
    )

    pipeline = Pipeline(
        steps=[
            ("scaler", StandardScaler()),
            (
                "rf",
                RandomForestRegressor(
                    n_estimators=N_ESTIMATORS,
                    max_depth=MAX_DEPTH,
                    random_state=RANDOM_SEED,
                    n_jobs=1,
                ),
            ),
        ]
    )
    pipeline.fit(x_train, y_train)

    # 验证指标
    y_pred = pipeline.predict(x_test)
    metrics: dict[str, Any] = {"per_output": {}}
    for i, dim in enumerate(OUTPUT_DIMS):
        y_true_i = y_test[:, i]
        y_pred_i = y_pred[:, i]
        r2 = float(r2_score(y_true_i, y_pred_i))
        rmse = float(
            math.sqrt(mean_squared_error(y_true_i, y_pred_i))
        )
        metrics["per_output"][dim] = {
            "r2": round(r2, 4),
            "rmse": round(rmse, 4),
        }

    # 综合指标
    r2_overall = float(r2_score(y_test, y_pred, multioutput="uniform_average"))
    rmse_overall = float(
        math.sqrt(mean_squared_error(y_test, y_pred))
    )
    metrics["overall"] = {
        "r2": round(r2_overall, 4),
        "rmse": round(rmse_overall, 4),
    }
    metrics["train_samples"] = int(len(x_train))
    metrics["test_samples"] = int(len(x_test))
    metrics["random_seed"] = RANDOM_SEED

    return pipeline, metrics


def serialize_artifacts(
    pipeline: Any,
    metrics: dict[str, Any],
    contract_path: Path,
    output_dir: Path,
) -> dict[str, str]:
    """序列化模型工件、契约、指标并计算 SHA-256。

    Args:
        pipeline: 训练好的 sklearn Pipeline。
        metrics: 验证指标。
        contract_path: 契约 JSON 文件路径。
        output_dir: 输出目录。

    Returns:
        dict[str, str]: 各产物路径与 SHA-256 摘要。
    """
    import joblib

    output_dir.mkdir(parents=True, exist_ok=True)

    # 序列化模型
    model_path = output_dir / "model.pkl"
    joblib.dump(pipeline, model_path)
    model_bytes = model_path.read_bytes()
    model_sha256 = hashlib.sha256(model_bytes).hexdigest()

    # 写入契约（从 contract.json 复制，附加模型哈希）
    with open(contract_path, encoding="utf-8") as f:
        contract = json.load(f)
    contract["model_sha256"] = model_sha256
    contract_out_path = output_dir / "contract.json"
    with open(contract_out_path, "w", encoding="utf-8") as f:
        json.dump(contract, f, ensure_ascii=False, indent=2)

    # 写入指标
    metrics_path = output_dir / "metrics.json"
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)

    # 写入 SHA-256 摘要
    sha_path = output_dir / "model_sha256.txt"
    sha_path.write_text(model_sha256, encoding="utf-8")

    return {
        "model_path": str(model_path),
        "contract_path": str(contract_out_path),
        "metrics_path": str(metrics_path),
        "model_sha256": model_sha256,
    }


def main() -> None:
    """命令行入口：训练模型并序列化产物。"""
    parser = argparse.ArgumentParser(
        description="训练篦冷机 ROM 模型"
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=Path("examples/grate-cooler-rom/data"),
        help="数据目录",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("examples/grate-cooler-rom/model"),
        help="输出目录",
    )
    parser.add_argument(
        "--contract",
        type=Path,
        default=Path("examples/grate-cooler-rom/contract.json"),
        help="契约 JSON 文件路径",
    )
    args = parser.parse_args()

    print("加载数据集...")
    x, y = load_dataset(args.data)
    print(f"  样本数: {len(x)}, 输入维度: {x.shape[1]}, 输出维度: {y.shape[1]}")

    print("训练模型（StandardScaler + RandomForestRegressor）...")
    pipeline, metrics = train_model(x, y)
    print(
        f"  整体 R²: {metrics['overall']['r2']}, "
        f"RMSE: {metrics['overall']['rmse']}"
    )

    print("序列化产物...")
    paths = serialize_artifacts(
        pipeline, metrics, args.contract, args.output
    )
    print(f"  模型: {paths['model_path']}")
    print(f"  契约: {paths['contract_path']}")
    print(f"  指标: {paths['metrics_path']}")
    print(f"  SHA-256: {paths['model_sha256']}")


if __name__ == "__main__":
    main()
