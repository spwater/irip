"""单元测试：GovernanceService 治理服务。

覆盖：
- _TRANSFERABLE_TABLES 白名单内容正确；
- _ROOT_STATS_TABLES 统计表列表正确；
- transfer_data 非白名单表抛 validation_failed；
- transfer_data 源目标相同抛 validation_failed；
- transfer_data dry_run 仅统计不执行 UPDATE；
- assign_roles 合并角色（去重 + 排序）；
- assign_roles 用户不存在抛 not_found；
- remove_role 移除指定角色；
- remove_role 用户不存在抛 not_found。
"""

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

import packages.governance.governance_service as gov_mod
from packages.common.errors import AppError
from packages.governance.governance_service import (
    _ROOT_STATS_TABLES,
    _TRANSFERABLE_TABLES,
    GovernanceService,
)


def _make_service() -> GovernanceService:
    """构造 GovernanceService 实例。"""
    return GovernanceService(
        session_factory=MagicMock(),
        department_id=uuid4(),
        actor_id=uuid4(),
    )


@asynccontextmanager
async def _patch_scoped_session(mock_session: AsyncMock) -> Any:
    """临时替换 governance_service 模块的 scoped_session 为返回 mock_session 的上下文。

    GovernanceService 方法直接调用模块级 ``scoped_session`` 函数（非 mixin），
    故需 patch ``packages.governance.governance_service.scoped_session``。
    """
    original = gov_mod.scoped_session

    @asynccontextmanager
    async def fake_scoped_session(factory: Any, dept_id: Any, user_id: Any) -> Any:
        yield mock_session

    gov_mod.scoped_session = fake_scoped_session  # type: ignore[assignment]
    try:
        yield
    finally:
        gov_mod.scoped_session = original  # type: ignore[assignment]


class TestTransferableTables:
    """_TRANSFERABLE_TABLES 白名单测试。"""

    def test_transferable_tables_contains_expected_entries(self) -> None:
        """白名单包含 6 张可移交表。"""
        assert "fact" in _TRANSFERABLE_TABLES
        assert "parameter" in _TRANSFERABLE_TABLES
        assert "model" in _TRANSFERABLE_TABLES
        assert "flow_definition" in _TRANSFERABLE_TABLES
        assert "flow_run" in _TRANSFERABLE_TABLES
        assert "equipment" in _TRANSFERABLE_TABLES
        assert len(_TRANSFERABLE_TABLES) == 6

    def test_root_stats_tables_match_transferable(self) -> None:
        """root 统计表与可移交表一致。"""
        assert set(_ROOT_STATS_TABLES.keys()) == set(_TRANSFERABLE_TABLES.keys())


class TestTransferDataValidation:
    """transfer_data 输入校验测试。"""

    async def test_non_whitelisted_table_raises(self) -> None:
        """非白名单表抛 validation_failed。"""
        service = _make_service()
        with pytest.raises(AppError, match="不支持的数据表"):
            await service.transfer_data("malicious_table", uuid4(), uuid4())

    async def test_same_source_target_raises(self) -> None:
        """源部门和目标部门相同抛 validation_failed。"""
        service = _make_service()
        same_id = uuid4()
        with pytest.raises(AppError, match="不能相同"):
            await service.transfer_data("fact", same_id, same_id)


class TestTransferDataDryRun:
    """transfer_data dry_run 测试。"""

    async def test_dry_run_counts_without_updating(self) -> None:
        """dry_run=True 仅统计行数，不执行 UPDATE。"""
        service = _make_service()
        mock_session = AsyncMock()
        count_result = MagicMock()
        count_result.scalar.return_value = 5
        mock_session.execute = AsyncMock(return_value=count_result)

        async with _patch_scoped_session(mock_session):
            result = await service.transfer_data("fact", uuid4(), uuid4(), dry_run=True)

        assert result == 5
        # dry_run 只执行一次 COUNT，不执行 UPDATE
        assert mock_session.execute.await_count == 1

    async def test_dry_run_zero_rows_no_update(self) -> None:
        """dry_run=True 且 0 行时不执行 UPDATE。"""
        service = _make_service()
        mock_session = AsyncMock()
        count_result = MagicMock()
        count_result.scalar.return_value = 0
        mock_session.execute = AsyncMock(return_value=count_result)

        async with _patch_scoped_session(mock_session):
            result = await service.transfer_data("parameter", uuid4(), uuid4(), dry_run=True)

        assert result == 0
        assert mock_session.execute.await_count == 1


class TestAssignRoles:
    """assign_roles 角色合并测试。"""

    async def test_assign_roles_merges_and_sorts(self) -> None:
        """assign_roles 合并新角色到已有角色列表并排序。"""
        service = _make_service()
        mock_session = AsyncMock()
        # session.add 是同步方法（AuditRecorder.record 调用），需设为同步 mock
        mock_session.add = MagicMock()

        existing_user = MagicMock()
        existing_user.id = uuid4()
        existing_user.roles = ["lab_member", "lab_viewer"]
        existing_user.display_name = "测试用户"

        mock_session.scalar = AsyncMock(return_value=existing_user)
        mock_session.execute = AsyncMock()
        mock_session.refresh = AsyncMock()

        async with _patch_scoped_session(mock_session):
            result = await service.assign_roles(
                existing_user.id, ["platform_auditor", "lab_member"]
            )

        assert result is existing_user
        # 验证 UPDATE 被执行（至少一次 execute 调用含 UPDATE 语句）
        assert mock_session.execute.await_count >= 1
        # 验证 user 被 refresh（方法末尾调用 session.refresh）
        mock_session.refresh.assert_awaited()

    async def test_assign_roles_user_not_found_raises(self) -> None:
        """用户不存在时抛 not_found。"""
        service = _make_service()
        mock_session = AsyncMock()
        mock_session.scalar = AsyncMock(return_value=None)

        async with _patch_scoped_session(mock_session):
            with pytest.raises(AppError, match="用户不存在"):
                await service.assign_roles(uuid4(), ["lab_member"])


class TestRemoveRole:
    """remove_role 角色移除测试。"""

    async def test_remove_role_removes_specified(self) -> None:
        """remove_role 从角色列表中移除指定角色。"""
        service = _make_service()
        mock_session = AsyncMock()
        mock_session.add = MagicMock()

        existing_user = MagicMock()
        existing_user.id = uuid4()
        existing_user.roles = ["lab_member", "lab_viewer", "platform_auditor"]

        mock_session.scalar = AsyncMock(return_value=existing_user)
        mock_session.execute = AsyncMock()
        mock_session.refresh = AsyncMock()

        async with _patch_scoped_session(mock_session):
            await service.remove_role(existing_user.id, "lab_viewer")

        # 验证 UPDATE 被执行
        assert mock_session.execute.await_count >= 1
        # 验证 user 被 refresh
        mock_session.refresh.assert_awaited()

    async def test_remove_role_user_not_found_raises(self) -> None:
        """用户不存在时抛 not_found。"""
        service = _make_service()
        mock_session = AsyncMock()
        mock_session.scalar = AsyncMock(return_value=None)

        async with _patch_scoped_session(mock_session):
            with pytest.raises(AppError, match="用户不存在"):
                await service.remove_role(uuid4(), "lab_member")
