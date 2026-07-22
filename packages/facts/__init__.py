"""L2 事实层包。

提供事实（Fact）的创建、修订、观察值管理、工件链接与全文搜索。
事实是 IRIP 平台 L1→L3 证据链的中心业务对象，记录实验/仿真/文档/模型
执行中发生了什么，包含不可变修订历史、原始与标准化观察值、工件链接。

子模块：
- entities: ORM 模型（fact / fact_revision / raw_observation /
  normalized_observation / fact_artifact / fact_revision_link）；
- observations: 观察值输入/输出值对象；
- repository: 数据访问层（FactRepository）；
- service: 事实业务编排服务（FactService）。
"""
