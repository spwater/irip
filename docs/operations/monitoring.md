# 监控运维指南

> 适用版本：IRIP V0–V3
> 关联文档：`docs/operations/install-upgrade.md`、`docs/operations/backup-restore.md`

---

## 1. 健康检查端点

### 1.1 存活探针（Liveness）

```bash
curl http://localhost:8000/api/v1/health/live
```

**响应**：
```json
{"status": "live"}
```

**用途**：检测 API 进程是否运行。返回 200 表示进程存活，返回非 200 表示进程不可用。

### 1.2 就绪探针（Readiness）

```bash
curl http://localhost:8000/api/v1/health/ready
```

**响应**：
```json
{
  "status": "ready",
  "checks": {
    "database": "ok",
    "redis": "ok",
    "minio": "ok",
    "outbox": "ok"
  }
}
```

**用途**：检测所有依赖是否就绪。任一组件不可用返回 503。

| 检查项 | 说明 | 不可用时影响 |
|--------|------|------------|
| `database` | PostgreSQL 连接 | API 无法处理任何请求 |
| `redis` | Redis 连接 | 异步作业无法投递（降级模式：API 仍可读） |
| `minio` | MinIO 连接 | 文件上传/下载不可用 |
| `outbox` | Outbox dispatcher 状态 | 事件投递延迟（不阻塞 API） |

### 1.3 Docker Compose 健康检查

`compose.yaml` 中各服务已配置 healthcheck：

```yaml
postgres:
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U irip -d irip"]
    interval: 5s
    timeout: 5s
    retries: 10

redis:
  healthcheck:
    test: ["CMD", "redis-cli", "ping"]
    interval: 5s
    timeout: 3s
    retries: 10

minio:
  healthcheck:
    test: ["CMD", "curl", "-f", "http://localhost:9000/minio/health/live"]
    interval: 5s
    timeout: 3s
    retries: 10

api:
  healthcheck:
    test: ["CMD-SHELL", "curl -f http://localhost:8000/api/v1/health/live || exit 1"]
    interval: 10s
    timeout: 5s
    retries: 10
```

### 1.4 查看服务状态

```bash
# 查看所有服务状态
docker compose ps

# 查看特定服务日志
docker compose logs -f api
docker compose logs -f worker
docker compose logs -f postgres
```

---

## 2. 日志收集

### 2.1 日志格式

IRIP 使用 `structlog` 输出结构化 JSON 日志，便于审计追溯和日志聚合：

```json
{
  "timestamp": "2026-07-22T10:30:00Z",
  "level": "info",
  "event": "job.accepted",
  "job_id": "uuid-string",
  "kind": "derivation",
  "organization_id": "uuid-string",
  "actor_user_id": "uuid-string"
}
```

### 2.2 日志级别

| 级别 | 用途 | 环境变量 |
|------|------|---------|
| `DEBUG` | 开发调试 | `IRIP_LOG_LEVEL=DEBUG` |
| `INFO` | 正常运行（默认） | `IRIP_LOG_LEVEL=INFO` |
| `WARNING` | 降级/重试 | — |
| `ERROR` | 错误（需关注） | — |
| `CRITICAL` | 严重故障 | — |

```bash
# 设置日志级别
export IRIP_LOG_LEVEL=DEBUG
```

### 2.3 日志查看

```bash
# 实时查看 API 日志
docker compose logs -f api

# 查看 Worker 日志（含异步作业执行）
docker compose logs -f worker

# 过滤错误日志
docker compose logs api 2>&1 | grep '"level": "error"'

# 导出日志到文件
docker compose logs api > api.log 2>&1
```

### 2.4 日志聚合建议

生产环境建议将日志聚合到集中式日志系统：

- **ELK Stack**（Elasticsearch + Logstash + Kibana）：通过 Docker 日志驱动收集。
- **Grafana Loki**：轻量级日志聚合，与 Prometheus 配合。
- 配置 Docker 日志驱动：
  ```yaml
  # /etc/docker/daemon.json
  {
    "log-driver": "json-file",
    "log-opts": {
      "max-size": "100m",
      "max-file": "5"
    }
  }
  ```

---

## 3. 性能指标

### 3.1 关键性能指标

| 指标 | 阈值 | 检查方式 |
|------|------|---------|
| API P95 响应时间 | < 500ms | k6 性能冒烟测试 |
| API 错误率 | < 1% | k6 性能冒烟测试 |
| 作业完成率 | > 95% | 作业监控页面 |
| 数据库连接池使用率 | < 80% | PostgreSQL 监控 |
| Redis 内存使用 | < 70% | Redis INFO 命令 |
| MinIO 磁盘使用 | < 85% | MinIO 控制台 |

### 3.2 k6 性能冒烟测试

```bash
# 运行 k6 性能冒烟测试（模拟 10 并发用户 60 秒）
k6 run tests/performance/k6-smoke.js
```

测试覆盖路径：
1. 登录
2. 列表事实
3. 搜索事实
4. AI 助手对话
5. 参数列表

断言：
- P95 响应时间 < 500ms
- 错误率 < 1%

### 3.3 PostgreSQL 指标

```sql
-- 连接数
SELECT count(*) FROM pg_stat_activity;

-- 数据库大小
SELECT pg_size_pretty(pg_database_size('irip'));

-- 表大小排行
SELECT relname, pg_size_pretty(pg_total_relation_size(relid))
FROM pg_catalog.pg_statio_user_tables
ORDER BY pg_total_relation_size(relid) DESC LIMIT 10;

-- 慢查询（需启用 pg_stat_statements 扩展）
SELECT query, calls, mean_exec_time, total_exec_time
FROM pg_stat_statements
ORDER BY mean_exec_time DESC LIMIT 10;
```

### 3.4 Redis 指标

```bash
# 查看 Redis 信息
docker compose exec redis redis-cli INFO

# 关键指标
docker compose exec redis redis-cli INFO memory | grep used_memory_human
docker compose exec redis redis-cli INFO stats | grep instantaneous_ops_per_sec
docker compose exec redis redis-cli LLEN celery  # 队列长度
```

### 3.5 MinIO 指标

```bash
# 查看 MinIO 磁盘使用
curl http://localhost:9000/minio/health/cluster

# 通过 aws-cli 查看 bucket 大小
aws --endpoint-url http://localhost:9000 s3 ls s3://irip-artifacts --recursive --summarize
```

---

## 4. 告警配置

### 4.1 告警规则建议

| 告警项 | 触发条件 | 严重级别 |
|--------|---------|---------|
| API 不可用 | `/health/live` 连续 3 次失败 | 🔴 严重 |
| 依赖不可用 | `/health/ready` 任一组件失败 | 🔴 严重 |
| 作业积压 | 队列长度 > 100 | 🟡 警告 |
| 作业失败率 | 失败率 > 5%（5 分钟窗口） | 🟡 警告 |
| 磁盘使用 | MinIO 磁盘使用 > 85% | 🟡 警告 |
| 数据库连接 | 连接池使用率 > 80% | 🟡 警告 |
| Redis 内存 | 内存使用 > 70% | 🟡 警告 |
| 备份失败 | 备份作业状态 = failed | 🔴 严重 |
| 证书过期 | TLS 证书 < 30 天过期 | 🟡 警告 |

### 4.2 Prometheus + Grafana 规划

IRIP 规划接入 Prometheus 指标暴露（未来阶段）：

```yaml
# scrape_config（规划）
scrape_configs:
  - job_name: irip-api
    metrics_path: /api/v1/metrics
    static_configs:
      - targets: ['api:8000']
```

当前阶段通过 Docker healthcheck + 日志监控实现基本告警。生产环境建议：

1. **Prometheus + Grafana**：指标采集 + 可视化仪表盘。
2. **Alertmanager**：告警路由和通知（邮件/Slack/企业微信）。
3. **Loki**：日志聚合和查询。

### 4.3 简易告警脚本

```bash
#!/usr/bin/env bash
# scripts/health-alert.sh — 简易健康告警
set -euo pipefail

HEALTH_URL="http://localhost:8000/api/v1/health/ready"
MAX_RETRIES=3
ALERT_WEBHOOK="${ALERT_WEBHOOK:-}"

for i in $(seq 1 $MAX_RETRIES); do
  if curl -sf "$HEALTH_URL" > /dev/null 2>&1; then
    exit 0
  fi
  sleep 5
done

# 健康检查失败
MESSAGE="IRIP 健康检查失败: $HEALTH_URL 连续 $MAX_RETRIES 次不可达"
echo "[CRITICAL] $MESSAGE"

if [ -n "$ALERT_WEBHOOK" ]; then
  curl -X POST "$ALERT_WEBHOOK" -H "Content-Type: application/json" \
    -d "{\"text\": \"$MESSAGE\"}"
fi
exit 1
```

---

## 5. 审计事件查询

### 5.1 通过 API 查询

```bash
# 查询审计事件（需 audit:read 权限）
curl "http://localhost:8000/api/v1/audit/events?action=auth.login&limit=50" \
  -H "Authorization: Bearer <jwt>"
```

### 5.2 通过治理控制台

1. 进入 **审计** 页面（`/governance/audit`）。
2. 使用筛选器：
   - Action 下拉（如 `auth.login`、`artifact.upload`、`model.publish`）
   - Resource Type 下拉
   - 时间范围选择器
3. 结果表格展示：时间、操作人、动作、资源类型、资源 ID。
4. 点击行展开 payload JSON 详情（已脱敏）。

### 5.3 审计事件类型

| Action | 说明 |
|--------|------|
| `auth.login` | 用户登录 |
| `auth.refresh_replayed` | 刷新令牌重放（安全事件） |
| `artifact.upload` | 文件上传 |
| `job.cancel` | 作业取消 |
| `governance.role_update` | 用户角色变更 |
| `model.publish` | 模型发布 |
| `model.rollback` | 模型回滚 |
| `backup.create` | 备份创建 |
| `backup.restore` | 恢复执行 |

---

## 6. 作业监控

### 6.1 通过 API 查询作业

```bash
# 列出所有作业
curl "http://localhost:8000/api/v1/jobs?status=running" \
  -H "Authorization: Bearer <jwt>"

# 查看作业详情
curl http://localhost:8000/api/v1/jobs/{job_id} \
  -H "Authorization: Bearer <jwt>"
```

### 6.2 通过作业中心页面

1. 进入 **作业中心** 页面（`/jobs`）。
2. 查看全量作业列表（kind/status/stage/progress）。
3. 点击作业查看详情（状态时间线：accepted→queued→running→succeeded/failed）。
4. 支持取消操作。

### 6.3 作业状态

| 状态 | 说明 |
|------|------|
| `accepted` | 作业已接受，等待投递 |
| `queued` | 作业已入队，等待 Worker 租约 |
| `running` | Worker 正在执行 |
| `retry_wait` | 等待重试（退避中） |
| `succeeded` | 执行成功 |
| `failed` | 执行失败 |
| `cancel_requested` | 取消请求已提交 |
| `cancelled` | 已取消 |

---

## 7. 系统健康仪表盘

进入 **系统健康** 页面（`/governance/health`），展示各组件状态：

| 组件 | 状态 | 来源 |
|------|------|------|
| 数据库 | 🟢 ok / 🔴 error | `/health/ready` → database |
| Redis | 🟢 ok / 🔴 error | `/health/ready` → redis |
| MinIO | 🟢 ok / 🔴 error | `/health/ready` → minio |
| Outbox | 🟢 ok / 🟡 delayed | `/health/ready` → outbox |

页面每 30 秒自动刷新。
