"""Insight 候选提取器：从 LLM 响应提取结构化 Insight 候选。

InsightExtractor 在 Orchestrator._execute_step 完成后（method=llm 或 mixed）调用：
1. 构建 INSIGHT_EXTRACTION_PROMPT（要求 AI 输出 6 字段 JSON + evidence_source_label）
2. 调用 ModelGateway.call(task_type=INSIGHT, ...)
3. 解析 AI 返回的结构化 JSON
4. 校验 6 个必填字段 + evidence_source_label 存在
5. 解析成功 → 返回 InsightCandidateData
6. 解析失败 → 保留 AI 原始文本，返回带 extraction_failed=true 的数据

参照架构设计 3.3 节 InsightExtractor 定义。
"""

import json
import logging
from typing import Any

from packages.research.execution.models_trusted import TaskType
from packages.research.models import InsightCandidateData

logger = logging.getLogger("research.insight_extractor")


class InsightExtractor:
    """从 LLM 响应提取结构化 Insight 候选。

    依赖注入 ModelGateway，通过 INSIGHT 任务类型调用模型。

    Attributes:
        _model_gateway: 模型网关实例。
    """

    #: Insight 提取系统提示词（要求输出 6 字段 JSON + evidence_source_label）。
    INSIGHT_EXTRACTION_PROMPT = (
        "你是一个科研分析助手。请从以下分析步骤的输出中提取结构化的 Insight（见解）。\n"
        "如果步骤输出中不包含可提取的 Insight（如纯数据处理步骤），请返回 null。\n\n"
        "请输出严格的 JSON 格式（不要使用 markdown 代码块），包含以下字段：\n"
        "{\n"
        '  "conclusion": "结论文本（必填）",\n'
        '  "scope": "适用范围文本（必填）",\n'
        '  "evidence_refs": [{"type": "dataset|fact|view", "name": "名称", "version": 1}],\n'
        '  "method_refs": [{"run_id": "Run ID", "step_key": "步骤键"}],\n'
        '  "confidence_level": "high|medium|low 或说明文本（必填）",\n'
        '  "limitations": "限制条件文本（必填）",\n'
        '  "evidence_source_label": "experimental_data|knowledge_base|model_inference（必填）"\n'
        "}\n\n"
        "字段说明：\n"
        "- conclusion: 核心结论，一句话概括\n"
        "- scope: 适用范围，说明该 Insight 在什么条件下成立\n"
        "- evidence_refs: 证据引用列表，指向使用的数据集/事实/视图\n"
        "- method_refs: 方法引用列表，指向使用的 Run 和步骤\n"
        "- confidence_level: 置信度（high/medium/low 或自由文本说明）\n"
        "- limitations: 限制条件，说明未控制的因素、样本量等\n"
        "- evidence_source_label: 证据来源标签\n"
        "  - experimental_data: 基于实验数据得出\n"
        "  - knowledge_base: 基于知识库/文献得出\n"
        "  - model_inference: 基于模型推测得出\n\n"
        "如果无法提取 Insight，返回: null"
    )

    #: 提示词版本（记录在 ModelGateway 调用元数据中）。
    PROMPT_VERSION = "insight_extraction_v1"

    def __init__(self, model_gateway: Any) -> None:
        """初始化 Insight 提取器。

        Args:
            model_gateway: ModelGateway 实例。
        """
        self._model_gateway = model_gateway

    async def extract(
        self,
        step_output: str,
        research_context: str,
    ) -> InsightCandidateData | None:
        """从步骤输出中提取 Insight 候选。

        Args:
            step_output: 步骤输出文本（LLM 回答或混合步骤的 LLM 部分）。
            research_context: 研究上下文（主问题 + 计划 + 已完成步骤摘要）。

        Returns:
            InsightCandidateData | None: 提取的候选数据，无 Insight 时返回 None。
        """
        if not step_output or not step_output.strip():
            return None

        self._build_insight_prompt(step_output, research_context)

        try:
            response = await self._model_gateway.call(
                task_type=TaskType.INSIGHT,
                system_prompt=self.INSIGHT_EXTRACTION_PROMPT,
                data_context=step_output,
                research_context=research_context,
            )
        except Exception as exc:
            logger.warning("Insight extraction model call failed: %s", exc)
            return None

        raw_answer = response.answer if hasattr(response, "answer") else str(response)
        return self._parse_insight_json(raw_answer)

    def _build_insight_prompt(self, step_output: str, research_context: str) -> str:
        """构建 Insight 提取提示词。

        Args:
            step_output: 步骤输出文本。
            research_context: 研究上下文。

        Returns:
            str: 构建的提示词文本。
        """
        # 截断过长的步骤输出
        truncated_output = step_output[:8000] if len(step_output) > 8000 else step_output
        return f"研究上下文:\n{research_context}\n\n步骤输出:\n{truncated_output}"

    def _parse_insight_json(self, raw_response: str) -> InsightCandidateData | None:
        """解析 AI 返回的 JSON，校验字段完整性。

        Args:
            raw_response: AI 原始回答文本。

        Returns:
            InsightCandidateData | None: 解析后的候选数据，无 Insight 时返回 None。
        """
        if not raw_response or not raw_response.strip():
            return None

        text = raw_response.strip()

        # 检查是否为 null（无 Insight）
        if text.lower() == "null" or text.lower() == "none":
            return None

        # 尝试提取 JSON（可能被 markdown 代码块包裹）
        json_text = self._extract_json_from_text(text)
        if json_text is None:
            # JSON 解析失败 → 保留 AI 原始文本，标记为生成失败
            logger.warning("Insight JSON parse failed, preserving raw text")
            return InsightCandidateData(
                conclusion=text[:500],
                scope="",
                evidence_refs=[],
                method_refs=[],
                confidence_level="",
                limitations="",
                evidence_source_label="model_inference",
                ai_raw_text=raw_response,
                extraction_failed=True,
            )

        try:
            data = json.loads(json_text)
        except json.JSONDecodeError as exc:
            logger.warning("Insight JSON decode failed: %s", exc)
            return InsightCandidateData(
                conclusion=text[:500],
                scope="",
                evidence_refs=[],
                method_refs=[],
                confidence_level="",
                limitations="",
                evidence_source_label="model_inference",
                ai_raw_text=raw_response,
                extraction_failed=True,
            )

        if not isinstance(data, dict):
            return None

        # 校验 6 个必填字段 + evidence_source_label
        if not self._validate_fields(data):
            logger.warning("Insight fields validation failed")
            return InsightCandidateData(
                conclusion=str(data.get("conclusion", text[:500])),
                scope=str(data.get("scope", "")),
                evidence_refs=data.get("evidence_refs", []),
                method_refs=data.get("method_refs", []),
                confidence_level=str(data.get("confidence_level", "")),
                limitations=str(data.get("limitations", "")),
                evidence_source_label=str(data.get("evidence_source_label", "model_inference")),
                ai_raw_text=raw_response,
                extraction_failed=True,
            )

        return InsightCandidateData(
            conclusion=str(data["conclusion"]),
            scope=str(data["scope"]),
            evidence_refs=list(data.get("evidence_refs", [])),
            method_refs=list(data.get("method_refs", [])),
            confidence_level=str(data["confidence_level"]),
            limitations=str(data["limitations"]),
            evidence_source_label=str(data["evidence_source_label"]),
            ai_raw_text=raw_response,
            extraction_failed=False,
        )

    def _validate_fields(self, data: dict[str, Any]) -> bool:
        """校验 6 个必填字段 + evidence_source_label 是否存在且非空。

        Args:
            data: 解析后的 JSON dict。

        Returns:
            bool: 校验通过返回 True。
        """
        required_fields = [
            "conclusion",
            "scope",
            "evidence_refs",
            "method_refs",
            "confidence_level",
            "limitations",
            "evidence_source_label",
        ]
        for field_name in required_fields:
            if field_name not in data:
                return False
            val = data[field_name]
            if val is None:
                return False
            if isinstance(val, str) and not val.strip():
                return False
            if (
                isinstance(val, (list, dict))
                and len(val) == 0
                and field_name
                in (
                    "evidence_refs",
                    "method_refs",
                )
            ):
                # evidence_refs 和 method_refs 允许为空列表
                continue
        # 校验 evidence_source_label 取值
        label = str(data.get("evidence_source_label", ""))
        valid_labels = {"experimental_data", "knowledge_base", "model_inference"}
        if label not in valid_labels:
            return False
        return True

    def _extract_json_from_text(self, text: str) -> str | None:
        """从文本中提取 JSON 字符串（处理 markdown 代码块包裹）。

        Args:
            text: 原始文本。

        Returns:
            str | None: 提取的 JSON 字符串，无法提取时返回 None。
        """
        text = text.strip()

        # 去除 markdown 代码块
        if text.startswith("```"):
            lines = text.split("\n")
            # 去除首行（```json 或 ```）
            if lines:
                lines = lines[1:]
            # 去除末行 ```
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        # 尝试找到 JSON 的起止位置
        brace_start = text.find("{")
        brace_end = text.rfind("}")
        if brace_start == -1 or brace_end == -1 or brace_end <= brace_start:
            return None

        return text[brace_start : brace_end + 1]
