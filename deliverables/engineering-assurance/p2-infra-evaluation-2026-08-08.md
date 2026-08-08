# IRIP P2 基础设施架构评估 — K8s / Redis HA / PostgreSQL HA

**日期**：2026-08-08
**评估人**：甄宇航（Zhen，工程督导）
**状态**：评估结论，待决策

---

## 评估背景

IRIP 当前以 Docker Compose 部署，适用于单机/小规模场景。P2 移交清单要求评估是否需要 K8s 以及 Redis/PostgreSQL 的高可用方案。

---

## P2-I1: K8s / IaC 评估

### 现状
- Docker Compose 全量编排（10 个服务）
- 单机部署，无水平扩展能力
- 部署依赖手动 `docker compose up`

### 评估结论

| 维度 | Docker Compose | K8s (Helm) | 建议 |
|------|---------------|-------------|------|
| 部署复杂度 | 低（单文件） | 高（Chart + values） | 当前 Demo 阶段用 Compose |
| 水平扩展 | 不支持 | 原生支持 | 中试规模不需要 |
| 自动扩缩 | 不支持 | HPA/VPA | 当前负载不需要 |
| 滚动更新 | 手动 | 原生支持 | CD 流水线已覆盖 |
| 自愈 | depends_on/restart | liveness/readiness probe | restart: unless-stopped 已覆盖 |
| 资源隔离 | 弱（共享内核） | 强（cgroups/limits） | 中试规模可接受 |

**建议**：维持 Docker Compose 部署。当以下任一条件满足时再迁移 K8s：
- 并发用户 > 500（需要水平扩展 API 实例）
- 需要多环境隔离（dev/staging/prod 集群）
- 需要自动扩缩容（流量波动大）
- 需要蓝绿/金丝雀部署

**迁移路径**（未来需要时）：
1. 编写 Helm Chart（values.yaml 区分 dev/staging/prod）
2. PostgreSQL → 使用 CloudNativePG Operator 或 RDS
3. Redis → 使用 Redis Operator 或 ElastiCache
4. MinIO → 使用 MinIO Operator 或 S3
5. CI/CD → ArgoCD/GitOps

---

## P2-I3: Redis Sentinel 高可用

### 现状
- 单实例 Redis，无副本
- Redis 宕机 → 限流失效、Outbox 降级（已支持 Redis 宕机降级运行）

### 评估结论

Redis 在 IRIP 中的用途：
- 限流（RedisRateLimiter）
- Outbox 模式（消息分发）
- get_fact_data 缓存（5 分钟 TTL）
- 部门并发计数器

**当前风险**：Redis 宕机时以上功能降级，但不影响核心数据写入（PostgreSQL 为权威存储）。

**Redis Sentinel 方案**：
```
架构：1 master + 2 replicas + 3 sentinels
故障切换：Sentinel 自动选举新 master（< 10s）
客户端适配：redis-py 支持 Sentinel 模式
```

**建议**：
- **短期**：维持单实例 Redis + 容错降级（已实现）
- **中期**：添加 Redis Sentinel 配置模板（compose.yaml profiles: ha-redis），在需要时启用
- **长期**：迁移到托管 Redis（ElastiCache/Redis Cloud）

### Sentinel 配置模板（供未来参考）

```yaml
# compose.yaml profiles: ["ha-redis"] 下添加
redis-replica-1:
  profiles: ["ha-redis"]
  image: redis:7-alpine
  command: redis-server --replicaof redis 6379 --requirepass ${IRIP_REDIS_PASSWORD}
  depends_on: [redis]

redis-replica-2:
  profiles: ["ha-redis"]
  image: redis:7-alpine
  command: redis-server --replicaof redis 6379 --requirepass ${IRIP_REDIS_PASSWORD}
  depends_on: [redis]

redis-sentinel-1:
  profiles: ["ha-redis"]
  image: redis:7-alpine
  command: redis-sentinel /etc/redis/sentinel.conf
  configs:
    sentinel.conf: |
      sentinel monitor mymaster redis 6379 2
      sentinel down-after-milliseconds mymaster 5000
      sentinel parallel-syncs mymaster 1
      sentinel failover-timeout mymaster 30000
```

---

## P2-I4: PostgreSQL 高可用

### 现状
- 单实例 PostgreSQL 16
- 已有 PITR 备份（pg_basebackup + WAL 归档）
- 无自动故障切换

### 评估结论

PostgreSQL 在 IRIP 中是**权威数据存储**，单点故障会导致全平台不可用。

**方案对比**：

| 方案 | 优点 | 缺点 | 适用场景 |
|------|------|------|---------|
| Patroni | 开源、自动故障切换、流复制 | 配置复杂、需 etcd/ZooKeeper | 私有云 |
| CloudNativePG | K8s 原生、CNCF 项目 | 依赖 K8s | K8s 环境 |
| RDS/Cloud SQL | 全托管、零运维 | 供应商锁定、成本高 | 云生产 |
| 流复制+手动切换 | 简单、无额外组件 | 故障切换需人工 | 开发/中试 |

**建议**：
- **当前阶段（Demo/中试）**：维持单实例 + PITR 备份（RTO < 30 分钟可接受）
- **生产阶段**：选择 RDS/Cloud SQL（全托管，最低运维成本）
- **私有部署**：Patroni + etcd（自动故障切换，RTO < 30s）

### 迁移路径（未来需要时）
1. 创建流复制副本（pg_basebackup → standby）
2. 部署 Patroni 管理集群
3. 配置 PgBouncer 指向 Patroni leader
4. 验证 RLS GUC 在故障切换后的一致性

---

## 总结决策矩阵

| 项目 | 当前阶段决策 | 触发迁移条件 | 迁移方案 |
|------|-------------|-------------|---------|
| K8s | 维持 Docker Compose | 并发 > 500 / 多环境 / 自动扩缩 | Helm Chart + ArgoCD |
| Redis HA | 维持单实例 + 降级 | Redis 可用性 SLA > 99.9% | Redis Sentinel 3 节点 |
| PostgreSQL HA | 维持单实例 + PITR | 生产上线 / RTO < 30s | RDS 优先 / Patroni 次选 |

---

> 本评估由工程保障团队于 2026-08-08 编写，决策需由项目技术负责人确认。
