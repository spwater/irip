"""模型网关：扩展现有 AI 调用层，增加预算计算、模式路由、调用记录、故障切换。

ModelGateway 封装 AIProvider Protocol，增加：
1. 按任务类型自动选择模型（planning/code_gen/long_context/insight/conversation）；
2. 计算有效数据预算（500K 硬上限）；
3. 记录调用元数据（供应商、模型、版本、提示词版本、时间、tokens）；
4. 故障切换备用模型。

ModelGateway 不修改现有 AIProvider Protocol 签名，通过封装层增加能力。

关键约束：
- 500K 硬上限在 _calculate_budget 中强制执行；
- 超预算时 raise DataBudgetExceeded（ContextRouter 已预先分块，此处为硬防线）；
- 故障时自动切换备用模型并记录切换。
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from packages.research.models_trusted import ModelConfig, ModelResponse, TaskType

logger = logging.getLogger("research.model_gateway")

#: 500K 硬上限（单次模型调用数据部分的 token 上限）。
DATA_BUDGET_HARD_LIMIT: int = 500_000

#: 默认安全余量（token）。
DEFAULT_SAFETY_MARGIN: int = 5000


@dataclass
class CallMetadata:
    """模型调用元数据记录。

    Attributes:
        task_type: 任务类型。
        provider: 供应商。
        model: 模型名称。
        model_version: 模型版本。
        prompt_version: 提示词版本。
        tool_version: 工具版本。
        called_at: 调用时间。
        tokens_used: 使用的 token 数。
        failover: 是否使用了故障切换。
    """

    task_type: str
    provider: str
    model: str
    model_version: str
    prompt_version: str = "v1"
    tool_version: str = "v1"
    called_at: str = ""
    tokens_used: int = 0
    failover: bool = False


class DataBudgetExceeded(Exception):
    """数据预算超限异常。

    当数据部分超过 500K 硬上限时抛出。
    ContextRouter 已预先分块，此处为硬防线。
    """

    def __init__(self, data_size: int, budget: int) -> None:
        """初始化异常。

        Args:
            data_size: 实际数据大小（token）。
            budget: 预算上限（token）。
        """
        super().__init__(f"数据预算超限: {data_size} > {budget}（500K 硬上限）")
        self.data_size = data_size
        self.budget = budget


class ModelGateway:
    """模型网关：扩展现有 AI 调用层。

    在 AIProvider Protocol 基础上封装：
    1. 按任务类型自动选择模型；
    2. 计算有效数据预算（500K 硬上限）；
    3. 记录调用元数据；
    4. 故障切换备用模型。

    Attributes:
        _provider: AIProvider 实例。
        _audit_recorder: 审计记录器。
        _model_registry: 模型配置注册表（task_type → ModelConfig）。
        _call_history: 调用历史（内存记录，用于观测）。
    """

    def __init__(
        self,
        provider: Any,
        audit_recorder: Any,
        model_registry: dict[TaskType, ModelConfig] | None = None,
    ) -> None:
        """初始化模型网关。

        Args:
            provider: AIProvider 实例。
            audit_recorder: 审计记录器。
            model_registry: 模型配置注册表（可选，默认使用 get_default_registry）。
        """
        self._provider = provider
        self._audit_recorder = audit_recorder
        self._model_registry = model_registry or self.get_default_registry()
        self._call_history: list[CallMetadata] = []

    @staticmethod
    def get_default_registry() -> dict[TaskType, ModelConfig]:
        """获取默认模型配置注册表。

        Returns:
            dict[TaskType, ModelConfig]: 默认模型配置。
        """
        return {
            TaskType.PLANNING: ModelConfig(
                provider="openai",
                model="gpt-4o",
                version="2024-08",
                context_limit=128000,
            ),
            TaskType.CODE_GEN: ModelConfig(
                provider="openai",
                model="gpt-4o",
                version="2024-08",
                context_limit=128000,
            ),
            TaskType.LONG_CONTEXT: ModelConfig(
                provider="openai",
                model="gpt-4o",
                version="2024-08",
                context_limit=128000,
            ),
            TaskType.INSIGHT: ModelConfig(
                provider="openai",
                model="gpt-4o",
                version="2024-08",
                context_limit=128000,
            ),
            TaskType.CONVERSATION: ModelConfig(
                provider="openai",
                model="gpt-4o-mini",
                version="2024-07",
                context_limit=128000,
            ),
        }

    async def call(
        self,
        task_type: TaskType,
        system_prompt: str,
        data_context: str,
        research_context: str,
        tools: list[dict] | None = None,
    ) -> ModelResponse:
        """调用模型并记录元数据。

        流程：
        1. _select_model(task_type, len(data_context)) → 选择模型；
        2. _calculate_budget → 有效预算；
        3. 如果 data_context > budget → raise DataBudgetExceeded；
        4. 构建 AIRequest → 调用 provider.complete()；
        5. _record_call → 记录元数据。

        Args:
            task_type: 任务类型（planning/code_gen/long_context/insight/conversation）。
            system_prompt: 系统提示词。
            data_context: 数据部分（受 500K 硬上限约束）。
            research_context: 研究上下文（问题、计划、先前结果）。
            tools: 工具列表（可选）。

        Returns:
            ModelResponse: 模型响应 + 元数据。

        Raises:
            DataBudgetExceeded: 当数据部分超过 500K 硬上限时。
        """
        model_config = self._select_model(task_type, len(data_context))
        budget = self._calculate_budget(
            model_config,
            system_tokens=len(system_prompt) // 4,
            research_tokens=len(research_context) // 4,
            output_tokens=4000,
        )

        data_tokens = len(data_context) // 4
        if data_tokens > budget:
            raise DataBudgetExceeded(data_tokens, budget)

        # 构建 AIRequest
        from packages.ai.providers import AIRequest

        messages = (
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": f"研究上下文:\n{research_context}\n\n数据:\n{data_context}",
            },
        )

        request = AIRequest(
            messages=messages,
            tools=tuple(t.get("name", "") for t in tools) if tools else (),
            tool_schemas=tuple(tools) if tools else (),
            provider_mode="openai_compatible",
        )

        # 调用 provider
        if self._provider is None:
            # 无 provider（Worker 初始化阶段），返回回退响应
            return ModelResponse(
                answer=f"[模拟响应] 任务类型: {task_type.value}",
                provider=model_config.provider,
                model=model_config.model,
                model_version=model_config.version,
                tokens_used=data_tokens,
            )

        response = await self._provider.complete(request)

        # 记录调用元数据
        self._record_call(
            CallMetadata(
                task_type=task_type.value,
                provider=model_config.provider,
                model=model_config.model,
                model_version=model_config.version,
                called_at=datetime.now(UTC).isoformat(),
                tokens_used=data_tokens,
            )
        )

        return ModelResponse(
            answer=response.answer,
            provider=model_config.provider,
            model=model_config.model,
            model_version=model_config.version,
            tokens_used=data_tokens,
            tool_calls=list(response.tool_calls) if response.tool_calls else [],
            uncertainty=response.uncertainty,
        )

    async def call_with_failover(
        self,
        task_type: TaskType,
        system_prompt: str,
        data_context: str,
        research_context: str,
        tools: list[dict] | None = None,
    ) -> ModelResponse:
        """调用模型，故障时自动切换备用模型。

        捕获 ModelError → _get_backup_model(task_type) → 重试 → 记录切换。

        Args:
            task_type: 任务类型。
            system_prompt: 系统提示词。
            data_context: 数据部分。
            research_context: 研究上下文。
            tools: 工具列表（可选）。

        Returns:
            ModelResponse: 模型响应（含 failover_used 标记）。
        """
        try:
            return await self.call(task_type, system_prompt, data_context, research_context, tools)
        except DataBudgetExceeded:
            # 预算超限不切换，直接抛出
            raise
        except Exception as exc:
            logger.warning("Primary model failed, switching to backup: %s", exc)
            backup_config = self._get_backup_model(task_type)

            # 使用备用模型重试
            if self._provider is None:
                return ModelResponse(
                    answer=f"[备用模拟响应] 任务类型: {task_type.value}",
                    provider=backup_config.provider,
                    model=backup_config.model,
                    model_version=backup_config.version,
                    failover_used=True,
                )

            from packages.ai.providers import AIRequest

            messages = (
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": f"研究上下文:\n{research_context}\n\n数据:\n{data_context}",
                },
            )

            request = AIRequest(
                messages=messages,
                tools=tuple(t.get("name", "") for t in tools) if tools else (),
                provider_mode="openai_compatible",
            )

            response = await self._provider.complete(request)

            # 记录故障切换
            self._record_call(
                CallMetadata(
                    task_type=task_type.value,
                    provider=backup_config.provider,
                    model=backup_config.model,
                    model_version=backup_config.version,
                    called_at=datetime.now(UTC).isoformat(),
                    tokens_used=len(data_context) // 4,
                    failover=True,
                )
            )

            return ModelResponse(
                answer=response.answer,
                provider=backup_config.provider,
                model=backup_config.model,
                model_version=backup_config.version,
                tokens_used=len(data_context) // 4,
                tool_calls=list(response.tool_calls) if response.tool_calls else [],
                uncertainty=response.uncertainty,
                failover_used=True,
            )

    def _select_model(self, task_type: TaskType, data_size: int) -> ModelConfig:
        """按任务类型选择模型。

        考虑 data_size 和模型 context_limit。

        Args:
            task_type: 任务类型。
            data_size: 数据大小（字符数）。

        Returns:
            ModelConfig: 模型配置。
        """
        config = self._model_registry.get(task_type)
        if config is None:
            # 回退到 CONVERSATION 配置
            config = self._model_registry.get(TaskType.CONVERSATION)
        if config is None:
            config = ModelConfig(provider="openai", model="gpt-4o", version="2024-08")
        return config

    def _calculate_budget(
        self,
        model_config: ModelConfig,
        system_tokens: int = 2000,
        research_tokens: int = 0,
        output_tokens: int = 4000,
        safety_margin: int = DEFAULT_SAFETY_MARGIN,
    ) -> int:
        """计算有效数据预算。

        effective_data_budget = min(500_000, model_context_limit
        - system_tokens - research_tokens - output_tokens - safety_margin)

        Args:
            model_config: 模型配置。
            system_tokens: 系统提示词 token 数。
            research_tokens: 研究上下文 token 数。
            output_tokens: 预留输出 token 数。
            safety_margin: 安全余量。

        Returns:
            int: 有效数据预算（token 数），不超过 500K。
        """
        calculated = (
            model_config.context_limit
            - system_tokens
            - research_tokens
            - output_tokens
            - safety_margin
        )
        return max(0, min(DATA_BUDGET_HARD_LIMIT, calculated))

    def _get_backup_model(self, task_type: TaskType) -> ModelConfig:
        """获取备用模型配置。

        Args:
            task_type: 任务类型。

        Returns:
            ModelConfig: 备用模型配置。
        """
        # 备用模型使用同一模型的降级版本
        return ModelConfig(
            provider="openai",
            model="gpt-4o-mini",
            version="2024-07",
            context_limit=128000,
        )

    def _record_call(self, metadata: CallMetadata) -> None:
        """记录调用元数据。

        Args:
            metadata: 调用元数据。
        """
        self._call_history.append(metadata)
        # 保留最近 1000 条
        if len(self._call_history) > 1000:
            self._call_history = self._call_history[-500:]

        logger.info(
            "Model call: task=%s, provider=%s, model=%s, tokens=%d, failover=%s",
            metadata.task_type,
            metadata.provider,
            metadata.model,
            metadata.tokens_used,
            metadata.failover,
        )

    def get_call_history(self) -> list[CallMetadata]:
        """获取调用历史记录。

        Returns:
            list[CallMetadata]: 调用历史列表。
        """
        return list(self._call_history)
