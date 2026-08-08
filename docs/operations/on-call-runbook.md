# IRIP 值班运维手册（On-Call Runbook）

> 本文档供 IRIP 平台值班运维人员使用，覆盖值班职责、告警响应、常见故障处理、备份恢复及事后复盘流程。

---

## 1. 值班职责与轮换

### 1.1 值班角色

| 角色 | 职责 | 联系方式 |
|------|------|----------|
| 主值班（Primary） | 第一响应告警，执行故障处理，编写事后复盘 | PagerDuty / 企业微信值班群 |
| 副值班（Secondary） | 主值班未响应时升级接管，协助排查复杂故障 | PagerDuty / 企业微信值班群 |
| 升级联系人（Escalation） | P0 故障升级至架构师/DBA/运维负责人 | 详见内部通讯录 |

### 1.2 轮换规则

- 轮换周期：每周一轮换（周一 09:00 交接）。
- 主副值班不可同时休假。
- 交接内容：上周未关闭的告警、待跟进的工单、已知风险项。
- 值班日历维护在团队 wiki / PagerDuty schedule 中。

### 1.3 值班期间要求

- 工作时间内 5 分钟内响应告警，非工作时间 15 分钟内响应。
- 值班期间保持手机畅通，笔记本可随时接入。
- 值班期间不得进行高风险变更（如 schema 迁移、生产数据批量操作）。

---

## 2. 告警响应流程

### 2.1 告警分级

| 级别 | 定义 | 响应时间 | 升级阈值 | 示例 |
|------|------|----------|----------|------|
| **P0 (Critical)** | 生产服务完全不可用或数据安全风险 | 5 分钟 | 15 分钟未恢复升级至 Escalation | API 全部宕机、DB 不可连接、数据泄露 |
| **P1 (Warning)** | 核心功能降级，影响部分用户 | 15 分钟 | 1 小时未恢复升级至 Secondary | Outbox 积压、错误率 >5%、Worker 心跳超时 |
| **P2 (Info)** | 非核心告警，可延后处理 | 1 小时 | 无需升级 | 磁盘使用率 >70%、证书即将过期 |

### 2.2 响应步骤

```
告警触发
  │
  ├─→ 1. 确认告警（ACK）并查看告警内容、关联仪表盘
  │
  ├─→ 2. 判断告警级别（P0/P1/P2）
  │
  ├─→ 3. 按本文档对应章节执行应急处理
  │
  ├─→ 4. 在值班群同步状态（已确认 / 处理中 / 已恢复）
  │
  ├─→ 5. P0 故障：立即通知 Escalation，每 15 分钟同步一次进展
  │
  ├─→ 6. 故障恢复后确认告警已消除
  │
  └─→ 7. 24 小时内提交事后复盘报告（P0/P1 必须）
```

### 2.3 告警静默（Silence）

- 仅在已确认误报或计划内维护时静默告警。
- 静默须设置明确的过期时间，不得超过 4 小时。
- P0 告警不允许静默。

---

## 3. 常见故障处理

### 3.1 API 宕机

**症状**：`APIDown` 告警触发，`up{job="irip-api"} == 0`。

**排查步骤**：

1. 确认告警是否误报（检查 Prometheus target 状态）。
2. 登录服务器检查 API 进程是否存活：
   ```bash
   docker compose ps api            # 查看容器状态
   docker compose logs api --tail 100  # 查看最近日志
   ```
3. 检查依赖服务（PostgreSQL / Redis / MinIO）是否正常：
   ```bash
   docker compose ps postgres redis minio
   ```
4. 常见原因：
   - **DB 连接失败** → 见 [3.3 DB 连接失败](#33-db-连接失败)
   - **端口占用** → 检查 `8000` 端口是否被占用，重启容器
   - **OOM** → 检查 `docker stats`，必要时扩容内存
   - **配置错误** → 检查 `.env` 文件，确认 `IRIP_DATABASE_URL` 等变量正确
5. 恢复操作：
   ```bash
   docker compose restart api
   # 确认服务恢复
   curl -sf http://localhost:8000/api/v1/health/live
   ```
6. 若 15 分钟内无法恢复，升级至 Escalation。

### 3.2 Worker 卡死

**症状**：`WorkerDown` 或 `WorkerHeartbeatStale` 告警触发。

**排查步骤**：

1. 检查 Worker 容器状态与日志：
   ```bash
   docker compose ps worker
   docker compose logs worker --tail 200
   ```
2. 检查 Celery 队列深度（Grafana → Queue Depth 面板）。
3. 检查 Redis 是否正常（Worker 依赖 Redis 作为 broker）：
   ```bash
   docker compose exec redis redis-cli ping
   ```
4. 常见原因：
   - **任务死锁** → 检查是否有长耗时任务，必要时终止并重试
   - **Redis 不可达** → 见 [3.4 Redis 不可达](#34-redis-不可达)
   - **OOM** → Worker 处理大文件时可能 OOM，检查 `docker stats`
5. 恢复操作：
   ```bash
   docker compose restart worker
   # 确认心跳恢复
   docker compose logs worker --tail 20 | grep heartbeat
   ```
6. 若心跳持续超时，检查是否有僵尸任务：
   ```bash
   docker compose exec worker celery -A apps.worker.celery_app inspect active
   docker compose exec worker celery -A apps.worker.celery_app purge  # 谨慎使用
   ```

### 3.3 DB 连接失败

**症状**：API/Worker 启动报 `could not connect to server`，或间歇性连接超时。

**排查步骤**：

1. 检查 PostgreSQL 容器状态：
   ```bash
   docker compose ps postgres
   docker compose logs postgres --tail 100
   ```
2. 测试连接：
   ```bash
   docker compose exec postgres pg_isready -U irip
   ```
3. 检查连接数是否超限：
   ```bash
   docker compose exec postgres psql -U irip -c "SELECT count(*) FROM pg_stat_activity;"
   # 最大连接数
   docker compose exec postgres psql -U irip -c "SHOW max_connections;"
   ```
4. 常见原因：
   - **连接池耗尽** → 检查应用配置 `IRIP_DATABASE_URL`，确认连接池参数合理
   - **磁盘满** → 检查 `df -h`，PostgreSQL 磁盘满会拒绝写入和连接
   - **密码错误** → 确认 `IRIP_DATABASE_PASSWORD` 与容器配置一致
5. 恢复操作：
   ```bash
   docker compose restart postgres
   # 等待健康检查通过后重启 API/Worker
   docker compose restart api worker
   ```

### 3.4 Redis 不可达

**症状**：Celery 任务不执行，API 报 Redis 连接错误。

**排查步骤**：

1. 检查 Redis 容器：
   ```bash
   docker compose ps redis
   docker compose exec redis redis-cli ping
   ```
2. 检查 Redis 内存使用：
   ```bash
   docker compose exec redis redis-cli info memory | grep used_memory_human
   ```
3. 常见原因：
   - **内存满** → 清理旧任务结果：`redis-cli FLUSHDB`（谨慎，仅清空当前 DB）
   - **密码不匹配** → 确认 `IRIP_REDIS_PASSWORD` 与 Redis `--requirepass` 一致
   - **网络隔离** → 确认 API/Worker 与 Redis 在同一 Docker network
4. 恢复操作：
   ```bash
   docker compose restart redis
   docker compose restart api worker
   ```

### 3.5 MinIO 磁盘满

**症状**：文件上传报 `Insufficient disk space`，MinIO 写入失败。

**排查步骤**：

1. 检查 MinIO 容器状态与磁盘：
   ```bash
   docker compose ps minio
   docker compose exec minio df -h
   ```
2. 检查宿主机磁盘：
   ```bash
   df -h  # 查看 Docker volume 所在分区
   ```
3. 常见原因：
   - **临时文件堆积** → 检查 MinIO bucket 中的孤儿对象
   - **Docker volume 扩容不足** → 扩容宿主机磁盘或迁移 volume
4. 恢复操作：
   - 清理不再需要的临时上传文件
   - 扩容磁盘后重启 MinIO：
     ```bash
     docker compose restart minio
     ```
   - 若磁盘满导致 API 无法正常工作，优先清理 MinIO 临时文件恢复服务

---

## 4. 备份恢复流程

详细备份与恢复操作请参考 [backup-restore.md](./backup-restore.md)。

### 4.1 备份策略摘要

| 项目 | 说明 |
|------|------|
| 备份频率 | 每日 02:00 UTC（cron 定时） |
| 备份内容 | PostgreSQL 全量 + MinIO bucket + 配置文件 |
| 备份保留 | daily 14 天，milestone 永久 |
| 异地存储 | `IRIP_BACKUP_REMOTE_TARGET` 配置（S3 或 rclone remote） |
| 恢复验证 | 每月执行一次恢复演练 |

### 4.2 紧急恢复

```bash
# 1. 停止应用服务
docker compose stop api worker

# 2. 恢复 PostgreSQL（参考 backup-restore.md）
python scripts/restore_backup.py --date <YYYY-MM-DD>

# 3. 恢复 MinIO 对象（参考 backup-restore.md）
python scripts/restore_minio.py --date <YYYY-MM-DD>

# 4. 验证数据完整性后重启服务
docker compose start api worker
curl -sf http://localhost:8000/api/v1/health/live
```

---

## 5. 事后复盘模板

> P0/P1 故障恢复后 24 小时内必须提交事后复盘报告。

```markdown
# 事后复盘：<故障标题>

## 基本信息

- 故障编号：INC-<YYYYMMDD>-<序号>
- 故障级别：P0 / P1
- 故障时间：<YYYY-MM-DD HH:MM> ~ <YYYY-MM-DD HH:MM>（持续 X 分钟）
- 影响范围：<受影响的用户/功能/服务>
- 值班人员：<姓名>
- 相关告警：<告警名称及链接>

## 时间线

| 时间 | 事件 |
|------|------|
| HH:MM | 告警触发 |
| HH:MM | 值班确认告警 |
| HH:MM | 开始排查 |
| HH:MM | 定位根因 |
| HH:MM | 执行恢复操作 |
| HH:MM | 服务恢复 |

## 根因分析

<详细描述故障根因，包括技术层面和管理层面的原因>

## 影响评估

- 用户影响：<数量/功能>
- 数据影响：<是否有数据丢失/损坏>
- 业务影响：<经济损失/SLA 违约>

## 恢复措施

<描述采取的恢复操作及其效果>

## 改进项（Action Items）

| 编号 | 改进项 | 负责人 | 截止日期 | 状态 |
|------|--------|--------|----------|------|
| 1 | <改进项描述> | <姓名> | <日期> | Open |

## 经验教训

<总结本次故障的教训和可复用的经验>
```

---

## 6. RTO/RPO 目标定义

### 6.1 恢复目标

| 指标 | 目标 | 说明 |
|------|------|------|
| **RTO**（Recovery Time Objective） | **2 小时** | 从故障发生到服务完全恢复的最长允许时间 |
| **RPO**（Recovery Point Objective） | **24 小时** | 可接受的最大数据丢失窗口（基于每日备份） |

### 6.2 备份配置

| 配置项 | 值 |
|--------|-----|
| 备份频率 | 每日 02:00 UTC |
| 备份类型 | PostgreSQL 全量 (`pg_dump`) + MinIO bucket 同步 + 配置文件 |
| 保留策略 | daily: 14 天；milestone（版本发布/重大变更）: 永久 |
| 异地存储 | `IRIP_BACKUP_REMOTE_TARGET`（S3 bucket 或 rclone remote） |
| 备份验证 | 每月恢复演练（在 staging 环境执行完整恢复流程） |

### 6.3 RTO/RPO 验证

- **RTO 验证**：每月恢复演练中计时，目标为 2 小时内完成全量恢复并恢复服务。
- **RPO 验证**：恢复后检查最新数据时间戳，确认丢失数据不超过 24 小时。
- 若实际 RTO/RPO 超出目标，需在事后复盘中分析原因并制定改进计划。
