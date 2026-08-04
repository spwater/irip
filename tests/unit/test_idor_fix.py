"""C-03 跨租户 IDOR 修复单元测试。

覆盖 T03 中为防止跨租户访问（IDOR）而增加的 department_id 过滤：
- ``packages/departments/repository.py`` — select_by_id / update / update_status /
  delete_by_id 增加 department_id 条件；
- ``packages/equipment/repository.py`` — select_by_id / update / update_status
  增加 department_id 条件；
- ``packages/components/registry.py`` — delete_component / activate_version
  增加 department_id 过滤。

测试策略：使用 AsyncMock 模拟数据库会话，验证生成的 SQL 语句 WHERE
子句中包含 ``department_id`` 过滤条件，确保跨租户查询不会命中其他
租户的数据。
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import sqlalchemy as sa

from packages.common.errors import AppError
from packages.components.registry import ComponentRegistryService
from packages.departments.repository import DepartmentRepository
from packages.equipment.repository import EquipmentRepository

# ---------------------------------------------------------------------------
# 辅助：编译语句并检查 WHERE 子句中是否包含 department_id
# ---------------------------------------------------------------------------


def _compiled_sql(stmt: sa.sql.Select | sa.sql.Update | sa.sql.Delete) -> str:
    """编译 SQLAlchemy 语句为 SQL 字符串（含参数占位符）。"""
    return str(stmt.compile(compile_kwargs={"literal_binds": False}))


def _has_department_filter(stmt: object) -> bool:
    """检查编译后的 SQL 是否包含 department_id 过滤条件。"""
    return "department_id" in _compiled_sql(stmt)


# ---------------------------------------------------------------------------
# Departments Repository
# ---------------------------------------------------------------------------


class TestDepartmentRepositoryIdorFix:
    """DepartmentRepository 的 department_id 租户隔离测试。

    阶段3退役后：department 表是结构数据（C 类），RLS 已禁用，
    不再需要 department_id 过滤条件。这些测试标记跳过。
    """

    @pytest.mark.skip(reason="阶段3: department 表是结构数据，不再按 department_id 过滤")
    @pytest.mark.skip(reason="阶段3: department 表是结构数据，不再按 department_id 过滤")
    async def test_select_by_id_includes_org_filter(self) -> None:
        """select_by_id 生成的 SQL 包含 department_id WHERE 条件。"""
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute.return_value = mock_result

        dept_id = uuid4()
        org_id = uuid4()

        result = await DepartmentRepository.select_by_id(session, dept_id, org_id)

        # 模拟跨租户查询返回 None
        assert result is None
        session.execute.assert_called_once()

        # 验证 WHERE 子句包含 department_id
        stmt = session.execute.call_args[0][0]
        assert _has_department_filter(stmt)

    @pytest.mark.skip(reason="阶段3: department 表是结构数据，不再按 department_id 过滤")
    async def test_select_by_id_wrong_org_returns_none(self) -> None:
        """带错误 org_id 查询时，模拟数据库返回 None。"""
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute.return_value = mock_result

        dept_id = uuid4()
        wrong_org = uuid4()

        result = await DepartmentRepository.select_by_id(session, dept_id, wrong_org)

        assert result is None

    @pytest.mark.skip(reason="阶段3: department 表是结构数据，不再按 department_id 过滤")
    async def test_update_includes_org_filter(self) -> None:
        """update 生成的 SQL 包含 department_id WHERE 条件。"""
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute.return_value = mock_result

        dept_id = uuid4()
        org_id = uuid4()

        result = await DepartmentRepository.update(
            session,
            department_id=dept_id,
            display_name="Updated",
            description=None,
            sort_order=0,
            lock_version=0,
        )

        # 模拟跨租户更新不命中（返回 None）
        assert result is None
        session.execute.assert_called_once()

        stmt = session.execute.call_args[0][0]
        assert _has_department_filter(stmt)

    @pytest.mark.skip(reason="阶段3: department 表是结构数据，不再按 department_id 过滤")
    async def test_update_wrong_org_returns_none(self) -> None:
        """带错误 org_id 更新时，模拟数据库不命中。"""
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute.return_value = mock_result

        result = await DepartmentRepository.update(
            session,
            department_id=uuid4(),
            display_name="X",
            description=None,
            sort_order=0,
            lock_version=0,
        )

        assert result is None

    @pytest.mark.skip(reason="阶段3: department 表是结构数据，不再按 department_id 过滤")
    async def test_update_status_includes_org_filter(self) -> None:
        """update_status 生成的 SQL 包含 department_id WHERE 条件。"""
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute.return_value = mock_result

        result = await DepartmentRepository.update_status(
            session,
            department_id=uuid4(),
            status="disabled",
            lock_version=0,
        )

        assert result is None
        stmt = session.execute.call_args[0][0]
        assert _has_department_filter(stmt)

    @pytest.mark.skip(reason="阶段3: department 表是结构数据，不再按 department_id 过滤")
    async def test_delete_by_id_includes_org_filter(self) -> None:
        """delete_by_id 生成的 SQL 包含 department_id WHERE 条件。"""
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.rowcount = 0
        session.execute.return_value = mock_result

        result = await DepartmentRepository.delete_by_id(session, uuid4(), uuid4())

        # 模拟跨租户删除不命中
        assert result is False
        stmt = session.execute.call_args[0][0]
        assert _has_department_filter(stmt)

    @pytest.mark.skip(reason="阶段3: department 表是结构数据，不再按 department_id 过滤")
    async def test_select_by_id_signature_has_org_param(self) -> None:
        """select_by_id 方法签名包含 department_id 参数。"""
        import inspect

        sig = inspect.signature(DepartmentRepository.select_by_id)
        assert "department_id" in sig.parameters

    @pytest.mark.skip(reason="阶段3: department 表是结构数据，不再按 department_id 过滤")
    async def test_update_signature_has_org_param(self) -> None:
        """update 方法签名包含 department_id 参数。"""
        import inspect

        sig = inspect.signature(DepartmentRepository.update)
        assert "department_id" in sig.parameters


# ---------------------------------------------------------------------------
# Equipment Repository
# ---------------------------------------------------------------------------


class TestEquipmentRepositoryIdorFix:
    """EquipmentRepository 的 department_id 租户隔离测试。"""

    async def test_select_by_id_includes_org_filter(self) -> None:
        """select_by_id 生成的 SQL 包含 department_id WHERE 条件。"""
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute.return_value = mock_result

        result = await EquipmentRepository.select_by_id(session, uuid4(), uuid4())

        assert result is None
        session.execute.assert_called_once()
        stmt = session.execute.call_args[0][0]
        assert _has_department_filter(stmt)

    async def test_select_by_id_wrong_org_returns_none(self) -> None:
        """带错误 org_id 查询时返回 None。"""
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute.return_value = mock_result

        result = await EquipmentRepository.select_by_id(session, uuid4(), uuid4())
        assert result is None

    async def test_update_includes_org_filter(self) -> None:
        """update 生成的 SQL 包含 department_id WHERE 条件。"""
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute.return_value = mock_result

        result = await EquipmentRepository.update(
            session,
            equipment_id=uuid4(),
            display_name="X",
            description=None,
            department_id=uuid4(),
            sort_order=0,
            lock_version=0,
        )

        assert result is None
        stmt = session.execute.call_args[0][0]
        assert _has_department_filter(stmt)

    async def test_update_wrong_org_returns_none(self) -> None:
        """带错误 org_id 更新时返回 None。"""
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute.return_value = mock_result

        result = await EquipmentRepository.update(
            session,
            equipment_id=uuid4(),
            display_name="X",
            description=None,
            department_id=uuid4(),
            sort_order=0,
            lock_version=0,
        )
        assert result is None

    async def test_update_status_includes_org_filter(self) -> None:
        """update_status 生成的 SQL 包含 department_id WHERE 条件。"""
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute.return_value = mock_result

        result = await EquipmentRepository.update_status(
            session,
            equipment_id=uuid4(),
            status="disabled",
            lock_version=0,
            department_id=uuid4(),
        )

        assert result is None
        stmt = session.execute.call_args[0][0]
        assert _has_department_filter(stmt)

    async def test_select_by_id_signature_has_org_param(self) -> None:
        """select_by_id 方法签名包含 department_id 参数。"""
        import inspect

        sig = inspect.signature(EquipmentRepository.select_by_id)
        assert "department_id" in sig.parameters

    async def test_update_signature_has_org_param(self) -> None:
        """update 方法签名包含 department_id 参数。"""
        import inspect

        sig = inspect.signature(EquipmentRepository.update)
        assert "department_id" in sig.parameters


# ---------------------------------------------------------------------------
# Components Registry — delete_component / activate_version
# ---------------------------------------------------------------------------


def _make_mock_session_scope(session: AsyncMock):
    """构造 mock session_scope 上下文管理器工厂。"""

    @asynccontextmanager
    async def _scope(factory):
        yield session

    return _scope


def _make_mock_scoped_session(session: AsyncMock):
    """构造 mock _scoped_session 上下文管理器（无参，用 service 内置 factory）。"""

    @asynccontextmanager
    async def _scope():
        yield session

    return _scope


class TestComponentRegistryIdorFix:
    """ComponentRegistryService 的 department_id 租户隔离测试。"""

    async def test_delete_component_wrong_org_raises_not_found(self) -> None:
        """delete_component 带错误 org_id（组件不存在于当前组织）抛 not_found。"""
        mock_session = AsyncMock()
        # 模拟跨租户查询：组件不属于当前组织 → 返回 None
        mock_session.scalar.return_value = None

        service = ComponentRegistryService(
            session_factory=MagicMock(),
            department_id=uuid4(),
            actor_id=uuid4(),
        )

        with patch.object(
            service,
            "_scoped_session",
            _make_mock_scoped_session(mock_session),
        ), patch(
            "packages.components.registry.registry.compute_visible_dept_ids",
            AsyncMock(return_value=[service.department_id]),
        ):
            with pytest.raises(AppError) as exc_info:
                await service.delete_component(uuid4())

        assert exc_info.value.code == "not_found"

    async def test_delete_component_correct_org_proceeds(self) -> None:
        """delete_component 带正确 org_id 时不抛异常（组件存在）。"""
        from packages.components.registry import Component

        mock_session = AsyncMock()
        # 模拟组件存在
        fake_component = Component(
            id=uuid4(),
            department_id=uuid4(),
            name="comp",
            kind="ingestion",
            status="published",
        )
        mock_session.scalar.return_value = fake_component
        mock_session.execute.return_value = MagicMock()

        service = ComponentRegistryService(
            session_factory=MagicMock(),
            department_id=fake_component.department_id,
            actor_id=uuid4(),
        )

        with patch.object(
            service,
            "_scoped_session",
            _make_mock_scoped_session(mock_session),
        ):
            # 不抛异常即通过
            await service.delete_component(fake_component.id)

    async def test_activate_version_wrong_org_raises_not_found(self) -> None:
        """activate_version 带错误 org_id（版本不属于当前组织）抛 not_found。"""
        mock_session = AsyncMock()
        # 模拟 JOIN + WHERE 返回 None（版本不属于当前组织）
        mock_session.scalar.return_value = None

        service = ComponentRegistryService(
            session_factory=MagicMock(),
            department_id=uuid4(),
            actor_id=uuid4(),
        )

        with patch.object(
            service,
            "_scoped_session",
            _make_mock_scoped_session(mock_session),
        ), patch(
            "packages.components.registry.registry.compute_visible_dept_ids",
            AsyncMock(return_value=[service.department_id]),
        ):
            with pytest.raises(AppError) as exc_info:
                await service.activate_version(uuid4())

        assert exc_info.value.code == "not_found"

    async def test_activate_version_query_includes_org_join(self) -> None:
        """activate_version 的查询通过 JOIN Component 确保 department_id 过滤。"""
        mock_session = AsyncMock()
        mock_session.scalar.return_value = None

        service = ComponentRegistryService(
            session_factory=MagicMock(),
            department_id=uuid4(),
            actor_id=uuid4(),
        )

        with patch.object(
            service,
            "_scoped_session",
            _make_mock_scoped_session(mock_session),
        ), patch(
            "packages.components.registry.registry.compute_visible_dept_ids",
            AsyncMock(return_value=[service.department_id]),
        ):
            with pytest.raises(AppError):
                await service.activate_version(uuid4())

        # 验证 scalar 被调用，且传入的语句编译后包含 department_id
        mock_session.scalar.assert_called_once()
        stmt = mock_session.scalar.call_args[0][0]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": False}))
        assert "department_id" in compiled

    async def test_delete_component_query_includes_org_filter(self) -> None:
        """delete_component 的 SELECT 查询包含 department_id WHERE 条件。"""
        mock_session = AsyncMock()
        mock_session.scalar.return_value = None

        service = ComponentRegistryService(
            session_factory=MagicMock(),
            department_id=uuid4(),
            actor_id=uuid4(),
        )

        with patch.object(
            service,
            "_scoped_session",
            _make_mock_scoped_session(mock_session),
        ), patch(
            "packages.components.registry.registry.compute_visible_dept_ids",
            AsyncMock(return_value=[service.department_id]),
        ):
            with pytest.raises(AppError):
                await service.delete_component(uuid4())

        mock_session.scalar.assert_called_once()
        stmt = mock_session.scalar.call_args[0][0]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": False}))
        assert "department_id" in compiled
