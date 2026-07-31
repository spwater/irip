"""标准管理包：工业对象图与对象类型字典。

本包原包含变量/模板/包/单位转换等标准层组件，但这些表全部为空且
相关代码已删除（migration 0057 DROP）。当前仅保留：

- objects: 工业对象（IndustrialObject）/ 对象类型字典（ObjectTypeDict）/
  对象图服务（ObjectGraphService），有 6 条数据在用；
- methods: 空子包（migration 0056 已废弃，保留空 __init__.py）；
- 顶层 shim 文件 object_graph.py / object_type_dict.py，供其他模块通过
  顶层路径导入。
"""
# ruff: noqa: F401
from packages.standards.objects import (
    HIERARCHICAL_RELATIONS,
    IndustrialObject,
    ObjectGraphService,
    ObjectType,
    ObjectTypeDict,
    RelationType,
)
