"""篦冷机 ROM（Reduced Order Model）示例（IRIP V2-T04）。

基于物理方程生成确定性数据集，训练 RandomForest 多输出回归模型，
演示 IRIP 模型生命周期的完整流程：数据生成 → 训练 → 序列化 → 契约。

子模块：
- generate: 生成 240 行确定性数据集（5 输入 × 4 输出）；
- train: 训练 RandomForestRegressor 多输出模型；
- contract: 模型输入/输出契约。
"""
