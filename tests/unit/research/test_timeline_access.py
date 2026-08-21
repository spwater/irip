"""Tests for unified workspace ownership guards.

验证 require_owned_workspace 和 require_owned_turn 的 fail-closed 行为：
跨用户、跨工作空间访问一律返回 not_found，不泄露资源存在性。
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from packages.common.errors import AppError
from packages.research.entities import ResearchWorkspace
from packages.research.timeline.access import require_owned_turn, require_owned_workspace


class TestRequireOwnedWorkspace:
    """require_owned_workspace guard tests."""

    async def test_require_owned_workspace_masks_foreign_workspace(self) -> None:
        """访问他人 workspace 返回 not_found（fail-closed，不泄露存在性）。"""
        session = AsyncMock()
        workspace_id = uuid4()
        actor_id = uuid4()

        with patch(
            "packages.research.timeline.access.WorkspaceRepository.get_workspace",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with pytest.raises(AppError, match="研究工作空间不存在") as exc_info:
                await require_owned_workspace(session, workspace_id, actor_id)

        assert exc_info.value.code == "not_found"

    async def test_require_owned_workspace_returns_workspace(self) -> None:
        """正常访问返回 workspace 对象。"""
        session = AsyncMock()
        workspace_id = uuid4()
        actor_id = uuid4()
        workspace = MagicMock(spec=ResearchWorkspace)
        workspace.id = workspace_id

        with patch(
            "packages.research.timeline.access.WorkspaceRepository.get_workspace",
            new_callable=AsyncMock,
            return_value=workspace,
        ):
            result = await require_owned_workspace(session, workspace_id, actor_id)

        assert result is workspace


class TestRequireOwnedTurn:
    """require_owned_turn guard tests."""

    async def test_require_owned_turn_checks_workspace_and_owner(self) -> None:
        """跨 workspace 访问 turn 返回 not_found（fail-closed）。

        workspace 所有权验证通过，但 turn 不属于该 workspace → not_found。
        """
        session = AsyncMock()
        workspace_id = uuid4()
        turn_id = uuid4()
        actor_id = uuid4()
        workspace = MagicMock(spec=ResearchWorkspace)

        with patch(
            "packages.research.timeline.access.WorkspaceRepository.get_workspace",
            new_callable=AsyncMock,
            return_value=workspace,
        ):
            # Simulate turn query returning None (turn not in this workspace)
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = None
            session.execute = AsyncMock(return_value=mock_result)

            with pytest.raises(AppError, match="研究工作空间不存在") as exc_info:
                await require_owned_turn(session, workspace_id, turn_id, actor_id)

        assert exc_info.value.code == "not_found"
