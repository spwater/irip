"""本地开发启动助手：先加载 .env 到 os.environ，再执行指定命令。

用法：
    .venv/bin/python run_local.py alembic upgrade head
    .venv/bin/python run_local.py bootstrap
    .venv/bin/python run_local.py uvicorn apps.api.main:app --host 0.0.0.0 --port 8000 --reload
"""
import os
import subprocess
import sys
from dotenv import dotenv_values


def load_env() -> None:
    env = dotenv_values(".env")
    for k, v in env.items():
        if k and v is not None:
            os.environ.setdefault(k, v)


def main() -> None:
    load_env()
    cmd = sys.argv[1:]
    if not cmd:
        print("usage: run_local.py <command> [args...]")
        sys.exit(1)
    # 若第一个参数是 python/python3/-m，替换为当前解释器，保证用 venv
    if cmd[0] in ("python", "python3"):
        cmd[0] = sys.executable
    elif cmd[0] == "-m":
        cmd = [sys.executable, "-m"] + cmd[1:]
    subprocess.run(cmd, cwd=os.getcwd())


if __name__ == "__main__":
    main()
