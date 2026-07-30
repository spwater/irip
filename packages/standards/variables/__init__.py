"""标准变量子包。"""
# ruff: noqa: I001, F401
# 导入顺序固定（拓扑序）：variables -> repository -> service -> units。
# repository/service 通过 from packages.standards.variables import X 引用包命名空间，
# 故 variables 必须先加载以绑定 Variable 等名称，否则触发 ImportError。
from packages.standards.variables.variables import (
    DataType,
    QuantityKind,
    Variable,
    VariableAlias,
    VariableStatus,
    VariableVersion,
)
from packages.standards.variables.repository import StandardsRepository
from packages.standards.variables.service import StandardService
from packages.standards.variables.units import UnitConverter
