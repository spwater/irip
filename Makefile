# IRIP 平台质量入口
# 用法：make lint | make test-unit | make test-integration | make web-test
# 约定：Python 走项目内 .venv；前端走 corepack 启用的 pnpm。

PY := .venv/bin/python
PYTEST := .venv/bin/python -m pytest
RUFF := .venv/bin/python -m ruff
MYPY := .venv/bin/python -m mypy
PNPM := pnpm

.PHONY: help lint format-check typecheck test-unit test-integration test-security test-recovery test-contract test-acceptance web-test web-build install-dev

help: ## 显示可用目标
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

install-dev: ## 安装 Python dev 依赖（清华源）
	$(PY) -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -e ".[dev]"

lint: ## ruff 静态检查 + 格式检查（apps packages tests）— 范围与 CI/release-gate 一致（F-24）
	$(RUFF) check apps packages tests
	$(RUFF) format --check apps packages tests

format-check: ## ruff format 检查（单独运行，F-24）
	$(RUFF) format --check apps packages tests

typecheck: ## mypy 严格类型检查（与 CI 一致：packages + apps/api + apps/worker）
	$(MYPY) packages apps/api apps/worker

test-unit: ## 单元测试（不含集成）
	$(PYTEST) tests/unit -v

test-integration: ## 集成测试（需要外部依赖容器）
	$(PYTEST) tests/integration -v

test-security: ## 安全测试（SQL注入/SSRF/路径穿越/令牌重放等）
	$(PYTEST) tests/security -v

test-recovery: ## 恢复测试（重复投递/Redis丢失/MinIO中断/备份恢复）
	$(PYTEST) tests/recovery -v

test-contract: ## 契约测试（API 契约一致性）
	$(PYTEST) tests/contract -v

test-acceptance: ## 验收测试（端到端用户场景）
	$(PYTEST) tests/acceptance -v

web-test: ## 前端单元测试
	$(PNPM) --dir apps/web test --run

web-build: ## 前端生产构建
	$(PNPM) --dir apps/web build
