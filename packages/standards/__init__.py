"""标准管理包：单位转换、标准变量、不可变版本、别名。

本包是 V1 粒度 L1→L3 证据链的基础层，提供：
- units: 基于 Decimal 的单位转换器（仿射变换 + 维度检查）；
- variables: 标准变量实体 + 不可变版本 + 别名 ORM 模型；
- state_machine: 标准状态机（draft → in_review → published → deprecated）；
- repository: 数据访问层；
- service: 业务编排服务（创建 / 提交 / 发布 / 拒绝 / 弃用 / 重提）。
"""
