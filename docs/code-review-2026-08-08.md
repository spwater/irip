# IRIP 代码审查报告

**审查日期**: 2026-08-08  
**审查范围**: 全栈（后端 Python/FastAPI + 前端 React/TypeScript + Celery Worker + 测试/CI）  
**审查版本**: v0.8.0  
**审查人**: Code Review Expert (火眼眼)

---

## 总体评价

IRIP 是一个架构成熟度较高的工业科研智能平台。项目在安全设计、多租户隔离、异步作业一致性方面展现了优秀工程实践。DDD 分层架构（apps 应用层 + packages 领域层）组织清晰，测试分层完善（unit/integration/contract/security/recovery/acceptance 六层）。

然而在代码质量的一致性上存在参差不齐的问题——核心安全路径设计精良，但部分业务模块存在路径不匹配、竞态条件、类型断裂等问题，需要系统性修复。

### 评分概览

| 维度 | 评分 | 说明 |
|------|------|------|
| 架构设计 | ★★★★☆ | DDD 分层清晰，Outbox 模式 + RLS 多租户设计优秀 |
| 安全防护 | ★★★★☆ | 认证/授权/RLS/沙箱/SSRF 防护完善，个别风险点需修复 |
| 后端代码 | ★★★☆☆ | 核心路径优秀，备份/上传/助手模块有明显 bug |
| 前端代码 | ★★★☆☆ | API 客户端精巧，路由无懒加载、Research 模块状态管理过重 |
| 测试质量 | ★★★★☆ | 六层测试 + 安全/恢复测试，覆盖率门禁偏低 |
| CI/CD | ★★★★☆ | Actions SHA 锁定 + SBOM + 错误码穷尽性检查 |

---

## 🔴 Blockers（必须修复）

### B-1: 文件上传 S3 路径不匹配 — 导致 complete_upload 永远找不到文件

**文件**: `apps/api/routers/uploads.py`  
**行号**: 第 149 行 vs 第 195 行

`presign_upload` 生成的 S3 object_key 为 `uploads/{user_prefix}/{artifact_id}`，其中 `user_prefix = str(current_user.user_id)[:8]`。但 `complete_upload` 传入的 `temp_key` 为 `uploads/{artifact_id}`，**缺少 `user_prefix` 段**。

**影响**: 上传预签名成功后，完成上传时 S3 找不到对象，上传流程断裂。这是一个功能性 bug，文件上传完成端点可能完全无法工作。

**建议**:
```python
# complete_upload 中应使用相同的 key 构造逻辑
user_prefix = str(current_user.user_id)[:8]
temp_key = f"uploads/{user_prefix}/{artifact_id}"
```

---

### B-2: 乐观锁更新未检查 rowcount — 取消作业可能静默失败

**文件**: `packages/jobs/service.py`  
**行号**: 第 193-204 行

`request_cancel` 使用乐观锁 `WHERE Job.lock_version == job.lock_version` 执行 UPDATE，但**未检查 `result.rowcount`**。如果 `lock_version` 在读取和更新之间被另一个请求修改，UPDATE 影响 0 行，但代码继续 enqueue outbox 事件并返回成功。

**影响**: 并发场景下作业取消可能"成功"但实际未变更状态，outbox 事件声称已取消，客户端收到成功响应，但作业继续执行。

**建议**:
```python
result = await session.execute(stmt)
if result.rowcount == 0:
    raise AppError(code="conflict", message="作业状态已被其他请求修改，请重试")
```

---

### B-3: SSE 错误信息泄露 — 内部异常详情直接发送给客户端

**文件**: `apps/api/routers/assistant.py`  
**行号**: 第 579-583 行

```python
except Exception as exc:
    yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"
```

`str(exc)` 直接发送给客户端，可能包含数据库连接字符串、文件路径、SQL 语句等敏感信息。

**建议**: 使用脱敏的通用错误消息，服务端记录完整异常。
```python
except Exception:
    logger.exception("SSE stream error")
    yield f"event: error\ndata: {json.dumps({'error': '处理请求时发生内部错误'})}\n\n"
```

---

### B-4: KaTeX 渲染失败时的 XSS 漏洞

**文件**: `apps/web/src/features/assistant/ShowcaseCard.tsx`  
**行号**: 第 45 行

```typescript
catch {
    return `<span style="color:red">${tex}</span>`;
}
```

KaTeX `renderToString` 抛异常时，原始 `tex` 字符串被直接插入 HTML。如果 `tex` 来自 AI 输出且包含 `<script>` 等恶意标签，存在 XSS 漏洞。`BlockifiedMarkdown.tsx` 中也有同样的模式。

**建议**: catch 分支中对 `tex` 进行 HTML 转义。
```typescript
catch {
    const escaped = tex.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    return `<span style="color:red">${escaped}</span>`;
}
```

---

## 🟡 Suggestions（应当修复）

### S-1: 部门 ID 回退为随机 UUID — 创建孤儿数据

**文件**: `apps/api/routers/assistant.py` 第 85/96 行, `packages/ai/ask_service.py` 第 153-154 行

```python
org_id = getattr(user, "department_id", None) or new_id()
```

当用户无部门或 DB 查询失败（异常被 `except Exception: pass` 静默吞没）时，生成随机 UUID 作为部门 ID。AI 对话将关联到不存在的部门，造成数据孤儿，且绕过 RLS 隔离。

**建议**: 无部门时应拒绝请求或使用明确的系统部门 ID，不应生成随机 UUID。

---

### S-2: 备份幂等键使用随机 UUID — 幂等保护形同虚设

**文件**: `apps/api/routers/backups.py`  
**行号**: 第 328 行, 第 589 行

```python
job_id = new_id()  # 每次都是新 UUID
idempotency_key=f"backup:{job_id}"  # 永远唯一，幂等检查永远不命中
```

`JobService.accept` 的幂等检查依赖 `idempotency_key` 匹配，但 key 使用随机 UUID 构造，永远不重复，幂等性保护完全失效。

**建议**: 使用确定性幂等键，如 `f"backup:{backup_id}:{timestamp}"` 或用户提供的唯一标识。

---

### S-3: 备份删除顺序不当 — 先删文件后删 DB

**文件**: `apps/api/routers/backups.py`  
**行号**: 第 662-668 行

先执行 `shutil.rmtree(backup_dir)` 删除文件，后删数据库记录。如果 DB 删除失败，文件已丢失但记录残留，造成不一致状态。

**建议**: 先在事务内删除 DB 记录，确认后再删文件；或使用补偿模式记录待清理文件。

---

### S-4: 同步文件操作在 async 端点中

**文件**: `apps/api/routers/backups.py` 第 665 行, `apps/api/main.py` 第 128 行

`shutil.rmtree` 和 `s3_repo.ensure_bucket()` 是同步阻塞调用，在 async 上下文中可能阻塞事件循环。

**建议**: 使用 `asyncio.to_thread()` 包装同步调用。

---

### S-5: CORS 配置过度宽松

**文件**: `apps/api/main.py`  
**行号**: 第 180-188 行

```python
allow_methods=["*"],
allow_headers=["*"],
allow_credentials=True,
```

`allow_methods=["*"]` + `allow_credentials=True` 允许所有 HTTP 方法携带凭据跨域，扩大攻击面。

**建议**: 显式列出所需方法和头：
```python
allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
```

---

### S-6: 前端路由无懒加载 — 影响首屏性能

**文件**: `apps/web/src/app/router.tsx`  
**行号**: 第 11-19 行

所有页面组件静态导入，全部打包到初始 bundle。对于 18 个功能模块的项目，严重影响首屏加载性能。

**建议**: 使用 `React.lazy()` + `Suspense` 或 TanStack Router 内置代码分割。

---

### S-7: ResearchCanvas 异步 fetch 无 cancelled 标志 — 竞态导致数据显示错误

**文件**: `apps/web/src/features/research/ResearchCanvas.tsx`  
**行号**: 第 77-124 行

```typescript
useEffect(() => {
    (async () => {
        // ... 多个 await 调用 ...
        setPlan(planDetail);  // workspaceId 变化后旧数据可能覆盖新数据
        setRun(progress);
    })();
}, [workspaceId]);  // 无 cleanup
```

用户快速切换 workspace 时，第一个 workspace 的异步链的 `setPlan`/`setRun` 可能覆盖第二个 workspace 的数据。

**建议**: 添加 `cancelled` 标志：
```typescript
useEffect(() => {
    let cancelled = false;
    (async () => {
        const data = await fetchData(workspaceId);
        if (!cancelled) setPlan(data);
    })();
    return () => { cancelled = true; };
}, [workspaceId]);
```

---

### S-8: seed_users.py 硬编码密码

**文件**: `scripts/seed_users.py`  
**行号**: 第 32-52 行

19 个用户的密码以明文 "asdf1234" 硬编码在脚本中，包括管理员账户。

**建议**: 从环境变量读取，或在 seed 时生成随机密码并输出到安全渠道。

---

### S-9: 兜底错误响应结构不一致

**文件**: `apps/api/main.py`  
**行号**: 第 324-340 行

兜底异常处理器返回 `{"error": {"code": "internal_error", "message": "..."}}`，缺少 `retryable` 和 `fields` 键，而 `AppError.to_dict()` 始终返回四个键。客户端需要处理两种不同的响应结构。

**建议**: 统一响应结构，兜底处理器也返回四个键。

---

### S-10: `system_context` 允许 1MB 文本 — DoS 向量

**文件**: `apps/api/routers/assistant.py`  
**行号**: 第 119 行

```python
system_context: str | None = Field(default=None, max_length=1000000)
```

1MB 的文本字段在 JSON body 中可能导致内存压力。`mentions` 列表也无 `max_length` 约束。

**建议**: 降低 `max_length` 上限（如 100KB），对列表添加 `max_items` 约束。

---

### S-11: departments/repository.py 循环内递归 SQL — N+1 查询

**文件**: `packages/departments/repository.py`  
**行号**: 第 197-203 行

```python
for row in rows:
    dept = row[0]
    count_result = await session.execute(recursive_sql, {"dept_id": dept.id})  # N+1!
```

每个部门触发一次递归 SQL 查询，N 个部门 = N+1 次查询。

**建议**: 使用单次批量递归 CTE 查询所有部门的统计信息。

---

### S-12: `insight_candidate` 类型链断裂

**文件**: `apps/web/src/api/research.ts` 第 455 行 → `WorkspaceDetail.tsx` 第 29 行 → `ResearchCanvas.tsx` 第 42 行 → `ResearchShowcasePanel.tsx` 第 30 行

整条类型链使用 `any`，类型安全完全丧失。

**建议**: 定义 `InsightCandidate` 接口并贯穿使用。

---

## 💭 Nits（可优化）

### N-1: toggle_pin/archive 低效重查 + TOCTOU
**文件**: `assistant.py` 第 346-414 行 — 更新后查询全部对话再遍历查找，O(n) 且有竞态。应直接按 conversation_id 查询单条。

### N-2: ask_service.py 冗余 DB 查询
**文件**: `ask_service.py` 第 191-200 行 — 同一 AIConversation 被查询两次，第二次完全冗余。

### N-3: error_codes.py `from_string` O(n) 性能
**文件**: `error_codes.py` 第 146-161 行 — ~100+ 枚举成员线性遍历，高频错误场景有性能影响。建议类级 dict 缓存。

### N-4: uploads.py 输入验证不足
- `size_bytes` 无 `ge=0` 约束，负数可通过验证
- `sha256` 无格式校验（应为 64 位十六进制）
- `filename` 无长度约束

### N-5: user_departments.py 逐条 INSERT
**文件**: `packages/departments/user_departments.py` 第 121-130 行 — 循环内逐条 INSERT，应批量操作。

### N-6: tool_seeding.py 逐个检查存在性
**文件**: `packages/ai/tool_seeding.py` 第 36-43 行 — 每个工具一次 SELECT + 可能的 INSERT，应一次批量查询。

### N-7: 覆盖率门禁偏低
- Unit: 25%, Integration: 15%, 全局: 30% — 对于 264 个源文件的项目偏低

### N-8: Makefile 缺少部分 CI 目标
`test-security`、`test-recovery`、`test-contract`、`test-acceptance` 未在 Makefile 中定义，本地无法直接运行。

### N-9: EvidencePanel 15 个 useState 过度复杂
**文件**: `apps/web/src/features/research/EvidencePanel.tsx` 第 70-89 行 — 状态管理过于复杂，且未使用 React Query。应拆分组件或引入 React Query。

### N-10: useStreamingAnswer.ts setTimeout 未清理
**文件**: `useStreamingAnswer.ts` 第 210 行 — 100ms setTimeout 在组件卸载后仍可能执行，导致对已卸载组件的状态更新。

### N-11: 状态字段缺少数据库级 CHECK 约束
ORM 层状态字段均为 `sa.Text` + 业务层 `StrEnum` 校验，数据库无 CHECK 约束。如果绕过应用层直接操作数据库，可能写入非法状态。

### N-12: console.error 集中在 research 模块
9 处 `console.error` 全部在 `features/research/`，应引入统一错误上报服务替代。

---

## 安全审查专项

### 安全亮点（值得表扬）

| 亮点 | 位置 | 说明 |
|------|------|------|
| Argon2id 密码哈希 | `packages/auth/passwords.py` | 使用 Argon2id，默认参数合理 |
| 时序攻击防护 | `packages/auth/backends.py` | 不存在用户执行 dummy Argon2 校验 |
| Token 版本撤销 | `apps/api/dependencies/auth.py` | 每次认证复核 token_version |
| Refresh Token 重放检测 | `packages/auth/service.py` | 检测重放时撤销整个会话家族 |
| Cookie 安全 | `apps/api/routers/auth.py` | HttpOnly + SameSite=Strict + Secure |
| RLS 启动断言 | `apps/api/main.py` | 拒绝 superuser/bypassrls 连接 |
| SSRF 防护 | `packages/common/safe_http.py` | DNS 解析 + 私网阻断 + rebinding 防护 |
| 沙箱容器隔离 | `packages/research/sandbox.py` | 断网/只读/非 root/cap_drop ALL |
| 受限表达式引擎 | `packages/ai/numeric/expression.py` | AST 白名单，不使用 eval/exec |
| 信封加密 | `packages/common/crypto.py` | AES-256-GCM + key 轮换 |
| 上传完整性校验 | `packages/common/artifacts.py` | SHA-256 + Size 验证 |
| 速率限制 | `packages/common/rate_limiter.py` | IP+账号双维度限流 |

### 安全风险

| 风险 | 严重度 | 位置 | 说明 |
|------|--------|------|------|
| pickle.loads RCE | 高 | `packages/models/adapters.py:428` | 信任边界为 model:manage 权限，建议增加签名验证 |
| .env 明文密钥 | 高 | `.env:38-62` | 开发密钥明文存储，需确认 .gitignore 覆盖 |
| seed_users 硬编码密码 | 高 | `scripts/seed_users.py:32-52` | 19 用户密码明文 "asdf1234" |
| f-string SQL 表名拼接 | 中 | `governance_service.py:634` | 表名需确认来自硬编码白名单 |
| 速率限制仅单进程 | 中 | `rate_limiter.py` | 多进程部署时实际限制被放大 |
| Host header 注入 | 低 | `uploads.py:237` | 下载 URL 从 Host header 推导 MinIO 地址 |

---

## 测试审查专项

### 测试优势

- **六层测试分类**: unit / integration / contract / security / recovery / acceptance
- **安全测试**: 6 个文件覆盖 SQL 注入、SSRF、路径穿越、令牌重放、上传限制、作业越权
- **恢复测试**: 5 个文件覆盖重复投递、Redis 丢失、MinIO 中断、备份恢复、迁移回滚
- **CI 供应链安全**: Actions SHA 锁定 + SBOM + 错误码穷尽性检查
- **源码:测试比 ≈ 2.81:1**: 测试覆盖较为充分
- **Mypy 严格模式**: 已启用 `strict = true`

### 测试关注点

- **type: ignore 使用**: 227 处，78 个文件（大部分有合理原因，但 `query_service.py` 14 处需关注）
- **覆盖率门禁偏低**: Unit 25%, Integration 15%
- **已知 Bug 未修复**: `research.py:149` NameError 导致部分测试 skip
- **Makefile 不完整**: 缺少 security/recovery/contract/acceptance 目标

---

## 修复优先级建议

| 优先级 | 编号 | 工作量 | 说明 |
|--------|------|--------|------|
| P0 立即修复 | B-1 | 小 | S3 路径不匹配（功能性 bug） |
| P0 立即修复 | B-2 | 小 | 乐观锁未检查 rowcount |
| P0 立即修复 | B-3 | 小 | SSE 错误信息泄露 |
| P0 立即修复 | B-4 | 小 | KaTeX XSS 漏洞 |
| P1 本周修复 | S-1 | 中 | 部门 ID 回退随机 UUID |
| P1 本周修复 | S-2 | 小 | 备份幂等键失效 |
| P1 本周修复 | S-3 | 小 | 备份删除顺序 |
| P1 本周修复 | S-5 | 小 | CORS 配置收紧 |
| P1 本周修复 | S-8 | 小 | seed_users 密码处理 |
| P1 本周修复 | S-9 | 小 | 兜底错误响应统一 |
| P2 本月修复 | S-4 | 小 | 同步操作包装 async |
| P2 本月修复 | S-6 | 中 | 前端路由懒加载 |
| P2 本月修复 | S-7 | 中 | ResearchCanvas 竞态修复 |
| P2 本月修复 | S-10 | 小 | 输入验证约束 |
| P2 本月修复 | S-11 | 中 | N+1 查询优化 |
| P2 本月修复 | S-12 | 中 | 类型链修复 |
| P3 逐步改进 | N-* | 各异 | Nits 批量处理 |

---

## 结论

IRIP 项目在核心架构和安全设计上展现了高水平工程能力。安全测试和恢复测试的覆盖度在同类项目中属于优秀水平。

当前最紧迫的问题是 4 个 P0 blocker —— S3 路径不匹配可能导致文件上传功能完全不可用，乐观锁未检查可能导致取消作业静默失败，SSE 错误泄露和 KaTeX XSS 是两个安全问题。这些修复工作量都很小（各几行代码），应立即处理。

12 个 P1 suggestion 涉及数据完整性（部门 ID 回退、幂等键失效、删除顺序）和安全加固（CORS、硬编码密码），建议本周内完成。

前端方面，路由懒加载和 Research 模块的状态管理是主要改进方向，建议在下一次迭代中规划。
