# V1 验收报告：粒度实验 L1→L3 证据链

## 验收日期
2026-07-21

## 验收范围

V1 阶段（Task 10-20）实现了 IRIP 工业科研智能平台的完整证据链：
- **L1 标准层**：变量、单位转换、状态机、不可变版本（Task 10-12）
- **L2 事实层**：事实、不可变修订、观察值、工件链接、质量引擎（Task 15-16）
- **L2.5 溯源层**：证据集、配方、推导运行、回放、溯源图（Task 17）
- **L3 参数层**：条件引擎、审批分离、不可变发布、过期检测（Task 18）
- **前端 UI**：标准/对象/摄入/事实/溯源/参数 8 个页面（Task 19）

## 验收门（V1 Reviewer Gate）

> 审查者能从任何 D10/D50/D90 值导航通过：
> 参数版本 → 推导运行 → 配方 → 证据成员 → 精确事实修订 → 原始字段 → 原始工件

### 验收路径

1. **标准管理** (`/standards`)
   - 查看 `particle.d10`、`particle.d50`、`particle.d90` 变量
   - 状态标签：已发布（绿色）
   - 版本号显示

2. **数据摄入** (`/ingestions`)
   - 上传 XLSX 文件 → 数据预览 → 字段映射评分 → 逐一确认映射 → 质量校验 → 确认导入
   - 映射确认门控：未确认所有映射时"确认并导入"按钮禁用

3. **实验事实** (`/facts`)
   - 事实列表：搜索、状态筛选、类型筛选
   - 事实详情：观察值原始↔标准化对照、质量评估、修订历史、原始工件链接

4. **溯源链路** (`/provenance`)
   - 证据集：创建、冻结
   - 配方：创建、发布
   - 推导运行：创建、回放
   - 溯源图：BFS 从推导运行 → 事实修订 → 观察值

5. **参数管理** (`/parameters`)
   - 参数列表：代码、状态、版本、证据数、过期状态
   - 候选审批面板：
     - 显示版本标签、值、置信区间、证据数、质量等级、状态、条件、提交者
     - "查看完整来源"链接跳转溯源图
     - **self_approval_forbidden**：提交者不显示"批准发布"和"驳回"按钮

## 测试覆盖

### 单元 + 集成测试
```bash
python -m pytest tests/unit tests/integration -v
```
- 286+ 测试通过（含 Task 10-18 全部模块）

### 前端测试
```bash
pnpm --dir apps/web test -- --run
```
- 7 测试通过（4 文件：LoginPage、JobDrawer、IngestionWizard、ApprovalPanel）

### 前端构建
```bash
pnpm --dir apps/web build
```
- TypeScript 0 错误
- Vite 构建成功（1707 模块）

### 恢复测试
```bash
python -m pytest tests/recovery/test_ingestion_worker_restart.py -v
```
- Worker 重启后正确完成摄入
- 首次尝试日志保留

### 验收测试
```bash
python -m pytest tests/acceptance/test_v1_invariants.py -v
```
- 每个已发布参数有完整原始路径
- 事实修订不可变
- 提交者不能审批自己的候选
- 事实有质量评估

### E2E 测试
```bash
pnpm --dir apps/web e2e tests/e2e/particle-size.spec.ts tests/e2e/parameter-provenance.spec.ts
```
- 粒度实验黄金路径：上传→映射→导入→推导→审批→溯源
- 参数溯源图完整性验证
- 提交者不能审批自己

## 核心不变量

| # | 不变量 | 验证方式 |
|---|--------|---------|
| 1 | 每个已发布参数有完整原始路径 | acceptance test |
| 2 | 事实修订不可变（旧 revision 不修改） | acceptance test |
| 3 | 提交者不能审批自己的候选 | unit + e2e test |
| 4 | 溯源图完整（参数→推导→事实→工件） | acceptance test |
| 5 | 每个事实有质量评估 | acceptance test |
| 6 | 幂等摄入（重复文件返回同一事实） | unit test |
| 7 | 映射确认门控（未确认不能导入） | unit test |
| 8 | 推导确定性（相同输入→相同输出摘要） | unit test |
| 9 | 过期检测（事实新修订→参数标记 review_required） | unit test |
| 10 | 单位仿射变换精度（Decimal） | unit test |

## 已知技术债（非阻塞）

1. E2E 测试需要后端 seed 数据和运行中的前端/后端服务
2. 粒度夹具生成器需手动运行 `python -m examples.particle-size.generate`
3. 前端 bundle 较大（1.4MB），后续可 code-split 优化
4. 路由前缀用短复数名词符合代码库约定
