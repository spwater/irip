"""IRIP 插件系统。

提供独立的插件目录，与主系统解耦。每个插件实现标准接口，
通过 registry 注册后由组件系统调用。

目录结构::

    packages/plugins/
        __init__.py          — 包入口（本文件，含插件规范说明）
        protocol.py          — 标准插件接口定义
        registry.py          — 插件注册表（name → plugin）
        converters/          — 解析器插件
            xrd_converter/   — XRD RAS/RAW 解析器
            llm_converter/   — 大模型解析器

插件规范
========

每个 converter 插件**必须且只能**包含 2 个文件::

    converters/<name>/
        __init__.py       — Python 包入口（空注释即可）
        converter.py      — 全部逻辑（一个文件搞定一件事）

converter.py 规范
-----------------

一个 converter 就干一件事：**输入 file_path，输出 {metadata, points, series}**。

结构::

    converter.py
    ├── 异常定义（自定义异常类，继承 Exception）
    ├── 内部工具函数（类型转换、文件读取等私有函数，下划线前缀）
    ├── 核心解析函数 parse(file_path) -> dict   ← 纯同步函数，干活的
    ├── 入口函数 convert(file_path) -> dict      ← 薄封装，调 parse + 日志
    └── 插件类 XxxConverter                     ← 实现 ConverterProtocol
        └── async execute(params) -> dict        ← registry 调用入口
            → asyncio.to_thread(convert, file_path)
            → ConverterResult(...).to_dict()

调用链路::

    组件层 → registry.get(name).execute(params)
           → asyncio.to_thread(convert, file_path)
           → parse(file_path)
           → {metadata, points, series}

新增解析器流程
-------------

1. 在 ``converters/`` 下创建目录，只放 ``__init__.py`` + ``converter.py``
2. ``converter.py`` 实现异常 + 解析 + 入口 + 插件类
3. 在 ``registry.py`` 的 ``_auto_register()`` 中加一行 ``register(name, XxxConverter())``
4. 在 ``ai_tool`` 表插入一条 ``category='ingestion'`` 记录（工具插件页面管理）

主系统代码完全不需要改动。
"""
