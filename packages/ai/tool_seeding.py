"""AI 工具种子数据：表空时写入 12 条内置工具。

``seed_tools_if_empty(session)`` 在应用启动 lifespan 中调用（幂等）：
- 仅当 ``ai_tool`` 表行数为 0 时执行 INSERT；
- 种子源为 ``packages.ai.tools.ALL_TOOLS``（保留不变的硬编码元组）；
- 写入时 ``enabled=True``、``lock_version=0``、``updated_by=None``；
- 重复启动不重复写入；管理员后续修改不会被种子覆盖。

设计约定（架构设计文档 §7.5 / Q-3）。
"""

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from packages.ai.tool_repository import AITool
from packages.ai.tools import ALL_TOOLS, ToolSpec
from packages.common.ids import new_id


async def seed_tools_if_empty(session: AsyncSession) -> int:
    """表空时写入全部内置工具，幂等。

    Args:
        session: 异步会话（由调用方管理事务）。

    Returns:
        int: 本次写入的行数（表非空时返回 0）。
    """
    count_result = await session.execute(sa.select(sa.func.count()).select_from(AITool))
    count: int = count_result.scalar_one()
    if count > 0:
        return 0

    for spec in ALL_TOOLS:
        _insert_one(session, spec)

    await session.flush()
    return len(ALL_TOOLS)


def _insert_one(session: AsyncSession, spec: ToolSpec) -> None:
    """将单个 ToolSpec 映射为 AITool ORM 实体并加入会话。

    Args:
        session: 异步会话。
        spec: 工具规格（来自 ALL_TOOLS 元组）。
    """
    entity = AITool(
        id=new_id(),
        name=spec.name,
        display_name=spec.display_name,
        description=spec.description,
        required_permission=spec.required_permission,
        parameters_schema=spec.parameters_schema,
        category=spec.category,
        enabled=True,
        lock_version=0,
        updated_by=None,
    )
    session.add(entity)
