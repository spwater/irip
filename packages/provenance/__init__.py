"""L2.5 溯源与推导层包。

提供证据集（EvidenceSet）、推导配方（TransformationRecipe）、推导运行
（DerivationRun）与溯源图（ProvenanceGraph）的创建、冻结、执行与查询能力。

证据集冻结事实快照以保证可复现推导；配方定义转换算法及参数；
推导运行执行配方并产出参数候选；溯源图将推导结果连接回原始事实。

子模块：
- entities: ORM 模型（evidence_set / evidence_set_version /
  transformation_recipe / transformation_recipe_version / derivation_run /
  provenance_edge）；
- evidence: 证据集服务（创建、冻结、查询成员）；
- recipes: 推导配方服务（创建、发布版本、查询）；
- algorithms: 推导算法（RobustParameterEstimator）与执行器协议；
- derivations: 推导运行服务（创建运行、回放、查询）；
- graph: 溯源图服务（图遍历、路径查询、边管理）。
"""
