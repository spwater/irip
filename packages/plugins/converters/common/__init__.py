"""Converter 公共模块。

提供跨插件共享的基础设施：
- ``text_extractor``: 文本/图片提取（从 llm_converter 迁移），供
  ``llm_converter`` 与 ``component_preview`` 共享。
- ``paddle_ocr_utils``: PaddleOCR PP-StructureV3 模型懒加载、调用封装
  与结果解析，供 ``image_converter`` 与 ``pdf_converter`` 共享。

将公共逻辑下沉到此包可消除跨模块私有函数引用（如原
``component_preview.py`` 引用 ``llm_converter._extract_text``），
避免代码重复。
"""
