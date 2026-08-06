"""研究域适配器包。

阶段 5 新增：
- CoreProvenanceAdapter: 只读核心 Provenance 适配器（查询 Fact / DerivationRun / EvidenceSet 节点）
- ResearchLineageAdapter: 只读研究 Lineage 适配器（查询研究域节点 + research_lineage_edge 入边）

两个适配器均为 Protocol 接口，实现类通过 DI 注入。
"""
