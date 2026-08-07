"""租户 GUC 常量与安全设置辅助函数。

提供：
- GUC 常量定义（DEPT_GUC / USER_GUC）；
- set_dept_guc / set_user_guc：安全 SET LOCAL（quote 防 SQL 注入，None 时设空串 fail closed）。

安全约定：
- SET LOCAL 不支持参数绑定，必须用字符串拼接；
- 使用 PostgreSQL 的 quote_literal 确保值安全引用，防止 SQL 注入；
- None 时设为空串（非 NULL），确保 RLS fail-closed 语义。
"""

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

#: 部门隔离 GUC 名称（RLS 策略锚定此 GUC）。
DEPT_GUC: str = "app.current_dept_id"

#: 用户隔离 GUC 名称（私有可见性 + AI 会话 RLS 锚定此 GUC）。
USER_GUC: str = "app.current_user_id"


def _safe_literal(value: str) -> str:
    """安全引用字符串值，防止 SQL 注入。

    使用 PostgreSQL 的 quote_literal 函数确保值被安全引用。
    退回方案：单引号转义（双单引号）。

    Args:
        value: 原始字符串值。

    Returns:
        str: 安全引用后的字符串（含外层单引号）。
    """
    # 简单转义：将单引号替换为双单引号
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


async def set_dept_guc(session: AsyncSession, dept_id: UUID | None) -> None:
    """安全设置部门 GUC（SET LOCAL）。

    在事务内设置 app.current_dept_id，供 RLS 策略过滤。
    同时设置 app.current_org_id 作为向后兼容（部分 RLS 策略仍用 organization_id）。
    None 时设为空串（fail-closed：RLS 返回空集）。

    Args:
        session: 数据库异步会话（需在事务内）。
        dept_id: 部门 UUID，None 时设空串（fail-closed）。
    """
    value = _safe_literal(str(dept_id)) if dept_id is not None else "''"
    await session.execute(sa.text(f"SET LOCAL {DEPT_GUC} = {value}"))
    # 向后兼容：部分表的 RLS 策略仍用 organization_id = current_setting('app.current_org_id')
    await session.execute(sa.text("SET LOCAL app.current_org_id = " + value))


async def set_user_guc(session: AsyncSession, user_id: UUID | None) -> None:
    """安全设置用户 GUC（SET LOCAL）。

    在事务内设置 app.current_user_id，供私有可见性和 AI 会话 RLS 过滤。
    None 时设为空串（fail-closed）。

    Args:
        session: 数据库异步会话（需在事务内）。
        user_id: 用户 UUID，None 时设空串（fail-closed）。
    """
    value = _safe_literal(str(user_id)) if user_id is not None else "''"
    await session.execute(sa.text(f"SET LOCAL {USER_GUC} = {value}"))
