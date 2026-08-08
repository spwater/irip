"""单元测试：dept_visibility 部门可见性计算。

覆盖：
- compute_visible_dept_ids：actor_id + dept_id 路径 / 仅 actor_id / 仅 dept_id /
  两者都 None；
- _coerce_uuid：UUID 对象 / 字符串转换。

使用 mock AsyncSession。
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from packages.common.dept_visibility import _coerce_uuid, compute_visible_dept_ids


def _make_result(rows: list[tuple[Any, ...]] | None = None) -> MagicMock:
    result = MagicMock()
    result.fetchall.return_value = [(r[0],) for r in (rows or [])]
    return result


class TestCoerceUuid:
    """_coerce_uuid 测试。"""

    def test_uuid_passthrough(self) -> None:
        """UUID 对象直接返回。"""
        uid = uuid4()
        assert _coerce_uuid(uid) == uid

    def test_string_to_uuid(self) -> None:
        """字符串转为 UUID。"""
        uid = uuid4()
        assert _coerce_uuid(str(uid)) == uid


class TestComputeVisibleDeptIds:
    """compute_visible_dept_ids 测试。"""

    async def test_both_actor_and_dept(self) -> None:
        """同时有 actor_id 和 dept_id：取并集。"""
        session = AsyncMock()
        dept_id = uuid4()
        actor_id = uuid4()
        dept_scope_id = uuid4()
        user_scope_id = uuid4()

        session.execute = AsyncMock(
            side_effect=[
                _make_result([(user_scope_id,)]),
                _make_result([(dept_scope_id,)]),
            ]
        )

        with (
            patch("packages.common.dept_visibility.set_dept_guc", new_callable=AsyncMock),
            patch("packages.common.dept_visibility.set_user_guc", new_callable=AsyncMock),
        ):
            result = await compute_visible_dept_ids(session, dept_id, actor_id)

        assert dept_id in result
        assert user_scope_id in result
        assert dept_scope_id in result

    async def test_only_actor(self) -> None:
        """仅有 actor_id：纯按用户可见集。"""
        session = AsyncMock()
        actor_id = uuid4()
        scope_id = uuid4()

        session.execute = AsyncMock(return_value=_make_result([(scope_id,)]))

        with patch("packages.common.dept_visibility.set_user_guc", new_callable=AsyncMock):
            result = await compute_visible_dept_ids(session, None, actor_id)

        assert scope_id in result

    async def test_only_dept(self) -> None:
        """仅有 dept_id：按 dept 递归。"""
        session = AsyncMock()
        dept_id = uuid4()
        child_id = uuid4()

        session.execute = AsyncMock(return_value=_make_result([(child_id,)]))

        with patch("packages.common.dept_visibility.set_dept_guc", new_callable=AsyncMock):
            result = await compute_visible_dept_ids(session, dept_id, None)

        assert dept_id in result
        assert child_id in result

    async def test_both_none(self) -> None:
        """两者都 None：返回空列表。"""
        session = AsyncMock()
        result = await compute_visible_dept_ids(session, None, None)
        assert result == []

    async def test_dept_always_in_result(self) -> None:
        """dept_id 始终在结果中。"""
        session = AsyncMock()
        dept_id = uuid4()
        session.execute = AsyncMock(return_value=_make_result([]))

        with patch("packages.common.dept_visibility.set_dept_guc", new_callable=AsyncMock):
            result = await compute_visible_dept_ids(session, dept_id, None)

        assert dept_id in result

    async def test_deduplication(self) -> None:
        """结果去重。"""
        session = AsyncMock()
        dept_id = uuid4()
        actor_id = uuid4()

        session.execute = AsyncMock(
            side_effect=[
                _make_result([(dept_id,)]),
                _make_result([(dept_id,)]),
            ]
        )

        with (
            patch("packages.common.dept_visibility.set_dept_guc", new_callable=AsyncMock),
            patch("packages.common.dept_visibility.set_user_guc", new_callable=AsyncMock),
        ):
            result = await compute_visible_dept_ids(session, dept_id, actor_id)

        assert result.count(dept_id) == 1
