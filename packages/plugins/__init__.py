"""IRIP 插件系统。

提供独立的插件目录，与主系统解耦。每个插件实现标准接口，
通过 registry 注册后由组件系统调用。

目录结构::

    packages/plugins/
        __init__.py          — 包入口
        protocol.py          — 标准插件接口定义
        registry.py           — 插件注册表（name → plugin）
        converters/           — 解析器插件
            xrd_converter/    — XRD RAS/RAW 解析器
            llm_converter/     — 大模型解析器（占位）
"""
