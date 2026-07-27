# CLI 组件沙箱镜像
#
# 用于在隔离的容器环境中执行 CLI 组件，提供安全边界：
# - 非 root 用户执行
# - 最小依赖（仅 Python 运行时 + 组件所需包）
# - 配合 compose network 配置实现无网络
# - 配合 compose 配置实现只读文件系统
#
# 使用方式：
#   在 docker-compose 中定义 cli-sandbox 服务，
#   通过 docker exec 或共享卷传递 input/output JSON 文件。
#
# 安全措施（技术设计文档 F-13）：
# 1. 非 root 用户（irip-cli，UID 2000）
# 2. 无 sudo/curl/wget 等网络工具
# 3. 最小 Python 依赖
# 4. 工作目录 /tmp/component-work（tmpfs，自动清理）
# 5. 只读根文件系统（通过 compose 配置 read_only: true）

FROM docker.m.daocloud.io/python:3.12-slim

# 元数据
LABEL maintainer="IRIP Team"
LABEL description="IRIP CLI component sandbox - minimal isolated execution environment"
LABEL security="non-root, no-network-tools, minimal-deps"

# 创建非 root 用户
RUN groupadd -r -g 2000 irip-cli && \
    useradd -r -u 2000 -g 2000 -d /home/irip-cli -s /bin/bash irip-cli && \
    mkdir -p /home/irip-cli /tmp/component-work && \
    chown -R irip-cli:irip-cli /home/irip-cli /tmp/component-work

# 安装最小系统依赖（仅 ca-certificates 用于 HTTPS，无网络工具）
# 不安装 curl/wget/ssh/nc 等网络工具
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/* && \
    apt-get clean

# 安装最小 Python 依赖
# CLI 组件通过 JSON 文件通信，仅需标准库 + jsonschema 做参数校验
RUN pip install --no-cache-dir \
        jsonschema>=4,<5

# 设置工作目录
WORKDIR /tmp/component-work

# 切换到非 root 用户
USER irip-cli

# 默认入口：等待 input.json 并执行命令
# 通信协议：
#   input.json  -> {"params": {...}, "context": {...}, "command": [...]}
#   output.json -> {"outputs": {...}, "summary": "...", ...}
ENTRYPOINT ["python3", "-c"]
