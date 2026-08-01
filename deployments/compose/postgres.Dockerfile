# IRIP PostgreSQL 16 + pgvector
# 基于 pgvector 官方镜像，自带 vector 扩展。
# 固定 tag 禁用 latest（架构文档 §6.3）。
# PITR: WAL 归档配置通过 compose.yaml command 参数注入，无需修改 postgresql.conf。
FROM pgvector/pgvector:pg16

# 可选: COPY 自定义 PITR 入口脚本（如使用 pg_pitr_entrypoint.sh 方案）
# COPY deployments/compose/pg_pitr_entrypoint.sh /usr/local/bin/pg_pitr_entrypoint.sh
# RUN chmod +x /usr/local/bin/pg_pitr_entrypoint.sh
