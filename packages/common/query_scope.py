"""统一查询范围值对象：QueryScope。

所有列表/单对象查询通过 QueryScope 自动应用部门过滤，
禁止先查全量再在 Python 中过滤。

使用约定（技术设计文档 §8.2）：
1. 所有列表查询端点必须通过 ``QueryScope.apply()`` 应用过滤；
2. 禁止先查全量再在 Python 中过滤；
3. ScopeGrant 授权检查通过 ``AuthorizationService.require`` 执行；
4. 默认拒绝：无匹配 ScopeGrant 时返回 403。
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import sqlalchemy as sa


@dataclass(frozen=True)
class QueryScope:
    """统一查询范围，自动应用部门过滤。

    应用层快路径：按 department_id 等值过滤（RLS 是第二道防线）。

    由认证依赖构造，传入所有应用服务和 Repository 方法，
    确保所有查询都带有部门隔离条件。

    Attributes:
        department_id: 当前部门 ID（必填，租户隔离基础）。
        object_root_id: 工业对象根 ID（可选，用于对象级过滤）。
    """

    department_id: UUID
    object_root_id: UUID | None = None

    def apply(self, query: sa.Select, entity_cls: type[Any] | None = None) -> sa.Select:  # type: ignore[type-arg]
        """将 scope 条件应用到 SQLAlchemy 查询。

        按 department_id 等值过滤（应用层快路径）。
        RLS 策略（database 层）作为第二道防线，确保即使应用层遗漏也能过滤。

        Args:
            query: SQLAlchemy SELECT 语句。
            entity_cls: 实体类（需有 department_id 列），
                为 None 时仅返回原查询（调用方需自行过滤）。

        Returns:
            sa.Select: 带有部门过滤条件的查询语句。
        """
        if entity_cls is None:
            return query

        dept_col = getattr(entity_cls, "department_id", None)
        if dept_col is not None:
            query = query.where(dept_col == self.department_id)

        if self.object_root_id is not None:
            obj_col = getattr(entity_cls, "object_root_id", None)
            if obj_col is not None:
                query = query.where(obj_col == self.object_root_id)

        return query
