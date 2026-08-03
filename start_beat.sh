#!/bin/bash
# IRIP Beat 调度器启动脚本
# 用法: bash start_beat.sh
# 启动 Celery Beat 调度器进程，自动加载 .env 环境变量。
# Beat 进程负责按 beat_schedule 配置定时触发调度任务。

set -e

# M-12: 基于脚本自身目录定位项目根目录，不硬编码个人路径
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

# 加载 .env 环境变量
set -a && source .env && set +a

# 启动 Beat 调度器
exec .venv/bin/celery -A apps.worker.celery_app beat --loglevel=info
