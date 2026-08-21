#!/usr/bin/env bash
# P1-T7: 检查恢复测试所需工具是否安装。
# 在 CI 和发布门中调用，工具缺失时以非零退出码阻断，禁止隐式跳过恢复测试。
set -euo pipefail

REQUIRED_TOOLS=("pg_basebackup" "pg_restore" "mc" "age")
MISSING=()

for tool in "${REQUIRED_TOOLS[@]}"; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        MISSING+=("$tool")
    fi
done

if [ ${#MISSING[@]} -gt 0 ]; then
    echo "ERROR: Required recovery tools missing: ${MISSING[*]}"
    echo "Install: brew install postgresql@16 minio age"
    exit 1
fi

echo "OK: all recovery tools present"
