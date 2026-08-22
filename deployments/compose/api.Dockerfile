# IRIP API / Worker / Bootstrap Dockerfile (multi-stage)
# 镜像固定 tag：python:3.12-slim-bookworm（架构文档 §6.3）
# pip 走中科大镜像（架构文档 §6.1）；基础镜像走 DaoCloud（科大 Docker Hub 镜像已下线）
#
# 多阶段拆分（deploy: split minimal runtime and operations images）：
#   - builder: 安装编译依赖 + 构建 Python 包（含 C 扩展）
#   - runtime: 仅包含 Python 运行时 + 应用包 + non-root user
# 运行时镜像不含 docker CLI / pg_dump / pg_restore / mc / age，
# 这些运维工具迁移至 deployments/compose/ops.Dockerfile。

# ============================================================
# Stage 1: builder —— 编译 C 扩展并安装 Python 依赖
# ============================================================
FROM docker.m.daocloud.io/python:3.12-slim-bookworm AS builder

# 系统依赖：libpq-dev（psycopg 编译）、gcc（C 扩展编译）
# 使用阿里云 Debian 镜像源加速
RUN sed -i 's|deb.debian.org|mirrors.ustc.edu.cn|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's|deb.debian.org|mirrors.ustc.edu.cn|g' /etc/apt/sources.list 2>/dev/null || true && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
      libpq-dev gcc && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先复制依赖清单，利用 Docker 层缓存
COPY pyproject.toml alembic.ini ./
COPY packages/ ./packages/
COPY apps/ ./apps/
COPY migrations/ ./migrations/
COPY schemas/ ./schemas/

# 安装 Python 依赖到独立前缀 /install，便于 runtime 阶段精确拷贝
# BuildKit 缓存挂载：pip 下载的包跨构建持久化
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -i https://mirrors.ustc.edu.cn/pypi/simple/ --trusted-host mirrors.ustc.edu.cn \
      --prefix=/install -e .

# ============================================================
# Stage 2: runtime —— 最小化运行时镜像
# ============================================================
FROM docker.m.daocloud.io/python:3.12-slim-bookworm AS runtime

# 运行时仅需 libpq 共享库（psycopg 运行时链接），无需 gcc/libpq-dev
RUN sed -i 's|deb.debian.org|mirrors.ustc.edu.cn|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's|deb.debian.org|mirrors.ustc.edu.cn|g' /etc/apt/sources.list 2>/dev/null || true && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
      libpq5 curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 从 builder 拷贝已安装的 Python 依赖（site-packages + bin）
COPY --from=builder /install /usr/local

# 拷贝应用源码（pip install -e . 产生的 .egg-link 指向 /app，需保留源码）
COPY pyproject.toml alembic.ini ./
COPY packages/ ./packages/
COPY apps/ ./apps/
COPY migrations/ ./migrations/
COPY schemas/ ./schemas/
COPY config/ ./config/

# 复制部署脚本（bootstrap 入口），放在拷贝依赖之后以利用层缓存
COPY deployments/ ./deployments/

# 默认启动 API 服务（worker/scheduler/bootstrap 通过 command 覆盖）
CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

# 非 root 运行（F-12 安全要求）
RUN groupadd -r irip && useradd -r -g irip -u 1000 -s /sbin/nologin irip && \
    chown -R irip:irip /app
USER 1000
