"""L3 参数层。

参数候选来自推导运行，经条件评估、审批（职责分离）后发布为不可变版本。

模块：
- entities: ORM 模型（parameter, parameter_version, parameter_candidate）；
- conditions: 条件 AST 解析与求值引擎（白名单字段，安全求值）；
- service: ParameterService 业务编排（创建候选、审批、发布、弃用）。
"""
