"""L3 参数层（IRIP Task 18）。

参数候选来自推导运行，经条件评估、审批（职责分离）后发布为不可变版本。
当底层事实发生修订时，依赖参数被标记为 review_required。

模块：
- entities: ORM 模型（parameter, parameter_version, parameter_candidate,
  parameter_staleness）；
- conditions: 条件 AST 解析与求值引擎（白名单字段，安全求值）；
- service: ParameterService 业务编排（创建候选、审批、发布、弃用）；
- staleness: StalenessChecker 过期状态检查器（事实修订 → 参数依赖）。
"""
