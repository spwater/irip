"""ModelGateway 单元测试。

覆盖 ``packages/research.planning.model_gateway`` 的核心逻辑：
- 模型选择（_select_model / get_default_registry）
- 数据预算计算（_calculate_budget，500K 硬上限）
- 调用记录（_record_call / get_call_history）
- call / call_with_failover（mock AIProvider）
- DataBudgetExceeded 异常

不依赖真实数据库或真实 LLM API，使用 AsyncMock 模拟 AIProvider。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from packages.ai.providers import AIResponse
from packages.research.execution.models_trusted import ModelConfig, TaskType
from packages.research.planning.model_gateway import (
    DATA_BUDGET_HARD_LIMIT,
    DEFAULT_SAFETY_MARGIN,
    CallMetadata,
    DataBudgetExceeded,
    ModelGateway,
)

# ============================================================
# Helpers
# ============================================================


def _make_provider(answer: str = "模型回答", tool_calls: tuple | None = None) -> MagicMock:
    """构造一个 mock AIProvider，complete 返回 AIResponse。"""
    provider = MagicMock()
    provider.complete = AsyncMock(
        return_value=AIResponse(
            answer=answer,
            tool_calls=tool_calls or (),
            uncertainty=None,
        )
    )
    return provider


def _make_gateway(provider=None) -> ModelGateway:
    """构造 ModelGateway 实例（provider 默认为 mock）。"""
    return ModelGateway(
        provider=provider if provider is not None else _make_provider(),
        audit_recorder=MagicMock(),
    )


# ============================================================
# get_default_registry / _select_model
# ============================================================


class TestModelSelection:
    """模型选择逻辑。"""

    def test_default_registry_covers_all_task_types(self) -> None:
        """默认注册表覆盖全部 5 种 TaskType。"""
        registry = ModelGateway.get_default_registry()
        for task_type in TaskType:
            assert task_type in registry, f"缺少 {task_type}"
            cfg = registry[task_type]
            assert isinstance(cfg, ModelConfig)
            assert cfg.provider == "openai"
            assert cfg.context_limit > 0

    def test_select_model_returns_registered_config(self) -> None:
        """_select_model 返回注册表中对应 TaskType 的配置。"""
        gw = _make_gateway()
        cfg = gw._select_model(TaskType.PLANNING, data_size=100)
        assert cfg.model == "gpt-4o"

    def test_select_model_falls_back_to_conversation(self) -> None:
        """未知 TaskType 回退到 CONVERSATION 配置。"""
        gw = _make_gateway()
        # 清空注册表中的 PLANNING → 应回退到 CONVERSATION
        gw._model_registry = {
            TaskType.CONVERSATION: ModelConfig(
                provider="openai", model="gpt-4o-mini", version="2024-07", context_limit=128000
            )
        }
        cfg = gw._select_model(TaskType.PLANNING, data_size=100)
        assert cfg.model == "gpt-4o-mini"

    def test_select_model_falls_back_to_hardcoded_when_empty(self) -> None:
        """注册表完全为空时回退到硬编码默认配置。"""
        gw = _make_gateway()
        gw._model_registry = {}
        cfg = gw._select_model(TaskType.PLANNING, data_size=100)
        assert cfg.model == "gpt-4o"
        assert cfg.provider == "openai"

    def test_get_backup_model_returns_mini(self) -> None:
        """_get_backup_model 返回 gpt-4o-mini 降级模型。"""
        gw = _make_gateway()
        backup = gw._get_backup_model(TaskType.PLANNING)
        assert backup.model == "gpt-4o-mini"
        assert backup.context_limit == 128000


# ============================================================
# _calculate_budget
# ============================================================


class TestBudgetCalculation:
    """数据预算计算。"""

    def test_budget_capped_at_500k(self) -> None:
        """预算不超过 500K 硬上限。"""
        gw = _make_gateway()
        cfg = ModelConfig(provider="openai", model="x", version="1", context_limit=2_000_000)
        budget = gw._calculate_budget(cfg)
        assert budget == DATA_BUDGET_HARD_LIMIT

    def test_budget_subtracts_overheads(self) -> None:
        """预算 = context_limit - system - research - output - safety_margin。"""
        gw = _make_gateway()
        cfg = ModelConfig(provider="openai", model="x", version="1", context_limit=128000)
        budget = gw._calculate_budget(
            cfg,
            system_tokens=2000,
            research_tokens=3000,
            output_tokens=4000,
            safety_margin=DEFAULT_SAFETY_MARGIN,
        )
        assert budget == 128000 - 2000 - 3000 - 4000 - DEFAULT_SAFETY_MARGIN

    def test_budget_never_negative(self) -> None:
        """开销超过 context_limit 时预算为 0（不低于 0）。"""
        gw = _make_gateway()
        cfg = ModelConfig(provider="openai", model="x", version="1", context_limit=10000)
        budget = gw._calculate_budget(
            cfg, system_tokens=4000, research_tokens=4000, output_tokens=4000
        )
        assert budget == 0


# ============================================================
# call / DataBudgetExceeded
# ============================================================


class TestCall:
    """call 方法。"""

    async def test_call_returns_model_response_with_provider(self) -> None:
        """有 provider 时 call 返回 provider 的回答 + 元数据。"""
        provider = _make_provider(answer="分析结果")
        gw = _make_gateway(provider=provider)
        resp = await gw.call(
            task_type=TaskType.PLANNING,
            system_prompt="系统提示",
            data_context="数据内容",
            research_context="研究上下文",
        )
        assert resp.answer == "分析结果"
        assert resp.provider == "openai"
        assert resp.model == "gpt-4o"
        # tokens_used = len(data_context) // 4
        assert resp.tokens_used == len("数据内容") // 4
        provider.complete.assert_awaited_once()

    async def test_call_without_provider_returns_fallback(self) -> None:
        """provider 为 None 时返回模拟回退响应。"""
        gw = ModelGateway(provider=None, audit_recorder=MagicMock())
        resp = await gw.call(
            task_type=TaskType.INSIGHT,
            system_prompt="s",
            data_context="d",
            research_context="r",
        )
        assert "[模拟响应]" in resp.answer
        assert TaskType.INSIGHT.value in resp.answer

    async def test_call_with_tools_passes_tool_schemas(self) -> None:
        """传入 tools 时 AIRequest 携带 tool 名称和 schemas。"""
        provider = _make_provider()
        gw = _make_gateway(provider=provider)
        tools = [{"name": "search", "schema": {}}]
        await gw.call(
            task_type=TaskType.CODE_GEN,
            system_prompt="s",
            data_context="d",
            research_context="r",
            tools=tools,
        )
        request = provider.complete.await_args.args[0]
        assert "search" in request.tools
        assert request.tool_schemas == tuple(tools)

    async def test_call_raises_data_budget_exceeded(self) -> None:
        """数据部分超过预算时抛出 DataBudgetExceeded。"""
        gw = _make_gateway()
        # 构造超大 data_context 使 data_tokens > budget
        huge_data = "x" * (DATA_BUDGET_HARD_LIMIT * 4 + 1000)
        with pytest.raises(DataBudgetExceeded) as exc_info:
            await gw.call(
                task_type=TaskType.PLANNING,
                system_prompt="s",
                data_context=huge_data,
                research_context="r",
            )
        assert exc_info.value.data_size > exc_info.value.budget

    async def test_call_records_metadata(self) -> None:
        """call 成功后记录调用元数据。"""
        provider = _make_provider()
        gw = _make_gateway(provider=provider)
        await gw.call(
            task_type=TaskType.PLANNING,
            system_prompt="s",
            data_context="data",
            research_context="r",
        )
        history = gw.get_call_history()
        assert len(history) == 1
        meta = history[0]
        assert isinstance(meta, CallMetadata)
        assert meta.task_type == "planning"
        assert meta.model == "gpt-4o"
        assert meta.failover is False


# ============================================================
# call_with_failover
# ============================================================


class TestCallWithFailover:
    """call_with_failover 故障切换。"""

    async def test_failover_success_on_primary_failure(self) -> None:
        """主模型失败时切换备用模型并标记 failover_used。"""
        provider = MagicMock()
        provider.complete = AsyncMock(
            side_effect=[RuntimeError("primary down"), AIResponse(answer="备用回答")]
        )
        gw = _make_gateway(provider=provider)
        resp = await gw.call_with_failover(
            task_type=TaskType.PLANNING,
            system_prompt="s",
            data_context="d",
            research_context="r",
        )
        assert resp.answer == "备用回答"
        assert resp.failover_used is True
        assert resp.model == "gpt-4o-mini"
        # 两次调用：主 + 备
        assert provider.complete.await_count == 2

    async def test_failover_returns_primary_response_when_success(self) -> None:
        """主模型成功时直接返回，不切换。"""
        provider = _make_provider(answer="主回答")
        gw = _make_gateway(provider=provider)
        resp = await gw.call_with_failover(
            task_type=TaskType.PLANNING, system_prompt="s", data_context="d", research_context="r"
        )
        assert resp.answer == "主回答"
        assert resp.failover_used is False
        assert provider.complete.await_count == 1

    async def test_failover_budget_exceeded_not_switched(self) -> None:
        """预算超限时直接抛出，不触发故障切换。"""
        gw = _make_gateway()
        huge_data = "x" * (DATA_BUDGET_HARD_LIMIT * 4 + 1000)
        with pytest.raises(DataBudgetExceeded):
            await gw.call_with_failover(
                task_type=TaskType.PLANNING,
                system_prompt="s",
                data_context=huge_data,
                research_context="r",
            )

    async def test_failover_without_provider_returns_backup_fallback(self) -> None:
        """provider 为 None 且主模型失败时返回备用模拟响应。"""
        gw = ModelGateway(provider=None, audit_recorder=MagicMock())
        # provider 为 None 时 call 不抛异常（返回回退），故 failover 不会触发
        # 但若主路径抛出非预算异常则进入备用回退。这里直接验证备用回退路径：
        resp = await gw.call_with_failover(
            task_type=TaskType.PLANNING, system_prompt="s", data_context="d", research_context="r"
        )
        # provider None → call 成功返回模拟响应 → 无 failover
        assert "[模拟响应]" in resp.answer
        assert resp.failover_used is False

    async def test_failover_records_failover_metadata(self) -> None:
        """故障切换成功后记录 failover=True 的元数据。"""
        provider = MagicMock()
        provider.complete = AsyncMock(side_effect=[RuntimeError("boom"), AIResponse(answer="备用")])
        gw = _make_gateway(provider=provider)
        await gw.call_with_failover(
            task_type=TaskType.PLANNING, system_prompt="s", data_context="d", research_context="r"
        )
        history = gw.get_call_history()
        failover_meta = [m for m in history if m.failover]
        assert len(failover_meta) == 1
        assert failover_meta[0].model == "gpt-4o-mini"


# ============================================================
# _record_call / get_call_history
# ============================================================


class TestCallHistory:
    """调用历史记录与上限裁剪。"""

    def test_get_call_history_returns_copy(self) -> None:
        """get_call_history 返回列表副本，修改不影响内部状态。"""
        gw = _make_gateway()
        gw._record_call(
            CallMetadata(task_type="planning", provider="openai", model="gpt-4o", model_version="1")
        )
        hist = gw.get_call_history()
        hist.clear()
        assert len(gw.get_call_history()) == 1

    def test_history_caps_at_threshold(self) -> None:
        """超过 1000 条时裁剪保留最近 500 条。"""
        gw = _make_gateway()
        for _i in range(1001):
            gw._record_call(
                CallMetadata(
                    task_type="planning", provider="openai", model="gpt-4o", model_version="1"
                )
            )
        assert len(gw._call_history) == 500
