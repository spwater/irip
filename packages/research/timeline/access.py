"""统一工作空间所有权 Guard。

集中所有研究时间线操作的工作空间所有权校验，确保跨用户、跨工作空间
的访问一律 fail-closed（返回 not_found），不泄露资源存在性。

所有 Service 层在操作研究工作空间或其子资源（Turn、Snapshot 等）前，
必须先调用本模块的 guard 函数进行所有权验证。

设计原则：
  - fail-closed：不存在或不属于当前用户 → not_found（而非 forbidden），
    避免泄露资源存在性。
  - 复合条件查询：require_owned_turn 同时匹配 workspace_id 和 turn_id，
    防止跨工作空间 ID 混淆攻击。
  - 单一入口：所有 Service 复用本模块，消除 30+ 处重复所有权检查。
"""

from __future__ import annotations

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from packages.common.errors import AppError
from packages.research.entities import ResearchWorkspace
from packages.research.repository.workspace import WorkspaceRepository
from packages.research.timeline.entities import ResearchTurn


async def require_owned_workspace(
    session: AsyncSession,
    workspace_id: UUID,
    actor_id: UUID | None,
) -> ResearchWorkspace:
    """验证工作空间所有权，返回工作空间实体。

    调用 WorkspaceRepository.get_workspace 按 owner 过滤查询。
    若工作空间不存在或不属于该用户，抛出 not_found 错误（fail-closed）。

    Args:
        session: 异步数据库会话。
        workspace_id: 工作空间 ID。
        actor_id: 当前操作者用户 ID（用于所有权过滤）。

    Returns:
        ResearchWorkspace: 已验证所有权的工作空间实体。

    Raises:
        AppError: code="not_found"，当工作空间不存在或不属于该用户时。
    """
    workspace = await WorkspaceRepository.get_workspace(
        session,
        workspace_id,
        actor_id,
    )
    if workspace is None:
        raise AppError(
            code="not_found",
            message="研究工作空间不存在",
            retryable=False,
            fields={"workspace_id": str(workspace_id)},
        )
    return workspace


async def require_owned_turn(
    session: AsyncSession,
    workspace_id: UUID,
    turn_id: UUID,
    actor_id: UUID | None,
) -> ResearchTurn:
    """验证工作空间所有权和 Turn 归属，返回 Turn 实体。

    先调用 require_owned_workspace 验证 workspace 所有权，
    再查询该 workspace 下的 turn（同时匹配 workspace_id 和 turn_id）。
    若任一验证失败，抛出 not_found 错误（fail-closed）。

    Args:
        session: 异步数据库会话。
        workspace_id: 工作空间 ID。
        turn_id: 研究轮次 ID。
        actor_id: 当前操作者用户 ID（用于所有权过滤）。

    Returns:
        ResearchTurn: 已验证归属的研究轮次实体。

    Raises:
        AppError: code="not_found"，当工作空间或 Turn 不存在/不属于该用户时。
    """
    await require_owned_workspace(session, workspace_id, actor_id)

    result = await session.execute(
        sa.select(ResearchTurn).where(
            ResearchTurn.id == turn_id,
            ResearchTurn.workspace_id == workspace_id,
        )
    )
    turn = result.scalar_one_or_none()
    if turn is None:
        raise AppError(
            code="not_found",
            message="研究工作空间不存在",
            retryable=False,
            fields={"turn_id": str(turn_id)},
        )
    return turn
