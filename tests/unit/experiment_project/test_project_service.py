"""ExperimentProjectService 单元测试：
create / list / get / update / set_status / check_not_archived。

测试策略：
- 使用 AsyncMock mock repository 方法，隔离数据库依赖
- 验证业务逻辑：编码唯一性、乐观锁、归档约束、游标编解码
- 验证 AppError 的 code 和 message

对应架构设计 §7.6 乐观锁约定 + §7.3 错误码约定。
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from packages.common.errors import AppError
from packages.experiment_project.entities import (
    ExperimentProject,
    ExperimentProjectStatus,
)
from packages.experiment_project.service import (
    ExperimentProjectService,
    _decode_cursor,
    _encode_cursor,
)

# ===========================================================================
# 辅助函数
# ===========================================================================


def _make_project(
    project_id: UUID | None = None,
    department_id: UUID | None = None,
    code: str = "proj_test01",
    display_name: str = "测试项目",
    description: str | None = None,
    status: str = "active",
    owner_user_id: UUID | None = None,
    lock_version: int = 0,
) -> ExperimentProject:
    """构造 ExperimentProject 实体。"""
    return ExperimentProject(
        id=project_id or uuid4(),
        department_id=department_id or uuid4(),
        code=code,
        display_name=display_name,
        description=description,
        status=status,
        visible_departments=[],
        visibility_scope="tree",
        owner_user_id=owner_user_id or uuid4(),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        lock_version=lock_version,
    )


def _make_service() -> ExperimentProjectService:
    """构造 ExperimentProjectService（mock session_factory）。"""
    return ExperimentProjectService(
        session_factory=MagicMock(),
        department_id=uuid4(),
        actor_id=uuid4(),
    )


# ===========================================================================
# 1. create 测试
# ===========================================================================


class TestServiceCreate:
    """创建项目服务测试。"""

    async def test_create_success(self):
        """创建项目成功：编码不重复时正常创建"""
        service = _make_service()

        with (
            patch.object(service, "_scoped_session") as mock_scope,
            patch("packages.experiment_project.service.ExperimentProjectRepository") as mock_repo,
        ):
            mock_session = AsyncMock()
            mock_scope.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_scope.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_repo.select_by_dept_and_code = AsyncMock(return_value=None)

            # insert 返回传入的 project 对象
            def _insert_side_effect(session, project):
                return project

            mock_repo.insert = AsyncMock(side_effect=_insert_side_effect)

            result = await service.create(
                department_id=service.department_id,
                code="proj_test01",
                display_name="测试项目",
                description="描述",
            )

            # 验证 select_by_dept_and_code 被调用
            mock_repo.select_by_dept_and_code.assert_called_once()
            # 验证 insert 被调用
            mock_repo.insert.assert_called_once()
            # 验证返回的是 ExperimentProject
            assert isinstance(result, ExperimentProject)
            assert result.code == "proj_test01"
            assert result.status == ExperimentProjectStatus.ACTIVE.value
            assert result.lock_version == 0

    async def test_create_duplicate_code_raises_conflict(self):
        """创建项目编码重复 → AppError(conflict)"""
        service = _make_service()

        with (
            patch.object(service, "_scoped_session") as mock_scope,
            patch("packages.experiment_project.service.ExperimentProjectRepository") as mock_repo,
        ):
            mock_session = AsyncMock()
            mock_scope.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_scope.return_value.__aexit__ = AsyncMock(return_value=None)

            existing = _make_project(code="proj_dup")
            mock_repo.select_by_dept_and_code = AsyncMock(return_value=existing)

            with pytest.raises(AppError) as exc_info:
                await service.create(
                    department_id=service.department_id,
                    code="proj_dup",
                    display_name="重复项目",
                    description=None,
                )

            assert exc_info.value.code == "conflict"
            assert "编码已存在" in exc_info.value.message


# ===========================================================================
# 2. get 测试
# ===========================================================================


class TestServiceGet:
    """查询项目详情服务测试。"""

    async def test_get_success(self):
        """查询项目成功"""
        dept_id = uuid4()
        project_id = uuid4()
        project = _make_project(project_id=project_id)

        service = ExperimentProjectService(
            session_factory=MagicMock(),
            department_id=dept_id,
            actor_id=uuid4(),
        )

        with (
            patch.object(service, "_scoped_session") as mock_scope,
            patch("packages.experiment_project.service.ExperimentProjectRepository") as mock_repo,
        ):
            mock_session = AsyncMock()
            mock_scope.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_scope.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_repo.select_by_id = AsyncMock(return_value=project)

            result = await service.get(project_id)

            assert result is project
            assert result.id == project_id

    async def test_get_not_found(self):
        """查询不存在 → AppError(not_found)"""
        service = _make_service()
        project_id = uuid4()

        with (
            patch.object(service, "_scoped_session") as mock_scope,
            patch("packages.experiment_project.service.ExperimentProjectRepository") as mock_repo,
        ):
            mock_session = AsyncMock()
            mock_scope.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_scope.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_repo.select_by_id = AsyncMock(return_value=None)

            with pytest.raises(AppError) as exc_info:
                await service.get(project_id)

            assert exc_info.value.code == "not_found"
            assert "项目不存在" in exc_info.value.message


# ===========================================================================
# 3. update 测试（乐观锁）
# ===========================================================================


class TestServiceUpdate:
    """编辑项目服务测试（乐观锁）。"""

    async def test_update_success(self):
        """编辑项目成功：lock_version 匹配"""
        service = _make_service()
        project_id = uuid4()
        updated = _make_project(project_id=project_id, lock_version=1, display_name="新名称")

        with (
            patch.object(service, "_scoped_session") as mock_scope,
            patch("packages.experiment_project.service.ExperimentProjectRepository") as mock_repo,
        ):
            mock_session = AsyncMock()
            mock_scope.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_scope.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_repo.update = AsyncMock(return_value=updated)

            result = await service.update(
                project_id=project_id,
                display_name="新名称",
                description="新描述",
                lock_version=0,
            )

            assert result is updated
            assert result.lock_version == 1
            # 验证 update 不含 code 列
            call_kwargs = mock_repo.update.call_args.kwargs
            assert "code" not in call_kwargs or call_kwargs.get("code") is None

    async def test_update_lock_version_conflict(self):
        """编辑项目乐观锁冲突 → AppError(conflict)"""
        service = _make_service()
        project_id = uuid4()
        existing = _make_project(project_id=project_id, lock_version=1)

        with (
            patch.object(service, "_scoped_session") as mock_scope,
            patch("packages.experiment_project.service.ExperimentProjectRepository") as mock_repo,
        ):
            mock_session = AsyncMock()
            mock_scope.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_scope.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_repo.update = AsyncMock(return_value=None)  # 0 rows
            mock_repo.select_by_id = AsyncMock(return_value=existing)

            with pytest.raises(AppError) as exc_info:
                await service.update(
                    project_id=project_id,
                    display_name="新名称",
                    description=None,
                    lock_version=0,  # 旧版本号
                )

            assert exc_info.value.code == "conflict"
            assert "修改" in exc_info.value.message

    async def test_update_not_found(self):
        """编辑不存在的项目 → AppError(not_found)"""
        service = _make_service()
        project_id = uuid4()

        with (
            patch.object(service, "_scoped_session") as mock_scope,
            patch("packages.experiment_project.service.ExperimentProjectRepository") as mock_repo,
        ):
            mock_session = AsyncMock()
            mock_scope.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_scope.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_repo.update = AsyncMock(return_value=None)  # 0 rows
            mock_repo.select_by_id = AsyncMock(return_value=None)  # 不存在

            with pytest.raises(AppError) as exc_info:
                await service.update(
                    project_id=project_id,
                    display_name="新名称",
                    description=None,
                    lock_version=0,
                )

            assert exc_info.value.code == "not_found"


# ===========================================================================
# 4. set_status 测试（归档/恢复 + 乐观锁）
# ===========================================================================


class TestServiceSetStatus:
    """归档/恢复项目服务测试（乐观锁）。"""

    async def test_set_status_archived_success(self):
        """归档项目成功"""
        service = _make_service()
        project_id = uuid4()
        archived = _make_project(project_id=project_id, status="archived", lock_version=1)

        with (
            patch.object(service, "_scoped_session") as mock_scope,
            patch("packages.experiment_project.service.ExperimentProjectRepository") as mock_repo,
        ):
            mock_session = AsyncMock()
            mock_scope.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_scope.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_repo.update_status = AsyncMock(return_value=archived)

            result = await service.set_status(
                project_id=project_id,
                status="archived",
                lock_version=0,
            )

            assert result.status == "archived"
            assert result.lock_version == 1

    async def test_set_status_restore_success(self):
        """恢复项目成功"""
        service = _make_service()
        project_id = uuid4()
        restored = _make_project(project_id=project_id, status="active", lock_version=2)

        with (
            patch.object(service, "_scoped_session") as mock_scope,
            patch("packages.experiment_project.service.ExperimentProjectRepository") as mock_repo,
        ):
            mock_session = AsyncMock()
            mock_scope.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_scope.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_repo.update_status = AsyncMock(return_value=restored)

            result = await service.set_status(
                project_id=project_id,
                status="active",
                lock_version=1,
            )

            assert result.status == "active"

    async def test_set_status_lock_version_conflict(self):
        """归档乐观锁冲突 → AppError(conflict)"""
        service = _make_service()
        project_id = uuid4()
        existing = _make_project(project_id=project_id, lock_version=1)

        with (
            patch.object(service, "_scoped_session") as mock_scope,
            patch("packages.experiment_project.service.ExperimentProjectRepository") as mock_repo,
        ):
            mock_session = AsyncMock()
            mock_scope.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_scope.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_repo.update_status = AsyncMock(return_value=None)
            mock_repo.select_by_id = AsyncMock(return_value=existing)

            with pytest.raises(AppError) as exc_info:
                await service.set_status(
                    project_id=project_id,
                    status="archived",
                    lock_version=0,
                )

            assert exc_info.value.code == "conflict"

    async def test_set_status_not_found(self):
        """归档不存在的项目 → AppError(not_found)"""
        service = _make_service()
        project_id = uuid4()

        with (
            patch.object(service, "_scoped_session") as mock_scope,
            patch("packages.experiment_project.service.ExperimentProjectRepository") as mock_repo,
        ):
            mock_session = AsyncMock()
            mock_scope.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_scope.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_repo.update_status = AsyncMock(return_value=None)
            mock_repo.select_by_id = AsyncMock(return_value=None)

            with pytest.raises(AppError) as exc_info:
                await service.set_status(
                    project_id=project_id,
                    status="archived",
                    lock_version=0,
                )

            assert exc_info.value.code == "not_found"


# ===========================================================================
# 5. check_not_archived 测试（归档约束）
# ===========================================================================


class TestServiceCheckNotArchived:
    """归档约束测试：归档项目下不可创建新任务。"""

    async def test_check_not_archived_active_ok(self):
        """活跃项目通过检查"""
        service = _make_service()
        project_id = uuid4()
        project = _make_project(project_id=project_id, status="active")

        with patch.object(ExperimentProjectService, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = project

            # 不应抛异常
            await service.check_not_archived(project_id)

    async def test_check_not_archived_raises_conflict(self):
        """归档项目 → AppError(conflict)"""
        service = _make_service()
        project_id = uuid4()
        project = _make_project(project_id=project_id, status="archived")

        with patch.object(ExperimentProjectService, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = project

            with pytest.raises(AppError) as exc_info:
                await service.check_not_archived(project_id)

            assert exc_info.value.code == "conflict"
            assert "归档" in exc_info.value.message

    async def test_check_not_archived_not_found(self):
        """项目不存在 → AppError(not_found)"""
        service = _make_service()
        project_id = uuid4()

        with patch.object(ExperimentProjectService, "get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = AppError(
                code="not_found", message="项目不存在", retryable=False, fields={}
            )

            with pytest.raises(AppError) as exc_info:
                await service.check_not_archived(project_id)

            assert exc_info.value.code == "not_found"


# ===========================================================================
# 6. 游标编解码测试
# ===========================================================================


class TestCursorCodec:
    """分页游标编解码测试。"""

    def test_encode_decode_roundtrip(self):
        """编码→解码往返一致"""
        created_at = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)
        project_id = uuid4()

        cursor = _encode_cursor(created_at, project_id)
        decoded_ct, decoded_id = _decode_cursor(cursor)

        assert decoded_ct == created_at
        assert decoded_id == project_id

    def test_decode_invalid_base64(self):
        """非法 base64 → AppError(invalid_cursor)"""
        with pytest.raises(AppError) as exc_info:
            _decode_cursor("!!!not-base64!!!")

        assert exc_info.value.code == "invalid_cursor"

    def test_decode_invalid_json(self):
        """合法 base64 但非 JSON → AppError(invalid_cursor)"""
        import base64

        raw = base64.urlsafe_b64encode(b"not json").decode("ascii")
        with pytest.raises(AppError) as exc_info:
            _decode_cursor(raw)

        assert exc_info.value.code == "invalid_cursor"

    def test_decode_missing_fields(self):
        """JSON 缺少必要字段 → AppError(invalid_cursor)"""
        import base64
        import json

        payload = json.dumps({"v": {}}).encode("utf-8")
        cursor = base64.urlsafe_b64encode(payload).decode("ascii")
        with pytest.raises(AppError) as exc_info:
            _decode_cursor(cursor)

        assert exc_info.value.code == "invalid_cursor"

    def test_decode_missing_ct_field(self):
        """缺少 ct 字段 → AppError(invalid_cursor)"""
        import base64
        import json

        payload = json.dumps({"v": {}, "id": str(uuid4())}).encode("utf-8")
        cursor = base64.urlsafe_b64encode(payload).decode("ascii")
        with pytest.raises(AppError) as exc_info:
            _decode_cursor(cursor)

        assert exc_info.value.code == "invalid_cursor"


# ===========================================================================
# 7. get_with_stats 测试
# ===========================================================================


class TestServiceGetWithStats:
    """查询项目详情 + 任务统计测试。"""

    async def test_get_with_stats_success(self):
        """查询项目 + 统计成功"""
        service = _make_service()
        project_id = uuid4()
        project = _make_project(project_id=project_id)

        with (
            patch.object(service, "_scoped_session") as mock_scope,
            patch("packages.experiment_project.service.ExperimentProjectRepository") as mock_repo,
        ):
            mock_session = AsyncMock()
            mock_scope.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_scope.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_repo.select_by_id = AsyncMock(return_value=project)
            mock_repo.count_flows_by_project = AsyncMock(return_value=5)
            mock_repo.count_facts_by_project = AsyncMock(return_value=0)

            result_project, task_count, fact_count = await service.get_with_stats(project_id)

            assert result_project is project
            assert task_count == 5
            assert fact_count == 0

    async def test_get_with_stats_not_found(self):
        """项目不存在 → AppError(not_found)"""
        service = _make_service()
        project_id = uuid4()

        with (
            patch.object(service, "_scoped_session") as mock_scope,
            patch("packages.experiment_project.service.ExperimentProjectRepository") as mock_repo,
        ):
            mock_session = AsyncMock()
            mock_scope.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_scope.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_repo.select_by_id = AsyncMock(return_value=None)

            with pytest.raises(AppError) as exc_info:
                await service.get_with_stats(project_id)

            assert exc_info.value.code == "not_found"
