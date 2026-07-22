"""IRIP 模型生命周期包（V2-T04）。

提供模型契约、适配器、ORM 实体、生命周期服务与适用域检查，
支撑工业模型从训练→验证→发布→预测→回滚→废弃的完整生命周期管理。

子模块：
- contracts: 模型契约与适配器协议等不可变值对象；
- adapters: 模型适配器实现（命令行 CLI 适配器、Python 适配器）；
- entities: ORM 实体（model / model_version）；
- service: 模型生命周期业务编排服务（ModelService）；
- applicability: 适用域检查器（ApplicabilityChecker）。
"""
