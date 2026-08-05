# 备份恢复操作手册（IRIP V3-T03）

> 本手册描述 IRIP 平台的备份、恢复与完整性验证操作流程。
> 备份覆盖 PostgreSQL 数据库 + MinIO 对象存储，通过 `BackupManifest`
> 携带 SHA-256 校验和确保恢复时完整性可验证。

---

## 1. 概述

### 1.1 备份范围

| 组件 | 备份方式 | 校验和 |
|------|---------|--------|
| PostgreSQL | `pg_dump --format=custom` | `database_sha256`（dump 文件 SHA-256） |
| MinIO 对象 | `S3Repository` 逐对象下载 + 元数据 | `objects_sha256`（对象元数据聚合 SHA-256） |

### 1.2 BackupManifest 结构

```json
{
  "format_version": 1,
  "created_at": "2026-07-22T10:00:00+00:00",
  "application_version": "0.8.0",
  "migration_version": "0050_component_active_version",
  "database_sha256": "a1b2c3...",
  "object_count": 42,
  "objects_sha256": "d4e5f6...",
  "encrypted": false,
  "backup_id": "uuid-string"
}
```

- `format_version`：manifest 格式版本（当前为 1）。
- `migration_version`：备份时的 Alembic 迁移版本（恢复时用于前向兼容判断）。
- `database_sha256`：PostgreSQL dump 文件的 SHA-256 摘要。
- `objects_sha256`：MinIO 全部对象元数据的聚合 SHA-256 摘要。
- `encrypted`：备份包是否已加密（age）。

### 1.3 备份目录结构

```
backup-output/
├── manifest.json          # BackupManifest
├── database.dump          # PostgreSQL custom 格式 dump
├── objects/               # MinIO 对象
│   ├── objects.json        # 对象元数据（key + sha256 + size）
│   ├── sha256/ab/...       # 内容寻址对象
│   └── sha256/cd/...
└── backup.tar             # 打包归档（或 backup.tar.age 已加密）
```

---

## 2. 备份命令

### 2.1 通过 Docker Compose

```bash
# 未加密备份（输出到容器内 /backups，需挂载卷）
docker compose run --rm \
  -e IRIP_BACKUP_OUTPUT_DIR=/backups/$(date +%Y%m%d-%H%M%S) \
  -v /host/backups:/backups \
  backup

# 加密备份（需提前生成 age 密钥对）
docker compose run --rm \
  -e IRIP_BACKUP_OUTPUT_DIR=/backups/$(date +%Y%m%d-%H%M%S) \
  -e IRIP_BACKUP_AGE_RECIPIENT=age1xxxxx... \
  -v /host/backups:/backups \
  backup
```

### 2.2 直接运行 Python 脚本

```bash
# 设置环境变量
export IRIP_DATABASE_URL="postgresql+psycopg://irip:irip_dev_password@localhost:5432/irip"
export IRIP_MINIO_ENDPOINT="http://localhost:9000"
export IRIP_MINIO_ACCESS_KEY="irip"
export IRIP_MINIO_SECRET_KEY="irip_dev_password"
export IRIP_MINIO_BUCKET="irip-artifacts"

# 未加密备份
python -m deployments.compose.backup --output-dir /tmp/irip-backup

# 加密备份
export IRIP_BACKUP_AGE_RECIPIENT="age1xxxxx..."
python -m deployments.compose.backup --output-dir /tmp/irip-backup
```

### 2.3 通过 API 触发异步备份

```bash
# 创建备份作业（需 system:manage 权限 + JWT）
curl -X POST http://localhost:8000/api/v1/backups/ \
  -H "Authorization: Bearer <jwt>" \
  -H "Content-Type: application/json" \
  -d '{"encrypt": false}'

# 查询备份作业状态
curl http://localhost:8000/api/v1/backups/ \
  -H "Authorization: Bearer <jwt>"

# 查询特定备份详情
curl http://localhost:8000/api/v1/backups/<job_id> \
  -H "Authorization: Bearer <jwt>"
```

---

## 3. 恢复命令

### 3.1 通过 Docker Compose

```bash
# 恢复到隔离的 Compose 项目（避免覆盖生产数据）
docker compose -p irip-restore-$(date +%s) run --rm \
  -e IRIP_DATABASE_URL="postgresql+psycopg://irip:irip_dev_password@restore-postgres:5432/irip" \
  -e IRIP_MINIO_ENDPOINT="http://restore-minio:9000" \
  -e IRIP_RESTORE_COMPOSE_PROJECT=irip-restore \
  restore --backup-dir /backups/20260722-100000

# 恢复加密备份（需 age 身份文件）
docker compose run --rm \
  -e IRIP_BACKUP_AGE_IDENTITY=/keys/identity.txt \
  -v /host/keys:/keys:ro \
  restore --backup-dir /backups/20260722-100000
```

### 3.2 直接运行 Python 脚本

```bash
# 设置环境变量（指向恢复目标环境）
export IRIP_DATABASE_URL="postgresql+psycopg://irip:irip_dev_password@localhost:5432/irip_restore"
export IRIP_MINIO_ENDPOINT="http://localhost:9001"
export IRIP_MINIO_BUCKET="irip-restore-artifacts"

# 恢复
python -m deployments.compose.restore --backup-dir /tmp/irip-backup

# 跳过迁移（仅恢复数据）
python -m deployments.compose.restore --backup-dir /tmp/irip-backup --skip-migrations
```

### 3.3 通过 API 触发异步恢复

```bash
# 基于备份作业 ID 创建恢复作业
curl -X POST http://localhost:8000/api/v1/backups/<backup_job_id>/restore \
  -H "Authorization: Bearer <jwt>" \
  -H "Content-Type: application/json" \
  -d '{"backup_dir": "/backups/20260722-100000", "skip_migrations": false}'
```

---

## 4. 完整性验证

### 4.1 恢复前自动校验

恢复脚本在恢复前自动执行完整性校验：
1. 读取 `manifest.json`；
2. 重算 `database.dump` 的 SHA-256，与 `manifest.database_sha256` 比对；
3. 重算 MinIO 对象聚合 SHA-256，与 `manifest.objects_sha256` 比对；
4. 校验对象数量与 `manifest.object_count` 一致；
5. 任一不匹配则中止恢复，拒绝加载被篡改的备份。

### 4.2 手动校验

```python
from deployments.compose.backup_manifest import (
    BackupManifestValidator,
    load_manifest,
)
from pathlib import Path

backup_dir = Path("/tmp/irip-backup")
manifest = load_manifest(backup_dir)
validator = BackupManifestValidator()

try:
    validator.validate(manifest, backup_dir)
    print("完整性校验通过 ✓")
except Exception as exc:
    print(f"完整性校验失败: {exc}")
```

### 4.3 冒烟查询

恢复后自动运行冒烟查询，验证核心表可访问：

| 表 | 查询 |
|----|------|
| `app_user` | `SELECT count(*) FROM app_user` |
| `organization` | `SELECT count(*) FROM organization` |
| `role` | `SELECT count(*) FROM role` |
| `artifact_blob` | `SELECT count(*) FROM artifact_blob` |
| `job` | `SELECT count(*) FROM job` |
| `alembic_version` | `SELECT count(*) FROM alembic_version` |

---

## 5. 加密选项

### 5.1 生成 age 密钥对

```bash
# 安装 age（macOS）
brew install age

# 生成密钥对
age-keygen -o /keys/identity.txt

# 输出示例：
# Public key: age1ql3z7hjy54pw3hyv5q...
# 将 Public key 设为 IRIP_BACKUP_AGE_RECIPIENT 环境变量
```

### 5.2 加密备份

```bash
export IRIP_BACKUP_AGE_RECIPIENT="age1ql3z7hjy54pw3hyv5q..."
python -m deployments.compose.backup --output-dir /tmp/irip-backup
# 生成 backup.tar.age（加密归档）
```

### 5.3 解密恢复

```bash
export IRIP_BACKUP_AGE_IDENTITY="/keys/identity.txt"
python -m deployments.compose.restore --backup-dir /tmp/irip-backup
# 自动解密 backup.tar.age → backup.tar → 解压 → 恢复
```

### 5.4 安全注意事项

- **age 身份文件**（`identity.txt`）是解密密钥，必须妥善保管，建议离线存储。
- **不要**将身份文件与备份包存放在同一位置。
- 加密备份的 `manifest.json` 中 `encrypted: true`，恢复脚本据此自动调用 age 解密。
- 即使加密，manifest 本身明文存储（含哈希校验和），可用于离线完整性校验。

---

## 6. 定期备份建议

### 6.1 备份频率

| 场景 | 建议频率 | 保留策略 |
|------|---------|---------|
| 生产环境 | 每日 1 次（凌晨低峰期） | 保留 30 天滚动窗口 |
| 数据迁移前 | 即时 1 次 | 迁移完成验证后归档 |
| 版本升级前 | 即时 1 次 | 保留至下一版本稳定 |
| 灾难恢复演练 | 每月 1 次 | 演练后清理 |

### 6.2 定时备份（Cron）

```bash
# /etc/cron.d/irip-backup
# 每日凌晨 2:00 执行备份
0 2 * * * irip /usr/bin/env \
  IRIP_DATABASE_URL="postgresql+psycopg://irip:***@db:5432/irip" \
  IRIP_MINIO_ENDPOINT="http://minio:9000" \
  IRIP_MINIO_ACCESS_KEY="irip" \
  IRIP_MINIO_SECRET_KEY="***" \
  IRIP_MINIO_BUCKET="irip-artifacts" \
  IRIP_BACKUP_OUTPUT_DIR="/backups/$(date +\%Y\%m\%d)" \
  IRIP_BACKUP_AGE_RECIPIENT="age1..." \
  /opt/irip/.venv/bin/python -m deployments.compose.backup \
  >> /var/log/irip-backup.log 2>&1
```

### 6.3 备份保留与清理

```bash
# 清理 30 天前的备份
find /backups -maxdepth 1 -type d -mtime +30 -exec rm -rf {} \;

# 或使用脚本
python -c "
from pathlib import Path
from datetime import datetime, timedelta

backup_root = Path('/backups')
cutoff = datetime.now() - timedelta(days=30)

for d in backup_root.iterdir():
    if d.is_dir() and d.stat().st_mtime < cutoff.timestamp():
        print(f'Removing old backup: {d}')
        # d.rmdir()  # 实际清理时取消注释
"
```

### 6.4 备份验证（恢复排练）

> **强烈建议**：定期在隔离环境中执行恢复排练，验证备份可用性。

```bash
# 1. 启动隔离环境
docker compose -p irip-rehearsal up -d postgres minio redis

# 2. 恢复到隔离环境
IRIP_DATABASE_URL="postgresql+psycopg://irip:irip_dev_password@localhost:55433/irip" \
IRIP_MINIO_ENDPOINT="http://localhost:59002" \
python -m deployments.compose.restore --backup-dir /backups/20260722

# 3. 验证冒烟查询
# （恢复脚本自动输出冒烟查询结果）

# 4. 清理隔离环境
docker compose -p irip-rehearsal down -v
```

---

## 7. 前向兼容迁移

恢复脚本仅应用前向兼容的迁移：

- **备份版本 ≤ 当前版本**：执行 `alembic upgrade head`，将 schema 补齐到最新。
- **备份版本 > 当前版本**（降级场景）：**拒绝自动迁移**，需人工介入。

```bash
# 若遇到降级场景，错误信息示例：
# 备份迁移版本 (0051_new_feature) 比当前代码版本 (0050_component_active_version) 新
# — 不支持自动降级，请人工处理
```

降级处理建议：
1. 部署与备份版本匹配的代码；
2. 恢复数据后不再执行迁移；
3. 或使用 `--skip-migrations` 参数跳过迁移步骤。

---

## 8. 故障排查

### 8.1 pg_dump 失败

```
pg_dump failed (exit=1): connection refused
```

**原因**：数据库不可达。
**解决**：检查 `IRIP_DATABASE_URL` 与 PostgreSQL 容器状态。

### 8.2 完整性校验失败

```
ManifestValidationError: 数据库 dump SHA-256 不匹配
```

**原因**：备份包被篡改或传输损坏。
**解决**：重新备份，检查存储介质完整性。

### 8.3 age 加密/解密失败

```
age binary not found
```

**原因**：未安装 age 工具。
**解决**：`brew install age`（macOS）或 `apt install age`（Linux）。

### 8.4 迁移版本不兼容

```
备份迁移版本 (0051) 比当前代码版本 (0050) 新 — 不支持自动降级
```

**原因**：尝试用旧代码恢复新版本的备份。
**解决**：升级代码到匹配版本，或使用 `--skip-migrations` 跳过迁移。

---

## 9. 环境变量速查

| 变量名 | 用途 | 默认值 |
|--------|------|--------|
| `IRIP_DATABASE_URL` | PostgreSQL 连接字符串 | （必填） |
| `IRIP_MINIO_ENDPOINT` | MinIO 端点 | `http://localhost:9000` |
| `IRIP_MINIO_ACCESS_KEY` | MinIO 访问密钥 | `irip` |
| `IRIP_MINIO_SECRET_KEY` | MinIO 秘密密钥 | `irip_dev_password` |
| `IRIP_MINIO_BUCKET` | MinIO bucket | `irip-artifacts` |
| `IRIP_MINIO_REGION` | MinIO 区域 | `us-east-1` |
| `IRIP_APPLICATION_VERSION` | 应用版本 | `0.8.0` |
| `IRIP_BACKUP_OUTPUT_DIR` | 备份输出目录 | 系统临时目录 |
| `IRIP_BACKUP_AGE_RECIPIENT` | age 加密公钥 | （不加密） |
| `IRIP_BACKUP_AGE_IDENTITY` | age 解密身份文件 | （不解密） |
| `IRIP_RESTORE_COMPOSE_PROJECT` | 恢复隔离项目名 | （不启动 Compose） |
| `IRIP_RESTORE_COMPOSE_FILE` | Compose 文件路径 | `compose.yaml` |
| `IRIP_RESTORE_SKIP_MIGRATIONS` | 跳过迁移 | `false` |
