# IRIP 平台质量入口
# 用法：make lint | make test-unit | make test-integration | make web-test
# 约定：Python 走项目内 .venv；前端走 corepack 启用的 pnpm。

PY := .venv/bin/python
PYTEST := .venv/bin/python -m pytest
RUFF := .venv/bin/python -m ruff
MYPY := .venv/bin/python -m mypy
PNPM := pnpm

.PHONY: help lint format-check typecheck test-unit test-integration web-test web-build install-dev

help: ## 显示可用目标
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

install-dev: ## 安装 Python dev 依赖（清华源）
	$(PY) -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -e ".[dev]"

lint: ## ruff 静态检查 + 格式检查（apps packages tests）— 范围与 CI/release-gate 一致（F-24）
	$(RUFF) check apps packages tests
	$(RUFF) format --check apps packages tests

format-check: ## ruff format 检查（单独运行，F-24）
	$(RUFF) format --check apps packages tests

typecheck: ## mypy 严格类型检查（与 CI 一致：packages + apps/api）
	$(MYPY) packages apps/api

test-unit: ## 单元测试（不含集成）
	$(PYTEST) tests/unit -v

test-integration: ## 集成测试（需要外部依赖容器）
	$(PYTEST) tests/integration -v

web-test: ## 前端单元测试
	$(PNPM) --dir apps/web test --run

web-build: ## 前端生产构建
	$(PNPM) --dir apps/web build
