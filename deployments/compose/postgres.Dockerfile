# IRIP PostgreSQL 16 + pgvector
# 基于 pgvector 官方镜像，自带 vector 扩展。
# 固定 tag 禁用 latest（架构文档 §6.3）。
FROM pgvector/pgvector:pg16
