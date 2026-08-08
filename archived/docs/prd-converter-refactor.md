# IRIP Converter 插件化重构需求说明书

## 1. 项目信息

| 项目 | 内容 |
|------|------|
| 语言 | 中文 |
| 编程语言 | Python（FastAPI + 插件化架构） |
| 项目名称 | irip-converter-refactor |
| 原始需求 | 将 `llm_converter` 拆分为按文件类型的独立 converter 插件，引入 PaddleOCR PP-StructureV3 做图片/PDF 表格识别主力，LLM 退为兜底 |

## 2. 项目背景与现状

### 2.1 系统概述

IRIP（工业研究智能平台）的 converter 插件系统位于 `packages/plugins/converters/`，通过注册表模式实现文件解析的插件化管理。当前有 2 个插件：

| 插件 | 职责 | 依赖 |
|------|------|------|
| `xrd_converter` | XRD RAS/RAW 确定性解析 | 纯算法，不依赖 LLM |
| `llm_converter` | 大模型解析（PDF/Excel/Word/图片/文本所有格式） | 提取文本后调 LLM 转结构化数据 |

### 2.2 现有插件规范

- **目录结构**：`converters/<name>/converter.py` + `__init__.py`，无其他文件
- **统一接口**：输入 `file_path`，输出 `{metadata, points, series}`
- **内部结构**：异常定义 → 内部工具函数 → 核心解析 `parse()` → 入口函数 `convert()` → 插件类 `XxxConverter.execute()`
- **调用链路**：组件层 → `registry.get(name).execute(params)` → `asyncio.to_thread(convert, file_path)` → `parse(file_path)` → `{metadata, points, series}`

### 2.3 当前 llm_converter 的问题

| 问题编号 | 描述 |
|----------|------|
| C-01 | 缺少自定义异常定义，统一用 `AppError`，无法区分不同失败场景 |
| C-02 | 缺少独立的 `parse()` 和 `convert()` 函数分层，逻辑全在 `execute()` 内联 |
| C-03 | `execute` 方法逻辑过重：文本提取 + LLM 请求构建 + 调用 + 响应解析全在内联 |
| C-04 | 一个插件处理所有格式（PDF/Excel/Word/图片/文本），耦合度高 |
| C-05 | `component_preview.py` 直接 import 了 `llm_converter.converter._extract_text`，存在跨模块私有函数引用 |

### 2.4 调用方分析

| 调用方 | 调用方式 | 注意事项 |
|--------|----------|----------|
| `ez_scan_extractor.py` | `registry.get(tool_type).execute({**params, "file_path": ..., "ai_config": ai_config})` | 仅当 `tool_type == "llm_converter"` 时注入 ai_config |
| `xrd_tool_component.py` | `registry.get("xrd_converter").execute({"file_path": ...})` | 确定性解析，不需要 ai_config |
| `component_preview.py` | `registry.get(tool_type).execute(...)` + 直接 import `_extract_text` | 跨模块引用私有函数，重构时需处理 |

## 3. 产品目标

> 将 `llm_converter` 的全格式解析能力拆分为按文件类型的独立 converter 插件，引入 PaddleOCR 作为图片/PDF 表格识别主力引擎，LLM 降级为兜底方案，实现**低耦合、高精度、零主系统改动**的插件化文件解析体系。

| 目标 | 衡量标准 |
|------|----------|
| G1: 解耦 | `llm_converter` 不再承担确定性提取职责，每种文件类型有独立插件 |
| G2: 精度提升 | 图片/PDF 表格识别由 PaddleOCR PP-StructureV3 承担，LLM 仅在兜底时调用 |
| G3: 零改动 | 主系统（组件层、路由层）代码不变，新增插件仅通过 registry 注册 + ai_tool 表插入 |

## 4. 用户故事

| 编号 | 角色 | 场景 | 期望 |
|------|------|------|------|
| US-01 | 科研人员 | 上传 Excel 检测报告（.xlsx） | 系统直接用 openpyxl 提取数据，无需 LLM，速度快、精度高、零 token 消耗 |
| US-02 | 科研人员 | 上传扫描版 PDF 或拍照图片 | 系统用 PaddleOCR PP-StructureV3 识别文字和表格，本地部署、免费、精度高 |
| US-03 | 研发工程师 | 上传非标准格式文件（无法被任何专用插件解析） | 系统自动 fallback 到 LLM 兜底插件，确保不会因格式问题完全无法处理 |

## 5. 插件架构设计

### 5.1 目标插件结构

```
converters/
├── xrd_converter/           # 已有，XRD RAS/RAW 确定性解析（不动）
├── pdf_converter/           # 新建，PDF 文字提取（pymupdf）+ 表格（PaddleOCR）
├── excel_converter/         # 新建，Excel 直接提取（openpyxl，不需要 LLM）
├── word_converter/          # 新建，Word 直接提取（python-docx，不需要 LLM）
├── image_converter/         # 新建，图片 OCR + 表格识别（PaddleOCR PP-StructureV3）
├── llm_converter/           # 保留为兜底，重构为仅处理 LLM 调用逻辑
```

### 5.2 各插件职责定义

| 插件 | 输入文件类型 | 核心依赖 | 是否需要 LLM | 职责说明 |
|------|-------------|----------|-------------|----------|
| `xrd_converter` | .ras / .raw | 纯算法 | 否 | XRD RAS/RAW 确定性解析（已有，不动） |
| `pdf_converter` | .pdf | pymupdf + PaddleOCR | 否（文字层充足时）/ 是（需 OCR 时调 PaddleOCR，非 LLM） | 先 pymupdf 提取文字层，文字不足则 PaddleOCR PP-StructureV3 识别表格 |
| `excel_converter` | .xls / .xlsx | openpyxl | 否 | 直接读取工作表，按 metadata/points/series 分类输出 |
| `word_converter` | .doc / .docx | python-docx | 否 | 提取段落文本 + 表格内容，按 metadata/points/series 分类输出 |
| `image_converter` | .jpg / .jpeg / .png | PaddleOCR PP-StructureV3 | 否 | OCR 文字识别 + 表格结构识别 |
| `llm_converter` | 兜底所有格式 | httpx（调 LLM API） | 是 | 其他插件识别失败时的兜底，保留 ai_config/prompt 参数 |

### 5.3 插件内部结构规范

每个新建插件遵循现有 `xrd_converter` 的分层模式：

```python
# converter.py 内部结构
# 1. 异常定义（自定义异常类，继承自基础异常）
class XxxConverterError(Exception): ...
class UnsupportedFileFormatError(XxxConverterError): ...
class FileReadError(XxxConverterError): ...

# 2. 内部工具函数（私有，下划线前缀）
def _read_file(file_path: str) -> ...: ...
def _extract_xxx(file_path: Path) -> ...: ...

# 3. 核心解析函数
def parse(file_path: str) -> dict[str, Any]: ...

# 4. 入口函数
def convert(file_path: str) -> dict[str, Any]: ...

# 5. 插件类
class XxxConverter:
    async def execute(self, params: dict[str, Any]) -> dict[str, Any]: ...
```

## 6. 接口设计

### 6.1 统一输入接口

所有 converter 插件的 `execute(params)` 接收统一参数字典：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file_path` | `str` | 是 | 文件路径（已由组件层下载到本地） |
| `ai_config` | `dict \| None` | 否 | AI 配置（base_url / api_key / model_name），仅 `llm_converter` 需要 |
| `prompt` | `str` | 否 | LLM 提示词，仅 `llm_converter` 需要 |
| `file_engine` | `str` | 否 | 文件读取方式（auto/pymupdf/image/raw），默认 auto |
| `image_dpi` | `int` | 否 | PDF 转图片 DPI，默认 200 |
| `timeout` | `int` | 否 | LLM 超时秒数，默认 300 |

### 6.2 统一输出格式

所有插件返回 `ConverterResult.to_dict()`：

```json
{
  "metadata": {},
  "points": [],
  "series": []
}
```

| 字段 | 类型 | 说明 | 映射到 IRIP 数据模型 |
|------|------|------|---------------------|
| `metadata` | `dict[str, Any]` | 单值标头信息（报告级公共信息） | 标头字段 |
| `points` | `list[dict]` | 单点数据，每项 `{"name": str, "value": Any, "unit": str}` | 每行一条 `fact_data_index` |
| `series` | `list[dict]` | 序列数据，每项 `{"name": str, "columns": list[str], "rows": list[list]}` | 整组一条 `observation` |

### 6.3 异常接口

每个插件定义自己的异常体系，继承自各自的基础异常类：

```
XxxConverterError（基础异常）
├── UnsupportedFileFormatError    # 不支持的文件格式
├── FileReadError                  # 文件读取失败
├── ParseError                     # 解析失败
└── DependencyMissingError         # 依赖未安装（如 PaddleOCR、openpyxl）
```

调用方通过 `AppError` 包装或直接捕获插件异常，按 `code` 区分处理逻辑。

## 7. 路由逻辑设计

### 7.1 文件类型 → 插件映射表

| 文件后缀 | 目标插件 | 提取策略 | 需要 ai_config |
|----------|----------|----------|---------------|
| `.ras` / `.raw` | `xrd_converter` | 确定性算法解析 | 否 |
| `.pdf` | `pdf_converter` | pymupdf 提文字 → 文字不足时 PaddleOCR | 否 |
| `.xls` / `.xlsx` | `excel_converter` | openpyxl 直接读取 | 否 |
| `.doc` / `.docx` | `word_converter` | python-docx 直接读取 | 否 |
| `.jpg` / `.jpeg` / `.png` | `image_converter` | PaddleOCR PP-StructureV3 | 否 |
| 其他 / 未知 | `llm_converter` | LLM 兜底 | 是 |

### 7.2 Fallback 机制

```
文件上传
  │
  ├─ 按后缀匹配目标插件
  │    │
  │    ├─ 插件解析成功 → 返回 {metadata, points, series}
  │    │
  │    └─ 插件解析失败（抛异常或返回空结果）
  │         │
  │         └─ Fallback 到 llm_converter
  │              │
  │              ├─ LLM 解析成功 → 返回结果
  │              └─ LLM 解析失败 → 抛出 AppError
  │
  └─ 后缀未匹配任何插件 → 直接路由到 llm_converter
```

**Fallback 触发条件**：
1. 专用插件抛出异常（文件格式不支持、依赖缺失、解析失败）
2. 专用插件返回空结果（metadata/points/series 全空）
3. 文件后缀未在映射表中

**Fallback 前提**：
- `llm_converter` 需要 `ai_config` 和 `prompt` 参数
- 调用方在 fallback 时需注入 ai_config（已有逻辑：`ez_scan_extractor.py` 在 `tool_type == "llm_converter"` 时注入）
- 如果 ai_config 未配置，抛出 `AppError(code="ai_not_configured")`

### 7.3 路由实现方式

路由逻辑应在**调用方**（`ez_scan_extractor.py` / `component_preview.py`）或**中间路由层**实现，而非在插件内部实现，以保持插件单一职责。两种方案：

| 方案 | 优点 | 缺点 |
|------|------|------|
| A: 调用方自行路由 | 改动集中在调用方 | 多个调用方需重复路由逻辑 |
| B: 新增 `converter_router` 模块统一路由 | 调用方一行调用，逻辑集中 | 新增一个模块（但不改主系统逻辑） |

**推荐方案 B**：在 `packages/plugins/` 下新增 `router.py`，提供 `route_and_convert(file_path, params)` 统一入口，内部按后缀选插件 + fallback。

## 8. 需求池

### P0: Must Have（必须有）

| 编号 | 需求 | 验收标准 |
|------|------|----------|
| P0-01 | 新建 `excel_converter` 插件 | openpyxl 读取 .xls/.xlsx，输出 {metadata, points, series}，不需要 LLM |
| P0-02 | 新建 `word_converter` 插件 | python-docx 读取 .doc/.docx，输出 {metadata, points, series}，不需要 LLM |
| P0-03 | 新建 `image_converter` 插件 | PaddleOCR PP-StructureV3 识别 .jpg/.jpeg/.png，输出 {metadata, points, series} |
| P0-04 | 新建 `pdf_converter` 插件 | pymupdf 提文字层，文字不足时 PaddleOCR 识别表格，输出 {metadata, points, series} |
| P0-05 | 重构 `llm_converter` 为兜底插件 | 仅保留 LLM 调用逻辑，移除文件格式分发，保留 ai_config/prompt 参数 |
| P0-06 | 每个新插件遵循规范 | 异常定义 → 工具函数 → parse() → convert() → 插件类 execute()，自定义异常 |
| P0-07 | registry.py 注册新插件 | `_auto_register()` 中新增 4 个插件注册行 |
| P0-08 | ai_tool 表插入记录 | 为每个新插件插入 `category=ingestion` 记录 |
| P0-09 | 主系统零改动 | 组件层、路由层代码不修改（仅新增 router 模块时新增 import） |
| P0-10 | 处理 `component_preview.py` 的 `_extract_text` 引用 | 将 `_extract_text` 提取为公共模块或由各 converter 提供等价能力，消除跨模块私有函数引用 |

### P1: Should Have（应该有）

| 编号 | 需求 | 验收标准 |
|------|------|----------|
| P1-01 | 新增 `converter_router` 统一路由模块 | 提供 `route_and_convert(file_path, params)` 入口，按后缀选插件 + fallback 到 llm_converter |
| P1-02 | Fallback 机制实现 | 专用插件失败时自动 fallback 到 llm_converter，需注入 ai_config |
| P1-03 | PaddleOCR 模型懒加载 | 首次调用时加载模型，避免启动时阻塞 |
| P1-04 | pdf_converter 文字量阈值可配置 | "文字不足"的阈值（当前 avg_chars < 50）通过参数可配 |
| P1-05 | 依赖缺失优雅降级 | PaddleOCR/openpyxl/python-docx 未安装时给出明确错误提示，不影响其他插件 |

### P2: Nice to Have（可以有）

| 编号 | 需求 | 验收标准 |
|------|------|----------|
| P2-01 | PaddleOCR 识别结果置信度过滤 | 低置信度结果标记 warning 或 fallback 到 LLM |
| P2-02 | converter 插件健康检查接口 | 提供依赖检测 API，返回各插件可用状态 |
| P2-03 | 解析性能日志 | 记录每个插件的解析耗时、结果条数 |
| P2-04 | 多页 PDF 并行 OCR | 大型 PDF 多页 PaddleOCR 并行处理 |

## 9. 技术依赖

| 依赖 | 用途 | 安装方式 | 备注 |
|------|------|----------|------|
| `paddleocr` | PaddleOCR PP-StructureV3 | `pip install paddleocr -i https://mirror.baidu.com/pypi/simple` | 国内源安装 |
| `paddlepaddle` | PaddleOCR 底层框架 | `pip install paddlepaddle -i https://mirror.baidu.com/pypi/simple` | 国内源安装 |
| `pymupdf` (fitz) | PDF 文字层提取 | 已有依赖 | llm_converter 已使用 |
| `openpyxl` | Excel 读取 | 已有依赖 | llm_converter 已使用 |
| `python-docx` | Word 读取 | 已有依赖 | llm_converter 已使用 |
| `httpx` | LLM API 调用 | 已有依赖 | llm_converter 已使用 |

## 10. 注册流程（新增解析器 4 步）

```
① 建目录写 converter.py + __init__.py
        │
        ▼
② registry.py _auto_register() 注册一行
        │
        ▼
③ ai_tool 表插 category=ingestion 记录
        │
        ▼
④ 主系统零改动
```

## 11. 待确认问题

| 编号 | 问题 | 影响范围 | 建议 |
|------|------|----------|------|
| Q-01 | **PaddleOCR Python 3.13 兼容性**：当前项目使用 Python 3.13（见 `.venv/lib/python3.13`），PaddleOCR / PaddlePaddle 对 Python 3.13 的支持可能不完善。是否需要降级 Python 版本或使用 Docker 郔离 PaddleOCR 服务？ | image_converter / pdf_converter | 先验证 PaddleOCR 在 Python 3.13 下是否可安装和运行；若不兼容，考虑 Docker 部署 PaddleOCR 服务 + HTTP API 调用 |
| Q-02 | **`_extract_text` 提取方案**：`component_preview.py` 直接 import 了 `llm_converter.converter._extract_text`。重构后该函数应放哪里？ | component_preview.py / llm_converter | 方案 A：提取到 `packages/plugins/converters/common/text_extractor.py` 公共模块；方案 B：由各 converter 提供等价能力，component_preview 按后缀路由到对应 converter |
| Q-03 | **路由层位置**：fallback 路由逻辑放在调用方（ez_scan_extractor / component_preview）还是新增统一 router 模块？ | 所有调用方 | 推荐新增 `packages/plugins/router.py` 统一路由模块，调用方一行调用，避免重复逻辑 |
| Q-04 | **PaddleOCR 模型存储位置**：PaddleOCR PP-StructureV3 模型文件较大，首次下载需指定存储路径。是否统一放在项目目录或用户目录？ | image_converter / pdf_converter | 建议放在 `~/.paddleocr/` 或项目级 `.models/paddleocr/`，通过环境变量配置 |
| Q-05 | **Excel/Word 解析的结构化分类策略**：openpyxl/python-docx 提取的是原始单元格/段落文本，如何自动分类为 metadata（单值标头）/ points（单点指标）/ series（序列数据）？是否需要简单的启发式规则，还是仍需 LLM 辅助分类？ | excel_converter / word_converter | 方案 A：纯启发式规则（表头行→metadata，单值→points，多行表格→series）；方案 B：提取后调 LLM 做分类（但违背"不需要 LLM"目标）。需确认用户期望 |
| Q-06 | **Fallback 时 prompt 来源**：专用插件失败 fallback 到 llm_converter 时，prompt 从哪里来？当前 prompt 由用户在前端配置，fallback 时是否使用同一 prompt？ | 调用方 / router | 建议 fallback 时复用调用方传入的 prompt 参数；若无 prompt 则使用默认提示词 |
| Q-07 | **ai_tool 表记录的具体字段**：新增 4 个插件的 ai_tool 表记录需要哪些字段？是否需要对应的前端 UI 变更（如下拉选项增加新插件）？ | 数据库 / 前端 | 需确认 ai_tool 表结构及前端是否需要同步更新 tool_type 选项 |
| Q-08 | **.doc（旧格式）支持**：python-docx 仅支持 .docx，不支持 .doc（二进制格式）。.doc 文件如何处理？ | word_converter | 方案 A：.doc 路由到 llm_converter 兜底；方案 B：引入 `antiword` 或 `textract` 库；方案 C：用 LibreOffice headless 转换。需确认 |
