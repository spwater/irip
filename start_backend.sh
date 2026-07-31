#!/bin/bash
# IRIP 后端启动脚本
cd /Users/shuipei/Desktop/snowSP/irip
set -a && source .env && set +a
exec .venv/bin/uvicorn apps.api.main:app --reload --port 8000
