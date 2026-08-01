# IRIP API / Worker / Bootstrap Dockerfile
# 镜像固定 tag：python:3.12-slim-bookworm（架构文档 §6.3）
# pip 走清华源（架构文档 §6.1）

FROM docker.m.daocloud.io/python:3.12-slim-bookworm

# 系统依赖（libpq-dev for psycopg, build-essential for C extensions）
# postgresql-client-16 for pg_dump/pg_restore（backup/restore 容器需要，与 pgvector:pg16 服务端版本对齐）
# 使用阿里云 Debian 镜像源 + PostgreSQL 官方 APT 仓库
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list 2>/dev/null || true && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
      libpq-dev gcc curl ca-certificates gnupg && \
    . /etc/os-release && \
    echo "deb https://apt.postgresql.org/pub/repos/apt/ ${VERSION_CODENAME}-pgdg main" \
      > /etc/apt/sources.list.d/pgdg.list && \
    curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
      | gpg --dearmor -o /etc/apt/trusted.gpg.d/pgdg.gpg && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
      postgresql-client-16 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先复制依赖清单，利用 Docker 层缓存
COPY pyproject.toml alembic.ini ./
COPY packages/ ./packages/
COPY apps/ ./apps/
COPY migrations/ ./migrations/
COPY schemas/ ./schemas/

# 安装 Python 依赖（阿里云镜像，国内速度快）
# BuildKit 缓存挂载：pip 下载的包跨构建持久化
# --no-cache-dir 避免某些包的缓存哈希校验问题
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com -e .

# 复制部署脚本（bootstrap/backup/restore），放在 pip install 之后以利用层缓存
COPY deployments/ ./deployments/

# PITR: 安装 MinIO mc 客户端（mc mirror 备份/恢复对象存储）
RUN curl -fsSL https://dl.min.io/client/mc/release/linux-amd64/mc \
      -o /usr/local/bin/mc && \
    chmod +x /usr/local/bin/mc

# PITR: 安装 docker CLI（PITR 恢复需通过 docker compose stop/start 控制 PG 容器）
RUN apt-get update && \
    apt-get install -y --no-install-recommends docker.io && \
    rm -rf /var/lib/apt/lists/*

# 默认启动 API 服务（worker/scheduler/bootstrap 通过 command 覆盖）
CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

# 非 root 运行（F-12 安全要求）
RUN groupadd -r irip && useradd -r -g irip -u 1000 -s /sbin/nologin irip
RUN chown -R irip:irip /app
USER 1000
