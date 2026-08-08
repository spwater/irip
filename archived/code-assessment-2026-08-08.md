# IRIP 代码评估报告

> 评估时间：2026-08-08 | 评估范围：`irip/` 全量代码库 | 版本：v0.8.0

---

## 一、项目概况

| 维度 | 数据 |
|------|------|
| 项目名称 | IRIP — 工业研究智能平台 |
| 版本 | v0.8.0 |
| Git 提交数 | 446 |
| 开发者 | 1 人 (spwater) |
| 后端代码 | ~137,390 行 Python (534 文件) |
| 前端代码 | ~44,102 行 TS/TSX (238 文件) |
| API 端点 | 270 个 (28 个路由文件) |
| 数据库迁移 | 82 个 (至 0082) |
| 测试函数 | 2,092 个 Python + 87 个 TS |

---

## 二、架构评估

### 2.1 整体架构 — ⭐⭐⭐⭐☆

**优点：**
- 清晰的 Monorepo 三层分离：`apps/{api,web,worker}` + `packages/{领域模块}`
- 17 个领域包各司其职：ai、auth、facts、research、models、provenance 等
- DI 组合层 (`apps/api/composition/`) 将依赖注入与路由解耦
- 数据库会话管理统一通过 `session_scope` + RLS GUC 实现
- 统一错误契约 `AppError` + `ErrorCode` 封闭枚举

**关注点：**
- `packages/research/models.py` 1255 行、`packages/ai/numeric/service.py` 999 行等大文件需拆分
- 前端 `DepartmentManagement.tsx` (744行)、`EvidencePanel.tsx` (704行) 偏大

### 2.2 技术栈选型 — ⭐⭐⭐⭐⭐

| 层面 | 选型 | 评价 |
|------|------|------|
| 后端框架 | FastAPI + Pydantic v2 + SQLAlchemy 2.0 (async) | 现代异步栈，匹配 I/O 密集场景 |
| 任务队列 | Celery + Redis 7 | 成熟稳定 |
| 数据库 | PostgreSQL 16 + pgvector | 向量搜索就绪 |
| 对象存储 | MinIO (S3 兼容) | 自托管，数据不出域 |
| 前端框架 | React 18 + TypeScript 5.7 | 主流选型 |
| 前端构建 | Vite 5 | 快速 HMR |
| UI 库 | Ant Design 5 | 企业级组件完备 |
| 状态管理 | Zustand + TanStack Query | 轻量 + 数据获取分离 |
| 可视化 | ECharts + Plotly + React Flow + AntV G6 | 多场景覆盖 |
| 测试框架 | pytest + Vitest + Playwright | 金字塔分层合理 |

---

## 三、后端代码质量

### 3.1 静态检查

| 检查项 | 结果 | 评价 |
|--------|------|------|
| Ruff Lint | 203 errors (195 行宽超限) | 99% 为 E501 行宽，实质性代码问题极少 |
| Mypy 类型检查 | **0 errors** | 优秀 — 从 311 降至 0 |
| `type: ignore` | 100 处 | 从 505 降至 100，改善 80% |
| TODO/FIXME/HACK | 0 处 | 代码标记干净 |
| 空文件 | 0 个 | 无废弃文件 |

### 3.2 代码模式

- **错误处理**：统一 `AppError` + `ErrorCode` 枚举，CI 有错误码穷尽性检查
- **日志记录**：244 处 structlog 调用，结构化日志到位
- **异常捕获**：少量 `except Exception` 裸捕获（主要在 health 检查和 preview 路由），可接受但建议收窄
- **SQL 安全**：使用 SQLAlchemy ORM 参数绑定；`execute(f"SET ...")` 仅用于 GUC 设置，无注入风险
- **凭据管理**：通过 `secret_id` 引用 + AES 加密存储，未发现硬编码密钥

### 3.3 大文件 Top 10（>750 行）

| 文件 | 行数 | 建议 |
|------|------|------|
| `packages/research/models.py` | 1255 | 拆分为多个子模型文件 |
| `packages/ai/numeric/service.py` | 999 | 拆分表达式评估/统计/数据解析 |
| `packages/research/entities.py` | 980 | 按实体域拆分 |
| `apps/api/routers/research_products.py` | 955 | 路由分组拆分 |
| `packages/research/execution/repository_trusted.py` | 925 | 拆分 CRUD/查询/特殊逻辑 |
| `packages/parameters/service.py` | 919 | 按业务操作拆分 |
| `apps/api/routers/flows.py` | 903 | 路由分组 |
| `packages/ai/ask_service.py` | 899 | 拆分流式/审计/引用 |
| `packages/components/registry/registry.py` | 853 | 拆分注册/查找/校验 |

---

## 四、前端代码质量

### 4.1 静态检查

| 检查项 | 结果 | 评价 |
|--------|------|------|
| TypeScript 编译 | **0 errors** | 类型安全通过 |
| ESLint | 48 errors + 16 warnings | 主要是 a11y 和未使用变量 |
| 构建产物 | dist/ 存在 (可构建) | 正常 |

### 4.2 ESLint 问题分类

| 类型 | 数量 | 严重性 |
|------|------|--------|
| `jsx-a11y/click-events-have-key-events` | ~10 | 中 — 无障碍键盘支持缺失 |
| `jsx-a11y/no-static-element-interactions` | ~8 | 中 — 非语义交互元素 |
| `@typescript-eslint/no-explicit-any` | ~5 | 低 — 类型可收窄 |
| `@typescript-eslint/no-unused-vars` | ~3 | 低 — 清理即可 |
| `react-hooks/exhaustive-deps` | ~16 (warnings) | 低 — 依赖数组告警 |

### 4.3 结构评价

- **Feature-based 目录**：23 个功能模块，职责清晰
- **API 层**：纯函数 + http 实例，有完整类型定义和端点注释
- **组件分层**：页面 → 功能组件 → 通用组件，层次分明
- **测试**：12 个测试文件 / 87 通过 / 27 跳过 — 覆盖核心流程

---

## 五、测试评估

### 5.1 Python 测试

| 类别 | 文件数 | 评价 |
|------|--------|------|
| Unit | 70 | 充足 |
| Integration | 37 | 良好 |
| Contract | 3 | 基本覆盖 |
| Security | 8 | 覆盖 SQL注入/SSRF/路径穿越/令牌重放 |
| Acceptance | 3 | V1 验收不变量 |
| Recovery | 7 | 容错/恢复测试 |
| Performance | 0 | **缺失** |
| E2E | 0 (TS 有 3 个 Playwright) | **不足** |

### 5.2 测试覆盖率

| 指标 | 数值 | 评价 |
|------|------|------|
| 总覆盖率 | **39%** | 偏低，需提升至 60%+ |
| >80% 覆盖率模块 | 99 个 | 核心模块覆盖好 |
| <30% 覆盖率模块 | 0 个 | 无完全未测试模块 |
| 0% 覆盖率模块 | 0 个 | 无遗漏 |
| 低覆盖热点 | research 域 (9-47%) | 新功能测试欠债 |

### 5.3 前端测试

- 12 个测试文件，87 个测试通过，27 个跳过
- 覆盖：登录、助手对话、研究基础/可信执行、组件流程
- 缺口：大部分功能模块（equipment、governance、dashboard 等）无前端测试

---

## 六、安全性评估

### 6.1 安全措施清单

| 措施 | 状态 | 说明 |
|------|------|------|
| Docker 安全基线 | ✅ | cap_drop ALL + no-new-privileges + read-only FS |
| CI Actions SHA 锁定 | ✅ | 所有 GitHub Actions pin 到 commit SHA |
| JWT 认证 + Token 版本 | ✅ | 修改密码使旧 token 失效 (H-06) |
| RLS 行级安全 | ✅ | PostgreSQL GUC + dept_scope |
| 凭据加密存储 | ✅ | AES 加密 + secret_id 引用 |
| CSP 安全头 | ✅ | 已配置 |
| SSRF 防护 | ✅ | 内网地址校验（可配置白名单） |
| 参数化查询 | ✅ | SQLAlchemy ORM 绑定 |
| 密码哈希 | ✅ | Argon2 |
| 安全测试 | ✅ | 8 个专用安全测试文件 |
| SBOM 生成 | ✅ | CI 中集成 |

### 6.2 安全风险

| 风险 | 级别 | 说明 |
|------|------|------|
| `.env` 含真实凭据 | 低 | .gitignore 已排除，但本机存在 |
| 裸 `except Exception` | 低 | 10 处，可能吞没错误细节 |
| Docker 沙箱 exec | 中 | `sandbox.py` 通过 Docker exec 执行代码，需确保隔离 |
| 无速率限制审计 | 低 | rate_limiter.py 存在，需验证覆盖范围 |

---

## 七、CI/CD 评估

### 7.1 流水线

```
push/PR → [lint(ruff)] + [error-code-check] + [mypy] + [pytest unit/integration/contract/acceptance] + [web test/build] + [security] + [recovery] + [SBOM]
```

**优点：**
- 多矩阵测试 (Python 3.12 + 3.13)
- 错误码穷尽性自动检查（自定义脚本）
- Actions 全部 pin SHA，防 supply-chain 攻击
- SBOM 生成
- 测试分类隔离，unit 不依赖外部服务

**不足：**
- 无自动化部署步骤（仅 CI，无 CD）
- 无 E2E 测试集成（Playwright 测试在本地但未在 CI 运行）
- 无性能基准测试

---

## 八、综合评分

| 维度 | 评分 | 说明 |
|------|------|------|
| 架构设计 | ⭐⭐⭐⭐☆ | 分层清晰，领域建模到位，部分大文件需拆分 |
| 后端质量 | ⭐⭐⭐⭐☆ | Mypy 0 错误优秀，Ruff 仅行宽问题，type:ignore 持续下降 |
| 前端质量 | ⭐⭐⭐☆☆ | TS 编译通过，但 ESLint a11y 问题多，前端测试覆盖低 |
| 测试覆盖 | ⭐⭐⭐☆☆ | 2092 测试量充足，但覆盖率 39% 偏低，缺性能/E2E |
| 安全性 | ⭐⭐⭐⭐⭐ | 多层防护，Docker 基线 + RLS + 凭据加密 + CI SHA 锁定 |
| CI/CD | ⭐⭐⭐⭐☆ | CI 完善且有创新（错误码穷尽性检查），缺 CD |
| 文档 | ⭐⭐⭐⭐☆ | 有架构文档/ADR/PRD/运维手册，代码内注释充分 |
| 技术债管理 | ⭐⭐⭐⭐☆ | 有明确治理轨迹 (type:ignore 505→100，大文件拆分进行中) |

**总评：⭐⭐⭐⭐☆ (4.0/5.0)** — 工程质量在单人项目中属优秀水平，架构成熟度高，安全意识强，主要短板在测试覆盖率和前端质量。

---

## 九、优先改进建议

### P0 — 应尽快处理
1. **提升测试覆盖率至 60%+** — 重点补 research 域 (当前 9-47%) 和 standards 域 (0-14%)
2. **修复 ESLint a11y 错误** — 48 个 a11y 错误影响无障碍合规

### P1 — 近期规划
3. **拆分大文件** — 9 个 >750 行的后端文件 + 6 个 >500 行的前端文件
4. **集成 Playwright E2E 到 CI** — 本地已有 3 个 E2E 测试但未纳入 CI
5. **补 performance 测试** — tests/performance 目录为空

### P2 — 持续改进
6. **收窄 `except Exception`** — 10 处裸捕获改为具体异常类型
7. **清理 Ruff E501** — 195 行行宽超限，统一格式化
8. **前端补测试** — governance/dashboard/equipment 等模块无测试
9. **补充 CD 流水线** — 当前仅 CI，缺自动化部署
10. **Rate Limiter 覆盖审计** — 验证所有公开端点是否有限流

---

## 十、亮点

- **Mypy 0 错误**：从 311 降至 0，316 个源文件全部类型安全
- **type:ignore 80% 削减**：505 → 100，类型安全持续改善
- **错误码穷尽性 CI 检查**：自定义脚本确保所有 AppError code 都在 ErrorCode 枚举中注册 — 创新实践
- **Docker 安全基线**：cap_drop ALL + no-new-privileges + read-only FS — 超出多数项目水平
- **CI Actions SHA 锁定**：防 supply-chain 攻击 — 安全意识到位
- **统一错误契约**：AppError + ErrorCode 枚举 + CI 穷尽性检查 — 工程化程度高
- **多租户 RLS**：PostgreSQL GUC + dept_scope 实现行级安全 — 数据隔离到位
- **测试金字塔**：unit/integration/contract/security/recovery/acceptance 六层分层 — 结构完整
