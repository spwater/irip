#!/bin/bash
# IRIP Worker 启动脚本
# 用法: bash start_worker.sh
# 启动 Celery Worker 进程，自动加载 .env 环境变量。
# Worker 启动后通过 worker_process_init signal 自动在 9100 端口提供健康检查端点。

set -e

# M-12: 基于脚本自身目录定位项目根目录，不硬编码个人路径
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

# 加载 .env 环境变量
set -a && source .env && set +a

# 启动 Worker
exec .venv/bin/celery -A apps.worker.celery_app worker --loglevel=info --concurrency=2
