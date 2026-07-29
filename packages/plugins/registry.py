"""插件注册表。

通过 ``register(name, plugin)`` 注册插件，
通过 ``get(name)`` 按名称获取插件实例。

新增解析器只需：
1. 在 ``converters/`` 下创建目录 + 实现 ``plugin.py``
2. 在此处注册一行
3. 在 ai_tool 表插入一条 ``category=ingestion`` 记录
"""

from packages.plugins.protocol import ConverterProtocol

#: 插件注册表：name → plugin 实例
_registry: dict[str, ConverterProtocol] = {}


def register(name: str, plugin: ConverterProtocol) -> None:
    """注册插件。

    Args:
        name: 插件名称（与 ai_tool 表的 name 一致）。
        plugin: 实现 ConverterProtocol 的插件实例。
    """
    _registry[name] = plugin


def get(name: str) -> ConverterProtocol | None:
    """按名称获取插件。

    Args:
        name: 插件名称。

    Returns:
        插件实例，未注册返回 None。
    """
    return _registry.get(name)


def list_plugins() -> list[str]:
    """列出全部已注册的插件名称。"""
    return sorted(_registry.keys())


# ---- 自动注册内置插件 ----


def _auto_register() -> None:
    """注册全部内置解析器插件。"""
    from packages.plugins.converters.llm_converter.converter import LlmConverter
    from packages.plugins.converters.xrd_converter.converter import XrdConverter

    register("xrd_converter", XrdConverter())
    register("llm_converter", LlmConverter())


_auto_register()
