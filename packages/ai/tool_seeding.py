"""AI 工具种子数据：逐个补齐缺失的内置工具。

``seed_missing_builtin_tools(session)`` 在应用启动 lifespan 中调用（幂等）：
- 遍历 ``packages.ai.tools.ALL_TOOLS``，逐个按 name 检查是否存在；
- 缺失则 INSERT，已存在的不更新（管理员编辑不被覆盖）；
- 返回本次新插入的行数。

设计约定：
- 0079 迁移为已有环境插入两个数值工具；
- 本函数为新安装在跑完迁移后补齐全部代码内置工具；
- 未来新增工具不再依赖"表必须为空"的偶然条件。
"""

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from packages.ai.tool_repository import AITool
from packages.ai.tools import ALL_TOOLS, ToolSpec
from packages.common.ids import new_id


async def seed_missing_builtin_tools(session: AsyncSession) -> int:
    """逐个补齐缺失的内置工具，幂等。

    遍历 ALL_TOOLS，对每个工具按 name 检查是否已存在，
    缺失则 INSERT，已存在的不更新。

    Args:
        session: 异步会话（由调用方管理事务）。

    Returns:
        int: 本次新插入的行数。
    """
    inserted = 0

    for spec in ALL_TOOLS:
        # 按 name 检查是否已存在
        existing = await session.execute(sa.select(AITool.id).where(AITool.name == spec.name))
        if existing.scalar_one_or_none() is not None:
            continue  # 已存在，不覆盖

        _insert_one(session, spec)
        inserted += 1

    if inserted > 0:
        await session.flush()

    return inserted


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
