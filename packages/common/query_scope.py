"""统一查询范围值对象：QueryScope。

所有列表/单对象查询通过 QueryScope 自动应用组织/部门/对象根过滤，
禁止先查全量再在 Python 中过滤。

使用约定（技术设计文档 §8.2）：
1. 所有列表查询端点必须通过 ``QueryScope.apply()`` 应用过滤；
2. 禁止先查全量再在 Python 中过滤；
3. ScopeGrant 授权检查通过 ``AuthorizationService.require`` 执行；
4. 默认拒绝：无匹配 ScopeGrant 时返回 403。
"""

from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa


@dataclass(frozen=True)
class QueryScope:
    """统一查询范围，自动应用组织/部门/对象根过滤。

    由认证依赖构造，传入所有应用服务和 Repository 方法，
    确保所有查询都带有组织隔离条件。

    Attributes:
        organization_id: 当前组织 ID（必填，租户隔离基础）。
        department_id: 部门/实验室 ID（可选，用于部门级过滤）。
        object_root_id: 工业对象根 ID（可选，用于对象级过滤）。
        resource_type: 资源类型通配符（默认 "*" 表示所有类型）。
    """

    organization_id: UUID
    department_id: UUID | None = None
    object_root_id: UUID | None = None
    resource_type: str = "*"

    def apply(self, query: sa.Select, entity_cls: type | None = None) -> sa.Select:
        """将 scope 条件应用到 SQLAlchemy 查询。

        当前简化实现：仅应用 organization_id 过滤。
        后续阶段（T1-2）将完善 department_id / object_root_id 过滤。

        Args:
            query: SQLAlchemy SELECT 语句。
            entity_cls: 实体类（需有 organization_id 列），
                为 None 时仅返回原查询（调用方需自行过滤）。

        Returns:
            sa.Select: 带有组织过滤条件的查询语句。
        """
        if entity_cls is None:
            return query

        org_col = getattr(entity_cls, "organization_id", None)
        if org_col is not None:
            query = query.where(org_col == self.organization_id)

        if self.department_id is not None:
            dept_col = getattr(entity_cls, "department_id", None)
            if dept_col is not None:
                query = query.where(dept_col == self.department_id)

        return query
