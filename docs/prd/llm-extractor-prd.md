# PRD：LLM 数据提取组件（llm_extractor）

## 1. 项目信息

| 项 | 值 |
|---|---|
| **语言** | 中文 |
| **编程语言 / 技术栈** | Python（IRIP 现有技术栈：FastAPI + SQLAlchemy + httpx + pyyaml + jsonschema） |
| **项目名** | `llm_extractor` |
| **组件类别** | `ingestion`（与 csv_reader / excel_reader 并列） |
| **运行时** | `python` |

### 原始需求复述

IRIP 已有 29 个内置组件，每种输入格式（CSV / Excel / PDF / JSON）都需要专门的 Python 组件，格式稍有变化就得改代码，容错率低。本需求创建一个 **llm_extractor** 组件：用大模型替代固定解析代码，输入"文件路径 + 提取 prompt + 目标字段 schema"，由已配置的大模型（Qwen3，OpenAI 兼容 API）理解文件内容并提取结构化数据，输出标准 `ObservationTable`，使下游组件（field_mapper / quality_check / descriptive）无需修改即可复用。该组件注册到组件管理系统后，可在流程编排中直接替换 csv_reader 节点。

---

## 2. 产品目标

| # | 目标 | 衡量标准 |
|---|------|---------|
| G1 | **一份 prompt 替代多个格式解析组件**——用户无需为每种文件格式编写/修改 Python 代码，通过自然语言 prompt + 字段 schema 即可提取结构化数据 | 同一个 llm_extractor 组件能正确处理 CSV / Excel / JSON / 纯文本等不同格式输入，输出符合 schema 的 ObservationTable |
| G2 | **对下游完全透明**——llm_extractor 的输出与 csv_reader 等现有摄入组件的输出类型一致，下游组件零改动 | llm_extractor 输出端口 `observations` 的 `data_type` 为 `observation_table`，可直接替换流程中的 csv_reader 节点，field_mapper → quality_check → descriptive 链路正常运行 |
| G3 | **AI 助手可自然语言触发提取流程**——用户在对话中说"帮我提取这份 CSV 的粒度数据"即可组装并运行提取流程 | AI 助手通过工具调用识别用户意图，自动构造 llm_extractor 节点参数（path / prompt / schema）并触发流程执行，返回提取结果摘要 |

---

## 3. 用户故事

| # | 角色 | 故事 |
|---|------|------|
| US1 | 研究员 | 作为研究员，我想**用一段自然语言描述从非标准格式文件中提取数据**，这样我就不必等开发写专门的解析组件，文件表头变了也能立刻适配。 |
| US2 | 研究员 | 作为研究员，我想**在流程编排中用 llm_extractor 直接替换 csv_reader 节点**，这样已有的 field_mapper → quality_check → descriptive 下游链路不用改任何配置。 |
| US3 | 研究员 | 作为研究员，我想**在 AI 助手对话中说"帮我提取这份 CSV 的粒度数据"就能自动跑完提取流程**，这样我不需要手动去流程编排页面拖拽节点。 |
| US4 | 平台管理员 | 作为平台管理员，我想**llm_extractor 自动复用已配置的大模型连接（AI 配置页的 base_url / api_key / model）**，这样不需要在组件参数里重复填写密钥，也不泄露密钥到流程参数。 |
| US5 | 研究员 | 作为研究员，我想**大模型返回的结果带有来源定位（文件名 + 行号）**，这样提取结果可追溯，质量检查组件能定位到原始文件的具体位置。 |

---

## 4. 需求池

### P0 — Must Have（必须有）

| ID | 需求 | 验收标准 |
|----|------|---------|
| P0-1 | **组件实现**：创建 `packages/components/builtin/ingestion/llm_extractor.py`，实现 `LLMExtractor` 类，遵循 `Component` 协议（`async execute(context, params) -> ComponentResult`） | 类可被实例化并调用 execute；输出 `ComponentResult.outputs["observations"]` 为 `ObservationTable` 实例 |
| P0-2 | **Manifest 清单**：创建 `schemas/component-manifest/llm-extractor.yaml`，kind=ingestion，runtime=python，无输入端口，输出端口 `observations`（data_type=observation_table） | 通过 `ManifestValidator.validate()` 校验，SHA-256 摘要正确计算 |
| P0-3 | **组件注册**：在 `packages/components/builtin/__init__.py` 的 `_BUILTIN_COMPONENTS` 和 `_YAML_FILES` 中注册 `llm_extractor` | `register_builtin_components()` 执行后，`PythonComponentRunner` 可按 `(llm_extractor, 1.0.0)` 找到实现；`list_builtin_components()` 包含 `llm_extractor` |
| P0-4 | **参数定义**：manifest parameters 至少包含 `path`（文件路径，必填）、`prompt`（提取指令，必填）、`schema`（目标字段定义，必填） | 缺少必填参数时 JSON Schema 校验失败；参数通过 `FlowValidationService.check_param_schema` 校验 |
| P0-5 | **读取 AI 配置**：组件执行时调用 `get_active_ai_config()` 获取已启用的大模型配置（base_url / api_key / model_name / thinking_enabled） | AI 配置未启用或未配置时，返回 `AppError`（code 建议 `ai_not_configured`），summary 明确提示用户去 AI 配置页开启 |
| P0-6 | **调用大模型**：读取文件内容 → 拼装 system + user 消息（含文件内容、提取 prompt、目标 schema）→ 调用 `{base_url}/chat/completions`（OpenAI 兼容格式）→ 解析 JSON 响应 | 使用 httpx 异步调用；超时可控（参数 `timeout` 或 manifest `timeout_seconds`）；API 密钥不记录到日志/summary/metadata |
| P0-7 | **输出 ObservationTable**：将大模型返回的结构化 JSON 转为 `ObservationTable`，columns 来自 schema 定义，rows 为解析后的数据，source_locations 标注文件名与行号 | `table.columns` 与 schema 字段名一致；`table.row_count()` >= 0；`source_locations[i]` 含 `file` 和 `row` 键 |
| P0-8 | **网络策略声明**：manifest 中声明 `network_policy.allowed_hosts`，允许访问 AI 配置的 base_url 对应主机 | 流程发布时端口/参数校验通过；运行时组件可访问 LLM API |
| P0-9 | **3 个 Demo CSV 文件**：在 `tests/fixtures/` 或 `docs/data-onboarding/` 下提供 3 个示例 CSV | 文件覆盖：标准表头 CSV、非标准/混合格式 CSV、含粒度数据的 CSV；可用于单元测试与端到端测试 |
| P0-10 | **单元测试**：在 `tests/unit/components/` 中测试 LLMExtractor | mock httpx 响应，验证：ObservationTable 列/行正确、空文件处理、AI 未配置报错、来源定位正确 |
| P0-11 | **Manifest 契约测试**：在 `tests/contract/test_component_manifest.py` 中补充 llm_extractor manifest 校验 | 有效 manifest 通过；缺少必填参数字段失败 |
| P0-12 | **端到端流程测试**：在 `tests/integration/components/` 中测试 llm_extractor → field_mapper → descriptive 完整链路 | mock LLM 响应，验证流程拓扑执行成功、各节点 status=succeeded、最终 ObservationTable 行数正确 |

### P1 — Should Have（应该有）

| ID | 需求 | 验收标准 |
|----|------|---------|
| P1-1 | **AI 助手工具注册**：在 `packages/ai/tools.py` 中注册白名单工具 `extract_data`（只读，auto_executable），描述"根据文件路径和提取指令从文件中提取结构化数据" | `ToolRegistry` 中包含该工具；AI 助手 provider-status 端点返回该工具；参数 schema 包含 path / prompt / schema |
| P1-2 | **AI 助手触发流程**：AI 助手识别"提取数据"意图后，自动构造 llm_extractor 节点参数并创建/运行流程 | 用户说"帮我提取这份 CSV 的粒度数据"，助手返回提取结果摘要（行数、列名、前几行预览） |
| P1-3 | **大文件分块**：文件内容超过模型上下文窗口时，自动分块发送（按行/按字符数截断） | 超长文件不报错，分块提取后合并结果；metadata 中记录分块数 |
| P1-4 | **JSON 响应容错**：大模型返回非标准 JSON（多余文字、markdown 包裹）时，自动提取 JSON 片段 | 提取成功率 > 95%；无法解析时返回 AppError 含原始响应摘要（截断） |
| P1-5 | **类型推断**：根据 schema 中的字段类型（string/number/integer/boolean），对大模型返回值做类型转换 | 数值字段转为 int/float；转换失败时保留原始字符串并在 diagnostics 中告警 |

### P2 — Nice to Have（可以有）

| ID | 需求 | 验收标准 |
|----|------|---------|
| P2-1 | **多格式文件支持**：除 CSV/纯文本外，支持读取 Excel（.xlsx）、JSON、PDF 表格内容作为 LLM 输入 | 能读取至少 2 种非 CSV 格式并提取数据 |
| P2-2 | **提取结果预览**：组件执行后在 metadata 中返回前 N 行预览与列统计 | metadata 包含 `preview_rows`（前 5 行）和 `column_types`（推断类型） |
| P2-3 | **Prompt 模板库**：预置常见提取场景的 prompt 模板（粒度数据、实验参数、物料配比），用户可选用 | 至少 3 个模板；模板通过参数 `prompt_template` 名称引用 |
| P2-4 | **重试机制**：LLM 调用失败时自动重试（可配置次数） | 重试次数通过参数 `max_retries` 配置（默认 2）；重试耗尽后抛 AppError |

---

## 5. UI 设计要点

> llm_extractor 是后端组件，无独立 UI 页面。UI 交互体现在**流程编排画布**与**AI 助手对话**两个已有界面中。

### 5.1 流程编排画布

- **节点选择器**：在"数据摄入"分类下新增 `llm_extractor` 节点，与 `csv_reader` 并列，图标建议用 AI/大模型相关标识区分。
- **节点参数面板**：选中 llm_extractor 节点时，右侧参数面板展示：
  - `path`：文件路径输入框（支持文件选择器）
  - `prompt`：多行文本框（提取指令，占位符示例："提取每一行的变量名、数值和单位"）
  - `schema`：JSON 编辑器 / 字段表格（定义目标字段名与类型）
  - `timeout`（可选）：超时秒数输入框，默认 120
- **端口连线**：输出端口 `observations` 可拖拽连接到 field_mapper / quality_check / descriptive 等下游节点的 `observations` 输入端口，连线类型与 csv_reader 完全一致。
- **替换操作**：右键已有 csv_reader 节点 → "替换为 llm_extractor"，自动保留下游连线，提示用户补填 prompt / schema 参数。

### 5.2 AI 助手对话

- 用户输入"帮我提取这份 CSV 的粒度数据"后，助手展示工具调用卡片：
  - 工具名：提取数据（extract_data）
  - 参数：path / prompt / schema（可编辑确认）
  - 状态：pending → running → succeeded
- 执行完成后，助手回复中嵌入提取结果摘要卡片：行数、列名列表、前 5 行预览表格。

---

## 6. 待确认问题

| # | 问题 | 影响范围 | 建议默认值 |
|---|------|---------|-----------|
| Q1 | **schema 参数格式**：用 JSON Schema（`{"type":"object","properties":{...}}`）还是简化字段列表（`[{"name":"x","type":"number"}]`）？ | manifest 参数定义、LLM prompt 构造、输出转换 | 建议用简化字段列表，降低用户填写门槛；内部转换为 JSON Schema 传给 LLM |
| Q2 | **大模型返回格式约定**：要求 LLM 返回 JSON 数组 `[{...}, {...}]` 还是包装对象 `{"rows": [...]}`？ | LLM prompt 设计、响应解析逻辑 | 建议要求返回 `{"rows": [{...}]}` 包装对象，便于扩展（未来加 metadata） |
| Q3 | **来源定位精确度**：source_locations 的 `row` 字段是 LLM 推断的原始行号还是输出序号？ | 可追溯性、质量检查定位 | P0 阶段用输出序号（从 1 递增）；P1 阶段尝试让 LLM 返回原始行号 |
| Q4 | **网络策略 allowed_hosts**：manifest 中写死允许的 host 还是从 AI 配置动态读取？ | 流程发布校验、安全性 | manifest 中声明通用网络策略；运行时由 FlowRuntimeService 注入实际 host 白名单（需确认引擎是否支持动态网络策略） |
| Q5 | **AI 助手工具权限**：`extract_data` 工具的 `required_permission` 设为什么？需新增权限还是复用 `ingestion:write`？ | 权限矩阵、角色配置 | 建议复用 `ingestion:write`，避免新增权限 |
| Q6 | **并发与成本控制**：大文件分块调用会消耗较多 token，是否需要单组件级 token / 调用次数上限？ | 成本、超时 | P0 不限制；P2 增加参数 `max_tokens` 和 `max_chunks` |
| Q7 | **Demo CSV 文件内容**：3 个 demo CSV 具体覆盖哪些工业研究场景？ | 测试覆盖度、文档示例 | 建议：①粒度分布数据（D10/D50/D90 + 单位）②实验参数表（温度/压力/时间）③物料配比表（成分/比例/批次） |
