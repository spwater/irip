"""L2 事实层包。

提供事实（Fact）的创建、查询、全文搜索与列表。
事实是 IRIP 平台 L1→L3 证据链的中心业务对象，记录实验/仿真/文档/模型
执行中发生了什么，包含主体标识、任务快照、时间范围与关联工件。

子模块：
- entities: ORM 模型（fact / fact_data_index）；
- observations: 事实引用值对象（FactRef）；
- repository: 数据访问层（FactRepository）；
- service: 事实业务编排服务（FactService）。
"""
