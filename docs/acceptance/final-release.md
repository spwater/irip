# IRIP 最终验收文档

> 版本: 0.8.0
> 日期: 2026-08-09

## V0 功能清单

- [x] 项目脚手架 + Docker 编排
- [x] PostgreSQL + Redis + MinIO 基础设施
- [x] 用户认证 + JWT
- [x] 部门多租户 + RLS

## V1 功能清单

- [x] 标准层（ObjectType / RelationType / ObjectGraph）
- [x] 事实层（Fact + Revision + 不可变性）
- [x] 证据集冻结
- [x] 组件注册 + 流程引擎
- [x] 参数审批 + 版本发布
- [x] 数据摄入（Converter 插件）
- [x] AI 助手 + 引用溯源

## V2 功能清单

- [x] 模型生命周期管理（训练/验证/发布/回滚）
- [x] 设备管理
- [x] 实验项目管理
- [x] 治理控制台
- [x] 仪表盘

## V3 功能清单

- [x] 研究分析模块（Workspace / 计划 / 执行 / 产物 / 发布 / 溯源）
- [x] AI 数值计算工具（evaluate_expression / describe_series）
- [x] 纯 LLM 三步分析链路（生成计划 → 执行分析 → 提取结论）
- [x] 研究成果发布与复用（权限包络 / ACL / 搜索）
- [x] 统一溯源图（AntV G6 可视化）
- [x] 知识库引用

## 已知限制

1. 沙箱执行链路已搁置，当前使用纯 LLM 三步分析替代
2. 后端覆盖率 46%，路由层尚未覆盖
3. CD 管道配置就绪但无目标服务器
4. k6 性能测试脚本就绪但未实际执行
5. 单人项目，bus factor = 1
