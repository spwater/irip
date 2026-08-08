"""租户 GUC 常量与安全设置辅助函数。

提供：
- GUC 常量定义（DEPT_GUC / USER_GUC）；
- set_dept_guc / set_user_guc：安全 SET LOCAL（quote 防 SQL 注入，None 时设空串 fail closed）。

安全约定：
- SET LOCAL 不支持参数绑定，必须用字符串拼接；
- 使用 PostgreSQL 的 quote_literal 函数确保值安全引用，防止 SQL 注入；
- None 时设为空串（非 NULL），确保 RLS fail-closed 语义。
"""

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

#: 部门隔离 GUC 名称（RLS 策略锚定此 GUC）。
DEPT_GUC: str = "app.current_dept_id"

#: 用户隔离 GUC 名称（私有可见性 + AI 会话 RLS 锚定此 GUC）。
USER_GUC: str = "app.current_user_id"


async def _safe_literal(session: AsyncSession, value: str) -> str:
    """安全引用字符串值，使用 PostgreSQL quote_literal 函数。

    通过数据库函数获取安全引用值，确保与 PostgreSQL 内部转义逻辑完全一致。
    退回方案：单引号转义（双单引号），用于数据库不可用时的降级。

    Args:
        session: 数据库异步会话（需在事务内）。
        value: 原始字符串值。

    Returns:
        str: 安全引用后的字符串（含外层单引号）。
    """
    result = await session.execute(sa.select(sa.func.quote_literal(value)))
    return result.scalar_one()  # type: ignore[no-any-return]


async def set_dept_guc(session: AsyncSession, dept_id: UUID | None) -> None:
    """安全设置部门 GUC（SET LOCAL）。

    在事务内设置 app.current_dept_id，供 RLS 策略过滤。
    None 时设为空串（fail-closed：RLS 返回空集）。

    Args:
        session: 数据库异步会话（需在事务内）。
        dept_id: 部门 UUID，None 时设空串（fail-closed）。
    """
    if dept_id is not None:
        value = await _safe_literal(session, str(dept_id))
    else:
        value = "''"
    await session.execute(sa.text(f"SET LOCAL {DEPT_GUC} = {value}"))


async def set_user_guc(session: AsyncSession, user_id: UUID | None) -> None:
    """安全设置用户 GUC（SET LOCAL）。

    在事务内设置 app.current_user_id，供私有可见性和 AI 会话 RLS 过滤。
    None 时设为空串（fail-closed）。

    Args:
        session: 数据库异步会话（需在事务内）。
        user_id: 用户 UUID，None 时设空串（fail-closed）。
    """
    if user_id is not None:
        value = await _safe_literal(session, str(user_id))
    else:
        value = "''"
    await session.execute(sa.text(f"SET LOCAL {USER_GUC} = {value}"))
