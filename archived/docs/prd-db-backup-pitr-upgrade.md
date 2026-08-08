# IRIP 数据库备份升级增量 PRD — PG PITR + WAL 归档 + MinIO mc mirror

> 基线文档: `docs/prd-db-backup.md`（commit 4eab3c6，pg_dump + S3Repository 方案）  
> 本文档仅描述**变更部分**，未提及的需求项维持基线 PRD 不变。  
> 版本: 1.0 | 日期: 2026-08-01

---

## 1. 变更目标

| 编号 | 目标 | 衡量标准 |
|------|------|---------|
| UG-1 | **精确到秒的时间点恢复**：用 PG PITR（`pg_basebackup` + WAL 归档）替代 `pg_dump`，支持恢复到任意时间点 | 可指定恢复目标时间（`recovery_target_time`），恢复后数据与该时间点一致（误差 < 1s）；RPO ≤ WAL 归档间隔 |
| UG-2 | **MinIO 备份升级为 mc mirror**：用 `mc mirror` 替代逐对象下载打包，提升大对象集的备份效率与一致性 | `mc mirror` 原子快照 MinIO bucket，备份耗时较 S3Repository 逐对象下载降低 ≥ 40% |
| UG-3 | **联合时间戳**：PG 基础备份与 MinIO mirror 打同一时间戳，保证两者时序一致 | 每次联合备份的 `backup_record` 同时记录 PG basebackup 时间戳和 MinIO mirror 时间戳，两者差值 < 5s |
| UG-4 | **联合恢复保证引用完整性**：先恢复 MinIO 对象再恢复 PG，保证 PG 中引用的 MinIO 对象已就位 | 恢复后冒烟查询包含"PG 记录引用的 MinIO 对象全部存在"校验，失败则中止 |
| UG-5 | **备份独立于应用层**：WAL 归档在 PG 容器内配置（`archive_command`），不依赖 API/Worker 容器存活 | 停止 API + Worker 容器后，PG 容器仍持续归档 WAL；恢复时无需 API 容器即可执行 PITR |

---

## 2. 变更范围

### 2.1 改什么

| 模块 | 变更内容 | 影响文件 |
|------|---------|---------|
| **PG 容器配置** | 新增 WAL 归档配置（`archive_mode=on`、`archive_command`、`wal_level=replica`）；挂载 WAL 归档目录 | `compose.yaml`（postgres service） |
| **PG 基础备份** | `pg_dump` → `pg_basebackup`，产出物理基础备份（`base.tar.gz` + `pg_wal/`） | `deployments/compose/backup.py`（`_dump_database` → `_basebackup`） |
| **PG 恢复** | `pg_restore` → PITR 恢复流程（配置 `recovery.signal` + `restore_command` + `recovery_target_time`，启动 PG 自动 replay WAL） | `deployments/compose/restore.py`（`_restore_database` → `_pitr_restore`） |
| **MinIO 备份** | S3Repository 逐对象下载 → `mc mirror` 原子快照 | `deployments/compose/backup.py`（`_export_minio_objects` → `_mc_mirror_minio`） |
| **MinIO 恢复** | S3Repository 逐对象上传 → `mc mirror --overwrite` 回放 | `deployments/compose/restore.py`（`_restore_minio_objects` → `_mc_restore_minio`） |
| **联合恢复顺序** | 恢复流程从"PG → MinIO"改为"MinIO → PG"，保证引用完整性 | `deployments/compose/restore.py`（`restore()` 主流程顺序调整） |
| **Manifest 结构** | 新增 PITR 相关字段（basebackup 时间戳、WAL 归档范围、MinIO mirror 时间戳、恢复目标时间） | `deployments/compose/backup_manifest.py`（`BackupManifest` 扩展） |
| **BackupRecord 模型** | 新增 PITR 元数据字段 | `packages/backups/entities.py`、新增 Alembic 迁移 |
| **备份目录结构** | 从单 tar 包改为 PG basebackup 目录 + WAL 归档目录 + MinIO mirror 目录联合存储 | `deployments/compose/backup.py` |
| **恢复参数** | API 支持指定 `recovery_target_time`（PITR 时间点） | `apps/api/routers/backups.py`、前端恢复对话框 |
| **mc 工具** | Worker/API 容器需安装 MinIO `mc` 客户端 | `deployments/compose/api.Dockerfile` |
| **Celery beat 调度** | `daily-backup` 任务触发联合备份（PG basebackup + mc mirror 同一时间戳） | `apps/worker/celery_app.py` |

### 2.2 不改什么

| 模块 | 保持不变 | 理由 |
|------|---------|------|
| 备份类型体系 | `daily` / `milestone` / `pre_restore` 三类不变 | 业务语义不变，仅底层技术替换 |
| 保留策略 | daily 14 天滚动、milestone 永久、pre_restore 7 天 | 与基线 PRD 一致 |
| 备份/恢复异步作业模式 | Job + Outbox + Celery → Worker handler 执行 | 编排层不变，handler 内部逻辑替换 |
| 权限控制 | `system:manage`（`platform_administrator`） | 安全模型不变 |
| 前端页面结构 | `/governance` → "数据库备份" Tab，每日镜像 + 里程碑两个子 Tab | UI 框架不变，新增恢复时间点选择 |
| 回滚前自动备份 | `pre_restore` 安全网机制 | 逻辑不变，底层替换为 PITR 快备份 |
| 里程碑手动备份入口 | API `POST /api/v1/backups` + UI 创建对话框 | 接口契约不变 |
| age 加密 | 备份包可选 age 加密 | 加密层不变，作用于打包后的归档 |
| 审计日志 | 复用现有 audit 机制 | 恢复操作仍写审计 |
| 清理策略 | Celery beat `backup-retention-cleanup` 定时清理 | 逻辑不变，清理对象调整为新目录结构 |

---

## 3. 新增/修改的需求项

### 3.1 P0 — 必须完成

| 编号 | 标题 | 描述 |
|------|------|------|
| P0-UP-01 | PG 容器 WAL 归档配置 | PG 容器启用 `archive_mode=on`、`wal_level=replica`，配置 `archive_command` 将 WAL 段归档到共享卷（`/backups/wal_archive/`），归档目录挂载到宿主机，独立于 API 容器存活 |
| P0-UP-02 | pg_basebackup 替代 pg_dump | 备份时使用 `pg_basebackup -D <target> -Ft -z -P -X stream` 生成物理基础备份（`base.tar.gz` + `pg_wal.tar.gz`），替代 `pg_dump --format=custom` |
| P0-UP-03 | mc mirror 替代 S3Repository 导出 | MinIO 备份改用 `mc mirror --overwrite <source>/<bucket> <target_dir>` 原子快照 bucket 全部对象，替代逐对象列举+下载 |
| P0-UP-04 | 联合时间戳 | 单次联合备份中，PG basebackup 与 MinIO mirror 使用同一 `backup_timestamp`（UTC ISO 8601，精确到毫秒），记录到 `BackupManifest` 和 `BackupRecord` |
| P0-UP-05 | PITR 恢复流程 | 恢复时：① 将 basebackup 解压到 PG data 目录 → ② 配置 `recovery.signal` + `restore_command`（从 WAL 归档目录读取）+ `recovery_target_time` → ③ 启动 PG 自动 replay WAL 至目标时间点 → ④ 提升为 primary |
| P0-UP-06 | 联合恢复顺序：MinIO → PG | 恢复流程调整为先 `mc mirror --overwrite` 恢复 MinIO 对象，再执行 PG PITR，保证 PG 中引用的 MinIO 对象已就位 |
| P0-UP-07 | 恢复目标时间参数 | API `POST /api/v1/backups/{id}/restore` 新增可选参数 `recovery_target_time`（ISO 8601），不传时默认恢复到备份时间点（全量恢复） |
| P0-UP-08 | 引用完整性冒烟校验 | 恢复后冒烟查询新增"PG 记录引用的 MinIO 对象全部存在"校验：查询 DB 中存储 MinIO object key 的列（如 `artifact_blob.storage_key`），逐 key 检查 MinIO bucket 中对象存在，任一缺失则中止恢复并报错 |
| P0-UP-09 | BackupRecord 模型扩展 | `backup_record` 表新增字段：`backup_timestamp TIMESTAMPTZ`（联合时间戳）、`wal_start_lsn TEXT`（WAL 起始 LSN）、`wal_end_lsn TEXT`（WAL 结束 LSN）、`recovery_target_time TIMESTAMPTZ NULL`（恢复时填写）、`backup_method VARCHAR(20) DEFAULT 'pitr'`（标识备份方法） |
| P0-UP-10 | 备份目录结构变更 | 每个备份目录结构调整为：`{backup_id}/pg_basebackup/base.tar.gz` + `{backup_id}/pg_basebackup/pg_wal.tar.gz` + `{backup_id}/minio_mirror/` + `{backup_id}/manifest.json` |
| P0-UP-11 | mc 客户端安装 | API/Worker 容器 Dockerfile 安装 MinIO `mc` 客户端二进制，确保备份/恢复时可用 |
| P0-UP-12 | manifest 扩展 | `BackupManifest` 新增字段：`backup_timestamp`、`pg_basebackup_sha256`、`pg_wal_sha256`、`minio_mirror_sha256`、`minio_mirror_object_count`、`wal_start_lsn`、`wal_end_lsn`；`format_version` 升级至 2（向后兼容 v1 的 pg_dump manifest） |

### 3.2 P1 — 重要

| 编号 | 标题 | 描述 |
|------|------|------|
| P1-UP-01 | 恢复时间点选择器 | 前端恢复对话框新增"恢复到指定时间点"选项：日期时间选择器 + 时间轴可视化（标注可用恢复区间，从 basebackup 时间到最新 WAL 归档时间） |
| P1-UP-02 | WAL 归档健康监控 | 治理页 SystemHealth 新增"WAL 归档状态"检查项：展示最近 WAL 归档时间、归档延迟（last_archived_wal 时间 vs 当前时间），延迟超过阈值（如 5 分钟）标红 |
| P1-UP-03 | PITR 恢复进度展示 | 恢复进行中时，UI 展示当前阶段（MinIO 恢复中 / PG basebackup 解压中 / WAL replay 中 / 冒烟校验中），复用 Job 轮询机制读取阶段信息 |
| P1-UP-04 | WAL 归档保留策略 | WAL 归档目录配置保留策略：仅保留覆盖现有 daily 备份恢复区间所需的 WAL 段；与 basebackup 关联的 WAL 在对应 daily 备份过期后清理 |
| P1-UP-05 | 旧格式备份兼容恢复 | 支持 `format_version=1`（pg_dump 格式）的旧备份仍可通过新 restore 流程恢复（检测 manifest 版本，走 pg_restore 旧路径），保证升级前创建的备份仍可用 |
| P1-UP-06 | mc mirror 排除规则 | `mc mirror` 支持配置排除前缀（如 `--exclude "tmp/*"`），避免备份临时对象 |

### 3.3 P2 — 后续

| 编号 | 标题 | 描述 |
|------|------|------|
| P2-UP-01 | 增量基础备份 | 利用 `pg_basebackup` 的增量备份能力（PG 17+ `--incremental`），减少每日基础备份体积 |
| P2-UP-02 | WAL 流式归档 | 从 `archive_command` 文件拷贝升级为 `pg_receivewal` 流式归档，降低 RPO |
| P2-UP-03 | 跨节点 PITR 演练 | 一键在隔离 Compose 项目中执行 PITR 演练，验证可恢复性 |
| P2-UP-04 | 恢复预检查 | 恢复前自动检查 WAL 归档完整性（所需 WAL 段是否全部存在），缺失则提前告警 |

---

## 4. 与现有功能的兼容性说明

### 4.1 旧备份格式兼容

| 场景 | 处理方式 |
|------|---------|
| 升级前创建的 pg_dump 格式备份（`format_version=1`） | 新版 `RestoreService` 检测 `manifest.format_version`：v1 走旧路径（`pg_restore` + S3Repository 上传），v2 走新路径（PITR + mc mirror）。旧备份的恢复功能不受影响 |
| 升级前创建的 `backup_record` 记录 | `backup_method` 字段新增默认值 `'pg_dump'`（迁移时回填），UI 展示备份方法标签区分 |
| 旧备份目录结构（`database.dump` + `objects/`） | 保留不动；新备份使用新目录结构（`pg_basebackup/` + `minio_mirror/`） |

### 4.2 API 契约兼容

| 端点 | 兼容性 |
|------|--------|
| `POST /api/v1/backups` | 请求体不变（`type` + `name` + `description`），底层自动使用 PITR 方式备份；响应新增 `backup_method` 字段 |
| `GET /api/v1/backups` | 响应新增 `backup_method` + `backup_timestamp` 字段，旧字段保留 |
| `POST /api/v1/backups/{id}/restore` | 请求体新增可选 `recovery_target_time` 字段；不传时默认恢复到备份时间点（行为等价旧版全量恢复） |
| `DELETE /api/v1/backups/{id}` | 不变 |
| `GET /api/v1/backups/stats` | 不变 |

### 4.3 数据库迁移兼容

| 项目 | 说明 |
|------|------|
| 新增 Alembic 迁移 | `00xx_alter_backup_record_pitr.py`：ALTER TABLE `backup_record` ADD COLUMN `backup_timestamp`、`wal_start_lsn`、`wal_end_lsn`、`recovery_target_time`、`backup_method`；回填 `backup_method='pg_dump'`（存量记录） |
| PG 容器配置变更 | 首次启动时需以 `archive_mode=on` 重新初始化或修改 `postgresql.conf`；现有 `pgdata` 卷需执行一次性配置更新（`ALTER SYSTEM SET archive_mode='on'` + 重启） |

### 4.4 部署兼容

| 项目 | 说明 |
|------|------|
| Docker Compose | `postgres` service 新增 WAL 归档卷挂载 + 环境变量配置；`api`/`worker`/`backup`/`restore` service 新增 `mc` 客户端 |
| 环境变量 | 新增：`IRIP_WAL_ARCHIVE_DIR`（WAL 归档目录）、`IRIP_MINIO_MC_ALIAS`（mc alias 名）、`IRIP_PG_REPLICATION_SLOT`（可选复制槽名）。已有环境变量不变 |
| 单机部署 | WAL 归档目录挂载到宿主机（`./backups/wal_archive`），与备份目录同级 |
| 集群部署 | WAL 归档可配置为写入 MinIO（`archive_command` 调用 `mc cp`），与单机部署通过环境变量切换 |

### 4.5 前端兼容

| 项目 | 说明 |
|------|------|
| 备份列表 | 新增"备份方法"列（Tag：PITR=blue / pg_dump=gray），旧备份标灰 |
| 恢复对话框 | 新增"恢复到指定时间点"开关 + 时间选择器，默认关闭（等价全量恢复） |
| 里程碑创建 | 不变 |

---

## 5. 待确认问题

| 编号 | 问题 | 影响范围 | 建议倾向 |
|------|------|---------|---------|
| QU-01 | WAL 归档存储位置：本地卷 vs MinIO？ | 部署灵活性、RPO | 单机用本地卷（延迟低），集群用 MinIO（可共享）；`archive_command` 通过环境变量切换 |
| QU-02 | `pg_basebackup` 使用复制槽还是临时复制连接？ | PG 配置、连接管理 | 建议使用命名复制槽（`--slot`），便于追踪 WAL 保留；但需管理复制槽生命周期（删除备份时清理） |
| QU-03 | 恢复目标时间是否需要校验 WAL 归档覆盖范围？ | 恢复成功率、用户体验 | 建议恢复前预检查：`recovery_target_time` 必须在 [basebackup 时间, 最新 WAL 归档时间] 区间内，否则拒绝并提示可用区间 |
| QU-04 | `mc mirror` 备份 MinIO 时如何处理大对象（如 > 5GB）？ | 备份完整性 | `mc mirror` 原生支持 multipart upload，无需特殊处理；但需确认 `mc` 版本支持 mirror 的 `--md5` 校验选项 |
| QU-05 | PITR 恢复是否需要停机（维护模式）？ | 用户体验、数据一致性 | 建议恢复期间进入只读维护模式（与基线 PRD Q2 一致），恢复后解除；PITR 恢复本身需停 PG 实例 |
| QU-06 | WAL 归档目录的磁盘空间管理策略？ | 存储成本、可靠性 | 建议设置 WAL 归档保留窗口（如 14 天，与 daily 保留一致），超期 WAL 自动清理；需避免清理仍在恢复区间内的 WAL |
| QU-07 | 引用完整性冒烟校验的范围：仅 artifact_blob 还是所有引用 MinIO 的表？ | 校验覆盖率、恢复速度 | 建议 P0 覆盖核心引用表（artifact_blob 等），P2 扩展为全表扫描可配置 |
| QU-08 | 是否保留 `pg_dump` 作为 milestone 备份的备选方案？ | 备份速度 vs 恢复灵活性 | 建议统一使用 PITR（一致性更好）；如需 pg_dump 可作为 P2 可配置选项 |
| QU-09 | 联合时间戳的时钟同步要求：PG 容器与 MinIO 容器时钟偏差容忍度？ | 时间戳一致性 | 容器间应配置 NTP 同步；联合时间戳以 PG 容器时钟为准，MinIO mirror 在 PG basebackup 完成后立即执行 |
| QU-10 | 升级过渡期：是否需要同时保留 pg_dump 和 PITR 两套备份并行运行一段时间？ | 升级风险 | 建议升级后并行运行 1 个保留周期（14 天），验证 PITR 可靠性后停用 pg_dump 路径 |
