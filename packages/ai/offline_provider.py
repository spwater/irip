"""离线确定性模拟 Provider。

OfflineProvider 不调用任何外部 API，基于关键词匹配返回预设回答与引用。
用于开发/测试环境与无网络场景，保证 AI 助手功能可演示且确定性可测。

匹配规则（大小写不敏感）：
- "D50" / "粒度": 返回粒度参数 D50 的溯源链路 + 引用
  （parameter_version + fact_revision + derivation_run）；
- "ROM" / "降阶模型": 返回降阶模型相关信息 + 引用 model_version；
- "标准" / "变量": 返回标准变量搜索结果 + 引用 standard_variable；
- "实验" / "事实": 返回事实搜索结果 + 引用 fact_revision；
- "参数": 返回参数搜索结果 + 引用 parameter_version；
- 其他: 返回通用引导回答（无引用）。

确定性保证：相同输入 → 相同输出（无随机数、无时间戳依赖）。
"""

from packages.ai.citations import Citation
from packages.ai.providers import AIRequest, AIResponse


class OfflineProvider:
    """离线确定性模拟 Provider，实现 AIProvider 协议。

    所有回答均为硬编码预设，不依赖外部服务。适合开发环境与 CI 测试。

    Attributes:
        provider_mode: 固定为 ``"offline"``。
    """

    provider_mode: str = "offline"

    async def complete(self, request: AIRequest) -> AIResponse:
        """处理 AI 请求，返回基于关键词匹配的确定性回答。

        Args:
            request: AI 请求（消息、工具、上下文、模式）。

        Returns:
            AIResponse: 预设回答（文本、工具调用、引用、不确定性）。
        """
        # 取最后一条 user 消息作为问题
        question = self._extract_last_user_message(request)

        # 关键词匹配（大小写不敏感）
        q_lower = question.lower()

        if "d50" in q_lower or "粒度" in question:
            return self._answer_d50(request)
        if "rom" in q_lower or "降阶模型" in question:
            return self._answer_rom(request)
        if "标准" in question or "变量" in question:
            return self._answer_standards(request)
        if "实验" in question or "事实" in question:
            return self._answer_facts(request)
        if "参数" in question:
            return self._answer_parameters(request)
        if "溯源" in question or "provenance" in q_lower:
            return self._answer_provenance(request)

        # 通用引导回答
        return AIResponse(
            answer=(
                "我是 IRIP AI 助手（离线模拟模式）。我可以帮您：\n"
                "1. 搜索标准变量、事实、参数；\n"
                "2. 解释参数溯源链路（如 D50 粒度参数）；\n"
                "3. 对比实验事实；\n"
                "4. 运行已发布模型；\n"
                "5. 生成报告草稿。\n\n"
                "请尝试提问，例如「D50 参数的溯源链路是什么？」或「有哪些降阶模型？」"
            ),
            tool_calls=(),
            citations=(),
            uncertainty="离线模拟模式，回答基于预设规则，非真实 LLM 推理",
            provider_mode=self.provider_mode,
        )

    def _extract_last_user_message(self, request: AIRequest) -> str:
        """从请求消息中提取最后一条 user 消息的文本。"""
        for msg in reversed(request.messages):
            if msg.get("role") == "user":
                return str(msg.get("content", ""))
        return ""

    def _answer_d50(self, request: AIRequest) -> AIResponse:
        """D50 粒度参数溯源链路回答。"""
        citations = (
            Citation(
                object_type="parameter_version",
                object_id="00000000-0000-0000-0000-0000000000d50",
                version="v3",
                label="粒度参数 D50 v3（已发布）",
                href="/parameters/00000000-0000-0000-0000-0000000000d50",
            ),
            Citation(
                object_type="fact_revision",
                object_id="00000000-0000-0000-0000-0000000000f01",
                version="rev 2",
                label="实验事实 EXP-2026-001 修订 2",
                href="/facts/00000000-0000-0000-0000-0000000000f01",
            ),
            Citation(
                object_type="derivation_run",
                object_id="00000000-0000-0000-0000-0000000000dr1",
                version="run #5",
                label="推导运行 DERIV-2026-005（succeeded）",
                href="/parameters/00000000-0000-0000-0000-0000000000dr1",
            ),
        )
        tool_calls = (
            {
                "tool": "search_parameters",
                "args": {"variable_code": "d50"},
                "summary": "找到 1 个已发布参数：D50 v3",
            },
            {
                "tool": "explain_provenance",
                "args": {"parameter_id": "00000000-0000-0000-0000-0000000000d50"},
                "summary": "溯源链路：事实修订 rev2 → 推导运行 #5 → 参数版本 v3",
            },
        )
        return AIResponse(
            answer=(
                "D50（粒度分布的中位径）参数的溯源链路如下：\n\n"
                "1. **参数版本**：D50 v3（已发布），当前值为 32.5 μm，"
                "置信度 0.92。\n"
                "2. **推导运行**：DERIV-2026-005（状态 succeeded），"
                "使用加权平均算法从 3 个实验事实中推导。\n"
                "3. **事实修订**：EXP-2026-001 修订 2，"
                "来源于篦冷机入料粒度激光衍射测试。\n\n"
                "该参数当前状态为 **current**（无需复核）。"
                "如需查看完整溯源图，请点击下方引用。"
            ),
            tool_calls=tool_calls,
            citations=citations,
            uncertainty=None,
            provider_mode=self.provider_mode,
        )

    def _answer_rom(self, request: AIRequest) -> AIResponse:
        """降阶模型（ROM）回答。"""
        citations = (
            Citation(
                object_type="model_version",
                object_id="00000000-0000-0000-0000-0000000000m01",
                version="v2",
                label="篦冷机降阶模型 grate_cooler_rom v2（已发布）",
                href="/models/00000000-0000-0000-0000-0000000000m01",
            ),
            Citation(
                object_type="parameter_version",
                object_id="00000000-0000-0000-0000-0000000000d50",
                version="v3",
                label="粒度参数 D50 v3（模型输入）",
                href="/parameters/00000000-0000-0000-0000-0000000000d50",
            ),
        )
        tool_calls = (
            {
                "tool": "search_standards",
                "args": {"query": "rom"},
                "summary": "未找到标准变量，建议搜索模型",
            },
            {
                "tool": "run_published_model",
                "args": {
                    "model_id": "00000000-0000-0000-0000-0000000000m01",
                    "inputs": {"d50": 32.5},
                },
                "summary": "预测完成：出口温度 = 285°C，二次风温 = 720°C",
            },
        )
        return AIResponse(
            answer=(
                "当前已发布的降阶模型（ROM）如下：\n\n"
                "- **grate_cooler_rom v2**（已发布）：篦冷机降阶模型，"
                "输入 D50 粒度参数，输出出口温度与二次风温。\n"
                "  - 最新预测示例：D50=32.5μm → 出口温度 285°C，"
                "二次风温 720°C。\n"
                "  - 适用域：D50 ∈ [20, 50] μm。\n\n"
                "如需运行预测，请在预测工作台选择该模型。"
            ),
            tool_calls=tool_calls,
            citations=citations,
            uncertainty="适用域边界附近预测精度下降",
            provider_mode=self.provider_mode,
        )

    def _answer_standards(self, request: AIRequest) -> AIResponse:
        """标准变量搜索回答。"""
        citations = (
            Citation(
                object_type="standard_variable",
                object_id="00000000-0000-0000-0000-0000000000sv1",
                version="v1",
                label="标准变量 d50（粒度中位径）",
                href="/standards",
            ),
        )
        tool_calls = (
            {
                "tool": "search_standards",
                "args": {"query": request.messages[-1].get("content", "")},
                "summary": "找到 1 个匹配的标准变量：d50",
            },
        )
        return AIResponse(
            answer=(
                "标准变量搜索结果：\n\n"
                "- **d50**（粒度中位径）：数据类型 number，标准单位 μm，"
                "量纲 length，有效范围 [1, 1000] μm。\n\n"
                "该变量已发布 v1 版本，可被事实观察值引用。"
            ),
            tool_calls=tool_calls,
            citations=citations,
            uncertainty=None,
            provider_mode=self.provider_mode,
        )

    def _answer_facts(self, request: AIRequest) -> AIResponse:
        """事实搜索回答。"""
        citations = (
            Citation(
                object_type="fact_revision",
                object_id="00000000-0000-0000-0000-0000000000f01",
                version="rev 2",
                label="实验事实 EXP-2026-001 修订 2",
                href="/facts/00000000-0000-0000-0000-0000000000f01",
            ),
        )
        tool_calls = (
            {
                "tool": "search_facts",
                "args": {"query": request.messages[-1].get("content", "")},
                "summary": "找到 1 个匹配的事实",
            },
        )
        return AIResponse(
            answer=(
                "实验事实搜索结果：\n\n"
                "- **EXP-2026-001 修订 2**（experiment_run）："
                "篦冷机入料粒度激光衍射测试，"
                "包含 1 个原始观察值与 1 个标准化观察值（d50=32.5μm）。\n\n"
                "该事实已被推导运行 DERIV-2026-005 引用。"
            ),
            tool_calls=tool_calls,
            citations=citations,
            uncertainty=None,
            provider_mode=self.provider_mode,
        )

    def _answer_parameters(self, request: AIRequest) -> AIResponse:
        """参数搜索回答。"""
        citations = (
            Citation(
                object_type="parameter_version",
                object_id="00000000-0000-0000-0000-0000000000d50",
                version="v3",
                label="粒度参数 D50 v3（已发布）",
                href="/parameters/00000000-0000-0000-0000-0000000000d50",
            ),
        )
        tool_calls = (
            {
                "tool": "search_parameters",
                "args": {"variable_code": "d50"},
                "summary": "找到 1 个已发布参数",
            },
        )
        return AIResponse(
            answer=(
                "参数搜索结果：\n\n"
                "- **D50 v3**（已发布）：变量 d50，值 32.5 μm，"
                "置信度 0.92，状态 current。\n"
                "  - 来源推导运行：DERIV-2026-005。\n"
                "  - 依赖事实：EXP-2026-001 rev 2。\n\n"
                "该参数当前无需复核。"
            ),
            tool_calls=tool_calls,
            citations=citations,
            uncertainty=None,
            provider_mode=self.provider_mode,
        )

    def _answer_provenance(self, request: AIRequest) -> AIResponse:
        """溯源链路回答。"""
        citations = (
            Citation(
                object_type="parameter_version",
                object_id="00000000-0000-0000-0000-0000000000d50",
                version="v3",
                label="粒度参数 D50 v3",
                href="/parameters/00000000-0000-0000-0000-0000000000d50",
            ),
            Citation(
                object_type="derivation_run",
                object_id="00000000-0000-0000-0000-0000000000dr1",
                version="run #5",
                label="推导运行 DERIV-2026-005",
                href="/parameters/00000000-0000-0000-0000-0000000000dr1",
            ),
            Citation(
                object_type="fact_revision",
                object_id="00000000-0000-0000-0000-0000000000f01",
                version="rev 2",
                label="实验事实 EXP-2026-001 rev 2",
                href="/facts/00000000-0000-0000-0000-0000000000f01",
            ),
        )
        tool_calls = (
            {
                "tool": "explain_provenance",
                "args": {"parameter_id": "00000000-0000-0000-0000-0000000000d50"},
                "summary": "完整溯源链路已生成",
            },
        )
        return AIResponse(
            answer=(
                "溯源链路（从参数到原始数据）：\n\n"
                "1. **参数版本** D50 v3（已发布）\n"
                "   ↓ 由推导运行生成\n"
                "2. **推导运行** DERIV-2026-005（succeeded，加权平均算法）\n"
                "   ↓ 引用证据集\n"
                "3. **事实修订** EXP-2026-001 rev 2（experiment_run）\n"
                "   ↓ 包含观察值\n"
                "4. **标准化观察值** d50 = 32.5 μm\n"
                "   ↓ 来源于\n"
                "5. **原始观察值** 激光衍射测试原始读数\n\n"
                "链路完整性：✓ 所有节点已发布且未被修订覆盖。"
            ),
            tool_calls=tool_calls,
            citations=citations,
            uncertainty=None,
            provider_mode=self.provider_mode,
        )
