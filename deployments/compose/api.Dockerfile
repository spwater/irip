# IRIP API / Worker / Bootstrap Dockerfile
# 镜像固定 tag：python:3.12-slim-bookworm（架构文档 §6.3）
# pip 走清华源（架构文档 §6.1）

FROM docker.m.daocloud.io/python:3.12-slim-bookworm

# 系统依赖（libpq-dev for psycopg, build-essential for C extensions）
# 使用阿里云 Debian 镜像源加速
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list 2>/dev/null || true && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
      libpq-dev gcc curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先复制依赖清单，利用 Docker 层缓存
COPY pyproject.toml alembic.ini ./
COPY packages/ ./packages/
COPY apps/ ./apps/
COPY migrations/ ./migrations/

# 安装 Python 依赖（清华源）
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -e ".[dev]"

# 默认启动 API 服务（worker/scheduler/bootstrap 通过 command 覆盖）
CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
