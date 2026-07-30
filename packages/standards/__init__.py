"""标准管理包：单位转换、标准变量、不可变版本、别名。

本包是 V1 粒度 L1->L3 证据链的基础层，提供：
- units: 基于 Decimal 的单位转换器（仿射变换 + 维度检查）；
- variables: 标准变量实体 + 不可变版本 + 别名 ORM 模型；
- state_machine: 标准状态机（draft -> in_review -> published -> deprecated）；
- repository: 数据访问层；
- service: 业务编排服务（创建 / 提交 / 发布 / 拒绝 / 弃用 / 重提）。
"""
# ruff: noqa: I001, F401
# 导入顺序固定（拓扑序）：state_machine -> variables -> methods
# -> templates -> packages -> objects。
# variables 子包内部依赖 state_machine；templates 依赖 variables；
# packages 依赖 methods/templates/variables（懒加载）；各子包内部顺序由各自 __init__.py 保证。
from packages.standards.state_machine import (
    StandardStatus,
    assert_transition,
)
from packages.standards.variables import (
    DataType,
    QuantityKind,
    StandardService,
    StandardsRepository,
    UnitConverter,
    Variable,
    VariableAlias,
    VariableStatus,
    VariableVersion,
)
from packages.standards.methods import (
    Method,
    MethodService,
    MethodStatus,
    MethodVersion,
)
from packages.standards.templates import (
    Cardinality,
    FactTemplate,
    FactTemplateVersion,
    FactType,
    ObservationRequirement,
    TemplateService,
    TemplateValidator,
    ValidationReport,
)
from packages.standards.packages import (
    PackageReference,
    PackageService,
    PackageStatus,
    PackageValidationReport,
    StandardPackage,
    StandardPackageVersion,
)
from packages.standards.objects import (
    HIERARCHICAL_RELATIONS,
    IndustrialObject,
    ObjectGraphService,
    ObjectRelation,
    ObjectType,
    ObjectTypeDict,
    RelationType,
)
