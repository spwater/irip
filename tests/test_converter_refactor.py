"""Converter 重构 —— 精简版测试套件（v3.0）。

v3.0 变更：合并回 llm_converter — 对外只有 xrd_converter + llm_converter 两个选项。
- 删除 4 个新 converter（pdf/excel/word/image）— 它们本质上是 llm_converter 的前置提取器
- 保留公共模块：text_extractor.py（文本提取）+ llm_utils.py（LLM 调用）
- llm_converter 使用公共模块，内部提取策略不变
- registry 只注册 2 个插件

验证内容：
1. 模块导入（公共模块、llm_converter、xrd_converter、注册表、种子数据）
2. text_extractor 功能测试
3. llm_utils 参数校验测试
4. 异常体系验证
5. 注册表 + 种子数据验证
"""

import asyncio
import os
import sys
import tempfile

_PROJECT_ROOT = "/Users/shuipei/Desktop/snowSP/irip"
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


# ============================================================
# 测试 1：模块导入 + 注册表 + 种子数据
# ============================================================


def test_imports():
    """验证模块导入、注册表只有 2 个插件、种子数据只有 2 个 ingestion 工具。"""
    from packages.plugins.converters.common.text_extractor import extract_text
    from packages.plugins.converters.common.llm_utils import call_llm_for_structured
    from packages.plugins.converters.llm_converter.converter import LlmConverter, LlmConverterError
    from packages.plugins.converters.xrd_converter.converter import XrdConverter
    from packages.plugins import registry as plugin_registry

    # 注册表只有 2 个插件
    assert sorted(plugin_registry.list_plugins()) == ["llm_converter", "xrd_converter"], \
        f"注册表插件不符: {sorted(plugin_registry.list_plugins())}"

    # 种子数据只有 2 个 ingestion 工具
    from packages.ai.tools import PLUGIN_TOOLS
    ingestion_tools = [t for t in PLUGIN_TOOLS if t.category == "ingestion"]
    assert len(ingestion_tools) == 2, f"ingestion 工具数应为 2，实际 {len(ingestion_tools)}"
    assert {t.name for t in ingestion_tools} == {"xrd_converter", "llm_converter"}


# ============================================================
# 测试 2：text_extractor 功能测试
# ============================================================


def test_text_extractor_txt():
    """测试文本文件提取。"""
    from pathlib import Path
    from packages.plugins.converters.common.text_extractor import extract_text

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False) as f:
            f.write("hello world")
            f.flush()
            tmp_path = f.name
        result = extract_text(Path(tmp_path))
        assert result == "hello world", f"文本提取结果不符: {result!r}"
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


def test_text_extractor_unknown_format():
    """测试不支持的格式 → 直接读取文本。"""
    from pathlib import Path
    from packages.plugins.converters.common.text_extractor import extract_text

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".unknown", mode="w", delete=False) as f:
            f.write("test content")
            f.flush()
            tmp_path = f.name
        result = extract_text(Path(tmp_path))
        assert "test content" in result, f"未知格式提取结果应包含原文: {result!r}"
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


# ============================================================
# 测试 3：llm_utils 参数校验
# ============================================================


def test_llm_utils_no_prompt():
    """call_llm_for_structured 无 prompt 应抛 AppError。"""
    from packages.common.errors import AppError
    from packages.plugins.converters.common.llm_utils import call_llm_for_structured

    try:
        asyncio.run(call_llm_for_structured("content", "", None))
        assert False, "应抛出 AppError"
    except AppError as e:
        assert "prompt" in e.message.lower() or "缺少" in e.message


def test_llm_utils_no_ai_config():
    """call_llm_for_structured 无 ai_config 应抛 AppError。"""
    from packages.common.errors import AppError
    from packages.plugins.converters.common.llm_utils import call_llm_for_structured

    try:
        asyncio.run(call_llm_for_structured("content", "test prompt", None))
        assert False, "应抛出 AppError"
    except AppError as e:
        assert "配置" in e.message or "configured" in e.message.lower()


def test_llm_utils_empty_content():
    """空内容应返回空结果（不调 LLM）。"""
    from packages.plugins.converters.common.llm_utils import call_llm_for_structured

    result = asyncio.run(call_llm_for_structured(
        "", "prompt", {"base_url": "x", "api_key": "y", "model_name": "z"}
    ))
    assert result == {"metadata": {}, "points": [], "series": []}


# ============================================================
# 测试 4：异常体系验证
# ============================================================


def test_exception_hierarchy():
    """验证 llm_converter 异常体系。"""
    from packages.plugins.converters.llm_converter.converter import LlmConverterError
    assert issubclass(LlmConverterError, Exception)


# ============================================================
# 独立运行入口
# ============================================================


def _run_all_tests():
    test_functions = [
        ("test_imports", test_imports),
        ("test_text_extractor_txt", test_text_extractor_txt),
        ("test_text_extractor_unknown_format", test_text_extractor_unknown_format),
        ("test_llm_utils_no_prompt", test_llm_utils_no_prompt),
        ("test_llm_utils_no_ai_config", test_llm_utils_no_ai_config),
        ("test_llm_utils_empty_content", test_llm_utils_empty_content),
        ("test_exception_hierarchy", test_exception_hierarchy),
    ]

    passed = 0
    failed = 0
    for name, func in test_functions:
        try:
            func()
            print(f"  PASS  {name}")
            passed += 1
        except Exception as exc:
            print(f"  FAIL  {name}: {exc}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n总计 {len(test_functions)} | 通过 {passed} | 失败 {failed}")
    return failed == 0


if __name__ == "__main__":
    success = _run_all_tests()
    sys.exit(0 if success else 1)
