"""Unit tests for packages.components.flow.flow_fact_service.

Tests FlowFactService methods with mocked sessions:
- resolve_artifact_filename
- check_artifact_exists
- get_task_snapshot
- write_fact_data_index
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from packages.components.flow.flow_fact_service import (
    FlowFactService,
    TaskSnapshot,
)


class _MockSessionCtx:
    """Helper to build a fake _scoped_session that yields mock_session."""

    def __init__(self, mock_session):
        self.mock_session = mock_session

    def __call__(self):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _ctx():
            yield self.mock_session

        return _ctx()


@pytest.fixture
def mock_session() -> AsyncMock:
    """A mock AsyncSession."""
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    return session


@pytest.fixture
def service(mock_session: AsyncMock) -> FlowFactService:
    """FlowFactService with mocked scoped_session."""
    svc = FlowFactService(
        session_factory=MagicMock(),
        department_id=UUID("00000000-0000-0000-0000-000000000001"),
        actor_id=UUID("00000000-0000-0000-0000-000000000002"),
    )
    # Patch _scoped_session to yield our mock
    svc._scoped_session = _MockSessionCtx(mock_session)  # type: ignore[method-assign]
    return svc


class TestResolveArtifactFilename:
    """Tests for resolve_artifact_filename."""

    async def test_returns_filename_when_found(
        self, service: FlowFactService, mock_session: AsyncMock
    ) -> None:
        art_mock = MagicMock()
        art_mock.filename = "data.json"
        mock_session.scalar.return_value = art_mock

        result = await service.resolve_artifact_filename(
            UUID("00000000-0000-0000-0000-000000000010")
        )
        assert result == "data.json"

    async def test_returns_none_when_not_found(
        self, service: FlowFactService, mock_session: AsyncMock
    ) -> None:
        mock_session.scalar.return_value = None
        result = await service.resolve_artifact_filename(
            UUID("00000000-0000-0000-0000-000000000011")
        )
        assert result is None

    async def test_returns_none_on_exception(
        self, service: FlowFactService, mock_session: AsyncMock
    ) -> None:
        mock_session.scalar.side_effect = RuntimeError("DB error")
        result = await service.resolve_artifact_filename(
            UUID("00000000-0000-0000-0000-000000000012")
        )
        assert result is None


class TestCheckArtifactExists:
    """Tests for check_artifact_exists."""

    async def test_exists(self, service: FlowFactService, mock_session: AsyncMock) -> None:
        mock_session.scalar.return_value = UUID("00000000-0000-0000-0000-000000000020")
        result = await service.check_artifact_exists(UUID("00000000-0000-0000-0000-000000000020"))
        assert result is True

    async def test_not_exists(self, service: FlowFactService, mock_session: AsyncMock) -> None:
        mock_session.scalar.return_value = None
        result = await service.check_artifact_exists(UUID("00000000-0000-0000-0000-000000000021"))
        assert result is False

    async def test_false_on_exception(
        self, service: FlowFactService, mock_session: AsyncMock
    ) -> None:
        mock_session.scalar.side_effect = RuntimeError("DB error")
        result = await service.check_artifact_exists(UUID("00000000-0000-0000-0000-000000000022"))
        assert result is False


class TestGetTaskSnapshot:
    """Tests for get_task_snapshot."""

    async def test_returns_empty_snapshot_on_no_flow_version(
        self, service: FlowFactService, mock_session: AsyncMock
    ) -> None:
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        snapshot = await service.get_task_snapshot(UUID("00000000-0000-0000-0000-000000000030"), {})
        assert snapshot.task_code is None
        assert snapshot.task_name is None

    async def test_returns_snapshot_with_flow_data(
        self, service: FlowFactService, mock_session: AsyncMock
    ) -> None:
        fv_mock = MagicMock()
        fv_mock.id = UUID("00000000-0000-0000-0000-000000000031")
        fv_mock.flow_definition_id = UUID("00000000-0000-0000-0000-000000000032")
        fv_mock.nodes_json = []

        fd_mock = MagicMock()
        fd_mock.id = UUID("00000000-0000-0000-0000-000000000032")
        fd_mock.code = "flow_code_1"
        fd_mock.display_name = "Flow Display"
        fd_mock.operator = "operator1"
        fd_mock.department_id = UUID("00000000-0000-0000-0000-000000000040")

        dept_mock = MagicMock()
        dept_mock.display_name = "Test Dept"

        # Execute is called multiple times for different queries
        # First: FlowDefinitionVersionORM query -> fv_mock
        # Second: FlowDefinition query -> fd_mock
        # Third (optional): Department query -> dept_mock
        fv_result = MagicMock()
        fv_result.scalar_one_or_none.return_value = fv_mock
        fd_result = MagicMock()
        fd_result.scalar_one_or_none.return_value = fd_mock
        dept_result = MagicMock()
        dept_result.scalar_one_or_none.return_value = dept_mock

        mock_session.execute.side_effect = [fv_result, fd_result, dept_result]

        snapshot = await service.get_task_snapshot(
            UUID("00000000-0000-0000-0000-000000000031"),
            {"_operator": "runner_op"},
        )
        assert snapshot.task_code == "flow_code_1"
        assert snapshot.task_name == "Flow Display"
        assert snapshot.operator == "operator1"
        assert snapshot.run_operator == "runner_op"
        assert snapshot.department_name == "Test Dept"

    async def test_snapshot_with_equipment(
        self, service: FlowFactService, mock_session: AsyncMock
    ) -> None:
        fv_mock = MagicMock()
        fv_mock.flow_definition_id = UUID("00000000-0000-0000-0000-000000000052")
        fv_mock.nodes_json = [{"component_name": "comp_1"}]

        fd_mock = MagicMock()
        fd_mock.code = "fc"
        fd_mock.display_name = "FD"
        fd_mock.operator = "op"
        fd_mock.department_id = UUID("00000000-0000-0000-0000-000000000060")

        dept_mock = MagicMock()
        dept_mock.display_name = "Dept"

        fv_result = MagicMock()
        fv_result.scalar_one_or_none.return_value = fv_mock
        fd_result = MagicMock()
        fd_result.scalar_one_or_none.return_value = fd_mock
        eq_result = MagicMock()
        eq_result.first.return_value = ("Equipment Name",)
        dept_result = MagicMock()
        dept_result.scalar_one_or_none.return_value = dept_mock

        mock_session.execute.side_effect = [fv_result, fd_result, eq_result, dept_result]

        snapshot = await service.get_task_snapshot(
            UUID("00000000-0000-0000-0000-000000000050"),
            {},
        )
        assert snapshot.equipment_name == "Equipment Name"
        assert snapshot.department_name == "Dept"

    async def test_exception_returns_empty_snapshot(
        self, service: FlowFactService, mock_session: AsyncMock
    ) -> None:
        mock_session.execute.side_effect = RuntimeError("DB error")
        snapshot = await service.get_task_snapshot(UUID("00000000-0000-0000-0000-000000000070"), {})
        assert snapshot.task_code is None
        assert snapshot.task_name is None


class TestWriteFactDataIndex:
    """Tests for write_fact_data_index."""

    async def test_writes_numeric_and_text(
        self, service: FlowFactService, mock_session: AsyncMock
    ) -> None:
        points = [
            {"name": "temp", "value": 42.5},
            {"name": "label", "value": "hello"},
            {"name": "skip", "value": None},
        ]
        await service.write_fact_data_index(UUID("00000000-0000-0000-0000-000000000080"), points)
        mock_session.execute.assert_called_once()
        insert_stmt = mock_session.execute.call_args[0][0]
        # Verify it was an insert
        assert hasattr(insert_stmt, "compile")

    async def test_empty_points_no_write(
        self, service: FlowFactService, mock_session: AsyncMock
    ) -> None:
        await service.write_fact_data_index(UUID("00000000-0000-0000-0000-000000000081"), [])
        mock_session.execute.assert_not_called()

    async def test_all_none_values_no_write(
        self, service: FlowFactService, mock_session: AsyncMock
    ) -> None:
        await service.write_fact_data_index(
            UUID("00000000-0000-0000-0000-000000000082"),
            [{"name": "a", "value": None}, {"name": "b", "value": None}],
        )
        mock_session.execute.assert_not_called()

    async def test_non_dict_point_skipped(
        self, service: FlowFactService, mock_session: AsyncMock
    ) -> None:
        points: list[dict] = [  # type: ignore[misc]
            "not_a_dict",  # type: ignore[list-item]
            {"name": "valid", "value": 1},
        ]
        await service.write_fact_data_index(UUID("00000000-0000-0000-0000-000000000083"), points)
        mock_session.execute.assert_called_once()

    async def test_exception_no_raise(
        self, service: FlowFactService, mock_session: AsyncMock
    ) -> None:
        mock_session.execute.side_effect = RuntimeError("DB error")
        # Should not raise
        await service.write_fact_data_index(
            UUID("00000000-0000-0000-0000-000000000084"),
            [{"name": "x", "value": 1}],
        )

    async def test_integer_value_stored_as_number(
        self, service: FlowFactService, mock_session: AsyncMock
    ) -> None:
        await service.write_fact_data_index(
            UUID("00000000-0000-0000-0000-000000000085"),
            [{"name": "count", "value": 100}],
        )
        mock_session.execute.assert_called_once()


class TestTaskSnapshotDataclass:
    """Tests for TaskSnapshot dataclass defaults."""

    def test_defaults_all_none(self) -> None:
        snap = TaskSnapshot()
        assert snap.task_code is None
        assert snap.task_name is None
        assert snap.department_name is None
        assert snap.operator is None
        assert snap.run_operator is None
        assert snap.equipment_name is None

    def test_explicit_values(self) -> None:
        snap = TaskSnapshot(
            task_code="TC",
            task_name="TN",
            department_name="DN",
            operator="OP",
            run_operator="RO",
            equipment_name="EN",
        )
        assert snap.task_code == "TC"
        assert snap.equipment_name == "EN"
