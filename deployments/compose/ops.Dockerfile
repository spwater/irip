# IRIP Ops Dockerfile (backup / restore / PITR 运维镜像)
# 继承应用 Python 运行时环境，叠加运维工具：
#   - PostgreSQL 16 client（pg_dump / pg_restore / pg_basebackup）
#   - MinIO mc 客户端（mc mirror 备份/恢复对象存储）
#   - age 加密工具（备份产物加密）
# 不含 Docker CLI（PITR 恢复改由专用恢复流程控制 PG 容器，不挂载 docker.sock）。
#
# 镜像固定 tag：python:3.12-slim-bookworm（架构文档 §6.3）
# pip 走中科大镜像（架构文档 §6.1）；基础镜像走 DaoCloud（科大 Docker Hub 镜像已下线）

# ============================================================
# Stage 1: builder —— 编译 C 扩展并安装 Python 依赖（与 api.Dockerfile 对齐）
# ============================================================
FROM docker.m.daocloud.io/python:3.12-slim-bookworm AS builder

RUN sed -i 's|deb.debian.org|mirrors.ustc.edu.cn|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's|deb.debian.org|mirrors.ustc.edu.cn|g' /etc/apt/sources.list 2>/dev/null || true && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
      libpq-dev gcc && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml alembic.ini ./
COPY packages/ ./packages/
COPY apps/ ./apps/
COPY migrations/ ./migrations/
COPY schemas/ ./schemas/

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -i https://mirrors.ustc.edu.cn/pypi/simple/ --trusted-host mirrors.ustc.edu.cn \
      --prefix=/install -e .

# ============================================================
# Stage 2: ops —— 应用 Python 环境 + 运维工具
# ============================================================
FROM docker.m.daocloud.io/python:3.12-slim-bookworm AS ops

# 运行时系统依赖 + libpq 共享库
RUN sed -i 's|deb.debian.org|mirrors.ustc.edu.cn|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's|deb.debian.org|mirrors.ustc.edu.cn|g' /etc/apt/sources.list 2>/dev/null || true && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
      libpq5 curl ca-certificates gnupg age && \
    rm -rf /var/lib/apt/lists/*

# PostgreSQL 16 client（pg_dump / pg_restore / pg_basebackup，与 pgvector:pg16 服务端版本对齐）
# 使用 PostgreSQL 官方 APT 仓库（signed-by 规范写法，与 CI .github/workflows/ci.yml 对齐）
RUN . /etc/os-release && \
    install -d /usr/share/postgresql-common/pgdg && \
    curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
      | gpg --dearmor -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.gpg && \
    echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.gpg] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" \
      > /etc/apt/sources.list.d/pgdg.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
      postgresql-client-16 && \
    rm -rf /var/lib/apt/lists/*

# MinIO mc 客户端 —— 从官方 minio/mc 镜像 COPY 二进制，绕过 dl.min.io 直接下载
# （本地网络到 dl.min.io/GitHub 不可达）。用 latest（本地已缓存 RELEASE.2025-08-13）：
# 具体 tag 在 DaoCloud 无缓存、需回源 Docker Hub 会卡死。版本 pin 在此让步给本地
# 可达性；在 CI/有稳定外网的环境可改回 pin 具体 tag（如 RELEASE.2024-11-17T19-35-25Z）。
COPY --from=docker.m.daocloud.io/minio/mc /usr/bin/mc /usr/local/bin/mc
RUN chmod 0755 /usr/local/bin/mc

# age 加密工具 —— 已并入上方 APT 安装（Debian bookworm 官方仓库 `age` 包，
# 版本 1.1.1，走科大镜像源，绕过 GitHub release 下载）。提供
# /usr/bin/age + /usr/bin/age-keygen。

WORKDIR /app

# 从 builder 拷贝已安装的 Python 依赖
COPY --from=builder /install /usr/local

# 拷贝应用源码
COPY pyproject.toml alembic.ini ./
COPY packages/ ./packages/
COPY apps/ ./apps/
COPY migrations/ ./migrations/
COPY schemas/ ./schemas/

# 复制部署脚本（backup / restore 入口）
COPY deployments/ ./deployments/

# 默认启动 backup（restore 通过 command 覆盖）
CMD ["python", "-m", "deployments.compose.backup"]

# 非 root 运行（F-12 安全要求）
RUN groupadd -r irip && useradd -r -g irip -u 1000 -s /sbin/nologin irip && \
    chown -R irip:irip /app
USER 1000
