"""上下文路由器：自动分析模式选择 + 有效数据预算计算 + 分块策略 + 覆盖率计算。

ContextRouter 是无状态服务，负责：
1. 根据步骤需求和数据特征自动选择分析模式（full_compute / chunked_full_scan /
   direct_full_context / retrieval / mixed）；
2. 计算有效数据预算（500K 硬上限）：
   effective_data_budget = min(500_000, model_context_limit - system_and_tool_tokens
   - research_context_tokens - reserved_output_tokens - safety_margin)；
3. 按 token 预算切分数据（默认策略，允许步骤级覆盖）；
4. 计算覆盖声明（数据覆盖率与 LLM 阅读率独立计算）。

关键约束：
- 不允许静默抽样：allows_sampling=False 时不分块不抽样，只能分块全量扫描；
- 500K 硬上限在 calculate_budget 中强制执行。
"""

import logging

from packages.research.models_trusted import (
    AnalysisMode,
    Chunk,
    ChunkStrategy,
    CoverageDeclaration,
    DataProfile,
    PlanStep,
)

logger = logging.getLogger("research.context_router")

#: 500K 硬上限（单次模型调用数据部分的 token 上限）。
DATA_BUDGET_HARD_LIMIT: int = 500_000

#: 默认安全余量（token）。
DEFAULT_SAFETY_MARGIN: int = 5000

#: 默认系统+工具 token 估算。
DEFAULT_SYSTEM_TOKENS: int = 2000

#: 默认输出 token 预留。
DEFAULT_OUTPUT_TOKENS: int = 4000

#: 默认模型上下文窗口限制。
DEFAULT_MODEL_CONTEXT_LIMIT: int = 128000

#: 粗略 token 估算比例（1 token ≈ 4 字符英文 / 2 字符中文）。
_CHARS_PER_TOKEN: float = 3.5


def _estimate_tokens(text: str) -> int:
    """粗略估算文本的 token 数。

    Args:
        text: 文本内容。

    Returns:
        int: 估算的 token 数。
    """
    if not text:
        return 0
    return max(1, int(len(text) / _CHARS_PER_TOKEN))


class ContextRouter:
    """上下文路由器：自动分析模式选择 + 预算计算 + 分块策略。

    无状态服务，可单例使用。所有方法均为纯函数（无副作用）。
    """

    def analyze_step(
        self,
        step: PlanStep,
        data_profile: DataProfile,
    ) -> tuple[str, str]:
        """根据步骤需求和数据特征自动选择分析模式。

        决策逻辑：
        - requires_full=True + cross_record_reasoning=False → full_compute
        - requires_full=True + per_record_semantic=True → chunked_full_scan
        - requires_full=True + cross_record_reasoning=True + fits_budget → direct_full_context
        - allows_sampling=False + not fits_budget → chunked_full_scan
        - 混合需求 → mixed

        Args:
            step: DAG 步骤定义。
            data_profile: 数据 Profile。

        Returns:
            tuple[str, str]: (analysis_mode, mode_reason)。
        """
        budget = self.calculate_budget(
            model_context_limit=DEFAULT_MODEL_CONTEXT_LIMIT,
            system_and_tool_tokens=DEFAULT_SYSTEM_TOKENS,
            research_context_tokens=step.estimated_tokens,
            reserved_output_tokens=DEFAULT_OUTPUT_TOKENS,
        )
        data_tokens = data_profile.total_tokens_estimate
        fits_budget = data_tokens <= budget

        # 全量计算（Python 全量处理，不需要 LLM 语义理解）
        if step.requires_full and not step.per_record_semantic and not step.cross_record_reasoning:
            return AnalysisMode.FULL_COMPUTE.value, ("数据需 Python 全量计算，无需逐条语义阅读")

        # 逐条语义阅读 + 全量要求 → 分块全量扫描
        if step.requires_full and step.per_record_semantic:
            if fits_budget:
                return AnalysisMode.DIRECT_FULL_CONTEXT.value, (
                    "数据在预算内，直接全量上下文逐条语义阅读"
                )
            return AnalysisMode.CHUNKED_FULL_SCAN.value, (
                "数据超预算，分块全量扫描确保每条记录至少进入一次模型调用"
            )

        # 跨记录推理 + 全量要求
        if step.requires_full and step.cross_record_reasoning:
            if fits_budget:
                return AnalysisMode.DIRECT_FULL_CONTEXT.value, (
                    "数据在预算内，直接全量上下文进行跨记录推理"
                )
            # 不允许抽样时只能分块全量扫描
            if not step.allows_sampling:
                return AnalysisMode.CHUNKED_FULL_SCAN.value, (
                    "数据超预算且不允许抽样，分块全量扫描后分层归并"
                )
            return AnalysisMode.RETRIEVAL.value, ("数据超预算且允许抽样，检索探索模式")

        # 混合需求
        if step.method == "mixed":
            return AnalysisMode.MIXED.value, ("步骤需 Python 全量计算 + LLM 语义分析混合")

        # 默认：检索探索
        return AnalysisMode.RETRIEVAL.value, "默认检索探索模式"

    def calculate_budget(
        self,
        model_context_limit: int = DEFAULT_MODEL_CONTEXT_LIMIT,
        system_and_tool_tokens: int = DEFAULT_SYSTEM_TOKENS,
        research_context_tokens: int = 0,
        reserved_output_tokens: int = DEFAULT_OUTPUT_TOKENS,
        safety_margin: int = DEFAULT_SAFETY_MARGIN,
    ) -> int:
        """计算有效数据预算。

        effective_data_budget = min(500_000, model_context_limit
        - system_and_tool_tokens - research_context_tokens
        - reserved_output_tokens - safety_margin)

        Args:
            model_context_limit: 模型上下文窗口限制。
            system_and_tool_tokens: 系统+工具 token 数。
            research_context_tokens: 研究上下文 token 数。
            reserved_output_tokens: 预留输出 token 数。
            safety_margin: 安全余量。

        Returns:
            int: 有效数据预算（token 数），不超过 500K。
        """
        calculated = (
            model_context_limit
            - system_and_tool_tokens
            - research_context_tokens
            - reserved_output_tokens
            - safety_margin
        )
        return max(0, min(DATA_BUDGET_HARD_LIMIT, calculated))

    def chunk_data(
        self,
        data: str,
        budget: int,
        strategy: ChunkStrategy = ChunkStrategy.TOKEN_BUDGET,
    ) -> list[Chunk]:
        """按 token 预算切分数据。

        Q6 默认按 token 预算切分，每块预留安全余量。允许步骤级覆盖策略。

        Args:
            data: 原始数据文本。
            budget: 每块 token 预算。
            strategy: 分块策略（默认 TOKEN_BUDGET）。

        Returns:
            list[Chunk]: 分块列表。
        """
        if not data:
            return []

        if strategy == ChunkStrategy.RECORD_COUNT:
            return self._chunk_by_record_count(data, budget)
        elif strategy == ChunkStrategy.BUSINESS_LOGIC:
            return self._chunk_by_business_logic(data, budget)
        else:
            return self._chunk_by_token_budget(data, budget)

    def _chunk_by_token_budget(self, data: str, budget: int) -> list[Chunk]:
        """按 token 预算切分。

        每块预留 10% 安全余量。

        Args:
            data: 原始数据文本。
            budget: 每块 token 预算。

        Returns:
            list[Chunk]: 分块列表。
        """
        effective_budget = int(budget * 0.9)  # 预留 10% 安全余量
        if effective_budget <= 0:
            effective_budget = 1

        # 按字符切分（每块 ≈ effective_budget * _CHARS_PER_TOKEN 字符）
        chars_per_chunk = int(effective_budget * _CHARS_PER_TOKEN)
        if chars_per_chunk <= 0:
            chars_per_chunk = 1

        chunks: list[Chunk] = []
        idx = 0
        chunk_index = 0
        total_len = len(data)

        while idx < total_len:
            end = min(idx + chars_per_chunk, total_len)
            chunk_content = data[idx:end]
            token_count = _estimate_tokens(chunk_content)
            chunks.append(
                Chunk(
                    index=chunk_index,
                    content=chunk_content,
                    token_count=token_count,
                    record_range=(idx, end),
                )
            )
            idx = end
            chunk_index += 1

        return chunks

    def _chunk_by_record_count(self, data: str, budget: int) -> list[Chunk]:
        """按记录数切分（简单实现：按行切分）。

        Args:
            data: 原始数据文本。
            budget: 每块 token 预算。

        Returns:
            list[Chunk]: 分块列表。
        """
        lines = data.split("\n")
        if not lines:
            return []

        effective_budget = int(budget * 0.9)
        chunks: list[Chunk] = []
        current_lines: list[str] = []
        current_tokens = 0
        chunk_index = 0
        start_line = 0

        for i, line in enumerate(lines):
            line_tokens = _estimate_tokens(line)
            if current_tokens + line_tokens > effective_budget and current_lines:
                chunk_content = "\n".join(current_lines)
                chunks.append(
                    Chunk(
                        index=chunk_index,
                        content=chunk_content,
                        token_count=current_tokens,
                        record_range=(start_line, i),
                    )
                )
                chunk_index += 1
                current_lines = []
                current_tokens = 0
                start_line = i
            current_lines.append(line)
            current_tokens += line_tokens

        if current_lines:
            chunk_content = "\n".join(current_lines)
            chunks.append(
                Chunk(
                    index=chunk_index,
                    content=chunk_content,
                    token_count=current_tokens,
                    record_range=(start_line, len(lines)),
                )
            )

        return chunks

    def _chunk_by_business_logic(self, data: str, budget: int) -> list[Chunk]:
        """按业务逻辑切分（占位实现：回退到 token 预算切分）。

        Args:
            data: 原始数据文本。
            budget: 每块 token 预算。

        Returns:
            list[Chunk]: 分块列表。
        """
        return self._chunk_by_token_budget(data, budget)

    def compute_coverage(
        self,
        step: PlanStep,
        chunks: list[Chunk] | None,
        total_records: int,
        analysis_mode: str,
        successful_chunks: int = 0,
    ) -> CoverageDeclaration:
        """计算覆盖声明。

        数据覆盖率与 LLM 阅读率独立计算：
        - 数据覆盖率 = Python 全量计算时为 1.0；分块扫描时为成功块数/总块数；
        - LLM 阅读率 = LLM 逐条语义阅读的记录比例；
        - is_sampled = allows_sampling 且实际使用了抽样。

        Args:
            step: 步骤定义。
            chunks: 分块列表（非分块时为 None 或空）。
            total_records: 总记录数。
            analysis_mode: 分析模式。
            successful_chunks: 成功处理的分块数。

        Returns:
            CoverageDeclaration: 覆盖声明。
        """
        is_sampled = step.allows_sampling and analysis_mode == AnalysisMode.RETRIEVAL.value

        # 全量计算：数据覆盖率 1.0，LLM 阅读率 0.0
        if analysis_mode == AnalysisMode.FULL_COMPUTE.value:
            return CoverageDeclaration(
                analysis_mode=analysis_mode,
                data_coverage_rate=1.0,
                llm_read_rate=0.0,
                is_sampled=False,
                batch_count=None,
                batch_progress=None,
                mode_reason="Python 全量计算，数据覆盖率 100%",
            )

        # 直接全量上下文：数据覆盖率 1.0，LLM 阅读率 1.0
        if analysis_mode == AnalysisMode.DIRECT_FULL_CONTEXT.value:
            return CoverageDeclaration(
                analysis_mode=analysis_mode,
                data_coverage_rate=1.0,
                llm_read_rate=1.0,
                is_sampled=False,
                batch_count=None,
                batch_progress=None,
                mode_reason="数据在预算内，全量上下文直接阅读",
            )

        # 分块全量扫描：数据覆盖率 = 成功块数/总块数，LLM 阅读率同
        if analysis_mode == AnalysisMode.CHUNKED_FULL_SCAN.value:
            total_chunks = len(chunks) if chunks else 0
            coverage_rate = (successful_chunks / total_chunks) if total_chunks > 0 else 0.0
            return CoverageDeclaration(
                analysis_mode=analysis_mode,
                data_coverage_rate=coverage_rate,
                llm_read_rate=coverage_rate,
                is_sampled=False,
                batch_count=total_chunks if total_chunks > 0 else None,
                batch_progress=successful_chunks if total_chunks > 0 else None,
                mode_reason="分块全量扫描，每条记录至少进入一次模型调用",
            )

        # 检索探索：数据覆盖率 < 1.0，LLM 阅读率 < 1.0
        if analysis_mode == AnalysisMode.RETRIEVAL.value:
            # 抽样场景下覆盖率取决于检索比例
            sampled_rate = min(1.0, successful_chunks / max(1, total_records))
            return CoverageDeclaration(
                analysis_mode=analysis_mode,
                data_coverage_rate=sampled_rate,
                llm_read_rate=sampled_rate,
                is_sampled=is_sampled,
                batch_count=None,
                batch_progress=None,
                mode_reason="检索探索模式，部分数据被检索" if is_sampled else "检索探索",
            )

        # 混合分析
        return CoverageDeclaration(
            analysis_mode=analysis_mode,
            data_coverage_rate=1.0,
            llm_read_rate=0.75,
            is_sampled=False,
            batch_count=len(chunks) if chunks else None,
            batch_progress=successful_chunks if chunks else None,
            mode_reason="Python 全量计算 + LLM 语义分析混合",
        )

    def build_data_profile_summary(self, profile: DataProfile) -> str:
        """构建数据 Profile 摘要文本（传给 AI 的数据描述）。

        Args:
            profile: 数据 Profile。

        Returns:
            str: 摘要文本。
        """
        lines: list[str] = [
            f"数据源数量: {profile.source_count}",
            f"总记录数: {profile.total_records}",
            f"预估 token 数: {profile.total_tokens_estimate}",
            "字段清单:",
        ]
        for fact_id, fields in profile.field_manifest.items():
            lines.append(f"  - {fact_id}: {', '.join(fields)}")
        return "\n".join(lines)
