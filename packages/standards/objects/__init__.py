"""工业对象子包。"""
# ruff: noqa: I001, F401
# 导入顺序固定（拓扑序）：objects -> object_graph -> object_type_dict。
# object_graph 通过 from packages.standards.objects import X 引用包命名空间，
# 故 objects 必须先加载以绑定 IndustrialObject 等名称。
from packages.standards.objects.objects import (
    HIERARCHICAL_RELATIONS,
    IndustrialObject,
    ObjectRelation,
    ObjectType,
    RelationType,
)
from packages.standards.objects.object_graph import ObjectGraphService
from packages.standards.objects.object_type_dict import ObjectTypeDict
