"""Worker 任务单元测试。

覆盖：
- ``apps/worker/tasks/derivation.py``：Celery 推导任务；
- ``apps/worker/tasks/ops_cleanup.py``：审计日志保留清理任务。

测试策略：
- mock 数据库连接、session_scope、DerivationService 等外部依赖；
- 验证任务的状态流转（RUNNING → COMPLETED / FAILED）和返回结构。

注意：derivation.py / ops_cleanup.py 在函数内部 import 依赖，
因此 patch 目标为依赖的源模块而非调用模块。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

# ---------------------------------------------------------------------------
# derivation.py — _process_derivation_async
# ---------------------------------------------------------------------------


def _make_mock_ref() -> MagicMock:
    """构建 mock DerivationRun 引用。"""
    mock_output = MagicMock()
    mock_output.variable_code = "var_1"
    mock_output.value = 42
    mock_output.unit = "kg"
    mock_output.confidence = 0.95
    mock_output.exclusion_reasons = []
    mock_ref = MagicMock()
    mock_ref.id = uuid4()
    mock_ref.status = "completed"
    mock_ref.output_digest = "digest123"
    mock_ref.outputs = [mock_output]
    return mock_ref


def _derivation_patches(
    mock_ref: MagicMock | None = None,
    create_run_side_effect: object | None = None,
    init_side_effect: object | None = None,
) -> list:
    """构建 _process_derivation_async 所需的 patch 列表。"""
    ref = mock_ref or _make_mock_ref()
    mock_session = AsyncMockSession()
    mock_service = MagicMock()
    if create_run_side_effect is not None:
        mock_service.create_run = AsyncMock(side_effect=create_run_side_effect)
    else:
        mock_service.create_run = AsyncMock(return_value=ref)

    if init_side_effect is not None:
        derivation_patch = patch(
            "packages.provenance.derivations.DerivationService",
            side_effect=init_side_effect,
        )
    else:
        derivation_patch = patch(
            "packages.provenance.derivations.DerivationService",
            return_value=mock_service,
        )

    return [
        patch(
            "packages.common.database.build_session_factory",
            return_value=MagicMock(),
        ),
        patch(
            "packages.common.database.get_database_url",
            return_value="postgresql+psycopg://irip:pw@localhost:55432/irip",
        ),
        patch(
            "packages.common.database.session_scope",
            return_value=AsyncCtxMgr(mock_session),
        ),
        patch(
            "packages.common.tenant_guc.set_dept_guc",
            return_value=_async_noop(),
        ),
        patch(
            "packages.common.tenant_guc.set_user_guc",
            return_value=_async_noop(),
        ),
        derivation_patch,
    ]


@pytest.mark.asyncio
async def test_process_derivation_async_success() -> None:
    """推导作业成功时更新状态为 COMPLETED 并返回摘要。"""
    job_id = str(uuid4())
    dept_id = uuid4()
    actor_id = uuid4()
    evidence_set_version_id = uuid4()
    recipe_version_id = uuid4()
    payload: dict[str, Any] = {
        "department_id": str(dept_id),
        "actor_id": str(actor_id),
        "evidence_set_version_id": str(evidence_set_version_id),
        "recipe_version_id": str(recipe_version_id),
    }

    mock_ref = _make_mock_ref()
    captured_kwargs: dict[str, Any] = {}

    def _fake_init(session_factory: Any, department_id: Any, actor_id: Any) -> MagicMock:
        captured_kwargs["department_id"] = department_id
        captured_kwargs["actor_id"] = actor_id
        svc = MagicMock()
        svc.create_run = AsyncMock(return_value=mock_ref)
        return svc

    patches = _derivation_patches(init_side_effect=_fake_init)
    for p in patches:
        p.start()
    try:
        from apps.worker.tasks.derivation import _process_derivation_async

        result = await _process_derivation_async(job_id, payload)
    finally:
        for p in patches:
            p.stop()

    assert result["run_id"] == str(mock_ref.id)
    assert result["status"] == "completed"
    assert result["output_digest"] == "digest123"
    assert len(result["outputs"]) == 1
    assert result["outputs"][0]["variable_code"] == "var_1"
    assert result["outputs"][0]["value"] == "42"
    assert result["outputs"][0]["unit"] == "kg"
    assert result["outputs"][0]["confidence"] == 0.95
    assert result["outputs"][0]["exclusion_reasons"] == []
    assert captured_kwargs["department_id"] == dept_id
    assert captured_kwargs["actor_id"] == actor_id


@pytest.mark.asyncio
async def test_process_derivation_async_handles_failure() -> None:
    """推导作业失败时更新状态为 FAILED 并重新抛出异常。"""
    job_id = str(uuid4())
    dept_id = uuid4()
    actor_id = uuid4()
    evidence_set_version_id = uuid4()
    recipe_version_id = uuid4()
    payload: dict[str, Any] = {
        "department_id": str(dept_id),
        "actor_id": str(actor_id),
        "evidence_set_version_id": str(evidence_set_version_id),
        "recipe_version_id": str(recipe_version_id),
    }

    patches = _derivation_patches(create_run_side_effect=RuntimeError("derivation failed"))
    sys_guc_patch = patch("apps.worker.tasks.get_system_guc", return_value=(uuid4(), uuid4()))
    patches.append(sys_guc_patch)

    for p in patches:
        p.start()
    try:
        from apps.worker.tasks.derivation import _process_derivation_async

        with pytest.raises(RuntimeError, match="derivation failed"):
            await _process_derivation_async(job_id, payload)
    finally:
        for p in patches:
            p.stop()


@pytest.mark.asyncio
async def test_process_derivation_async_without_actor_id() -> None:
    """payload 不含 actor_id 时，actor_id 为 None。"""
    job_id = str(uuid4())
    dept_id = uuid4()
    evidence_set_version_id = uuid4()
    recipe_version_id = uuid4()
    payload: dict[str, Any] = {
        "department_id": str(dept_id),
        "evidence_set_version_id": str(evidence_set_version_id),
        "recipe_version_id": str(recipe_version_id),
    }

    mock_ref = MagicMock()
    mock_ref.id = uuid4()
    mock_ref.status = "completed"
    mock_ref.output_digest = "digest"
    mock_ref.outputs = []

    captured_kwargs: dict[str, Any] = {}

    def _fake_init(session_factory: Any, department_id: Any, actor_id: Any) -> MagicMock:
        captured_kwargs["department_id"] = department_id
        captured_kwargs["actor_id"] = actor_id
        svc = MagicMock()
        svc.create_run = AsyncMock(return_value=mock_ref)
        return svc

    patches = _derivation_patches(init_side_effect=_fake_init)
    for p in patches:
        p.start()
    try:
        from apps.worker.tasks.derivation import _process_derivation_async

        result = await _process_derivation_async(job_id, payload)
    finally:
        for p in patches:
            p.stop()

    assert captured_kwargs["actor_id"] is None
    assert captured_kwargs["department_id"] == dept_id
    assert result["status"] == "completed"
    assert result["outputs"] == []


@pytest.mark.asyncio
async def test_process_derivation_async_url_conversion() -> None:
    """验证 psycopg 同步 URL 被转为 psycopg_async 异步 URL。"""
    job_id = str(uuid4())
    dept_id = uuid4()
    evidence_set_version_id = uuid4()
    recipe_version_id = uuid4()
    payload: dict[str, Any] = {
        "department_id": str(dept_id),
        "evidence_set_version_id": str(evidence_set_version_id),
        "recipe_version_id": str(recipe_version_id),
    }

    mock_ref = MagicMock()
    mock_ref.id = uuid4()
    mock_ref.status = "completed"
    mock_ref.output_digest = "d"
    mock_ref.outputs = []

    mock_session = AsyncMockSession()
    captured_urls: list[str] = []

    def _fake_build(url: str) -> MagicMock:
        captured_urls.append(url)
        return MagicMock()

    patches = [
        patch(
            "packages.common.database.build_session_factory",
            side_effect=_fake_build,
        ),
        patch(
            "packages.common.database.get_database_url",
            return_value="postgresql+psycopg://irip:pw@localhost:55432/irip",
        ),
        patch(
            "packages.common.database.session_scope",
            return_value=AsyncCtxMgr(mock_session),
        ),
        patch(
            "packages.common.tenant_guc.set_dept_guc",
            return_value=_async_noop(),
        ),
        patch(
            "packages.common.tenant_guc.set_user_guc",
            return_value=_async_noop(),
        ),
        patch(
            "packages.provenance.derivations.DerivationService",
            return_value=MagicMock(create_run=AsyncMock(return_value=mock_ref)),
        ),
    ]
    for p in patches:
        p.start()
    try:
        from apps.worker.tasks.derivation import _process_derivation_async

        await _process_derivation_async(job_id, payload)
    finally:
        for p in patches:
            p.stop()

    assert len(captured_urls) == 1
    assert captured_urls[0].startswith("postgresql+psycopg_async://")


# ---------------------------------------------------------------------------
# derivation.py — process_derivation_job (Celery task wrapper)
# ---------------------------------------------------------------------------


def test_process_derivation_job_calls_async_and_returns_result() -> None:
    """Celery 任务调用 _process_derivation_async 并返回结果。"""
    expected_result: dict[str, Any] = {
        "run_id": "abc",
        "status": "completed",
        "output_digest": "d",
        "outputs": [],
    }

    async def _fake_async(job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        assert job_id == "job-1"
        assert payload == {"k": "v"}
        return expected_result

    with patch(
        "apps.worker.tasks.derivation._process_derivation_async",
        _fake_async,
    ):
        from apps.worker.tasks.derivation import process_derivation_job

        result = process_derivation_job.run("job-1", {"k": "v"})

    assert result == expected_result


def test_process_derivation_job_retries_on_timeout() -> None:
    """TimeoutError 触发 self.retry。"""
    from apps.worker.tasks.derivation import process_derivation_job

    task_obj = process_derivation_job._get_current_object()

    async def _fail_async(job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise TimeoutError("timed out")

    with (
        patch("apps.worker.tasks.derivation._process_derivation_async", _fail_async),
        patch.object(task_obj, "retry", side_effect=RuntimeError("retrying")) as mock_retry,
    ):
        with pytest.raises(RuntimeError, match="retrying"):
            process_derivation_job.run("job-1", {})

    mock_retry.assert_called_once()


def test_process_derivation_job_retries_on_connection_error() -> None:
    """ConnectionError 触发 self.retry。"""
    from apps.worker.tasks.derivation import process_derivation_job

    task_obj = process_derivation_job._get_current_object()

    async def _fail_async(job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise ConnectionError("conn lost")

    with (
        patch("apps.worker.tasks.derivation._process_derivation_async", _fail_async),
        patch.object(task_obj, "retry", side_effect=RuntimeError("retrying")) as mock_retry,
    ):
        with pytest.raises(RuntimeError, match="retrying"):
            process_derivation_job.run("job-1", {})

    mock_retry.assert_called_once()


def test_process_derivation_job_retries_on_oserror() -> None:
    """OSError 触发 self.retry。"""
    from apps.worker.tasks.derivation import process_derivation_job

    task_obj = process_derivation_job._get_current_object()

    async def _fail_async(job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise OSError("os error")

    with (
        patch("apps.worker.tasks.derivation._process_derivation_async", _fail_async),
        patch.object(task_obj, "retry", side_effect=RuntimeError("retrying")) as mock_retry,
    ):
        with pytest.raises(RuntimeError, match="retrying"):
            process_derivation_job.run("job-1", {})

    mock_retry.assert_called_once()


def test_process_derivation_job_raises_non_retryable_error() -> None:
    """非可重试异常直接 raise，不调用 self.retry。"""
    from apps.worker.tasks.derivation import process_derivation_job

    task_obj = process_derivation_job._get_current_object()

    async def _fail_async(job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise ValueError("bad input")

    with (
        patch("apps.worker.tasks.derivation._process_derivation_async", _fail_async),
        patch.object(task_obj, "retry", side_effect=RuntimeError("should not retry")) as mock_retry,
    ):
        with pytest.raises(ValueError, match="bad input"):
            process_derivation_job.run("job-1", {})

    mock_retry.assert_not_called()


# ---------------------------------------------------------------------------
# ops_cleanup.py — audit_retention_cleanup
# ---------------------------------------------------------------------------


def _ops_cleanup_patches(rowcount: int = 42) -> tuple[list, AsyncMockSession]:
    """构建 audit_retention_cleanup 所需的 patch 列表。

    返回 (patches, mock_session)。patches[-1] 是 SystemClock 的 patch，
    调用方 start 后可通过 patches[-1].mock 设置时钟行为。
    """
    mock_session = AsyncMockSession()
    mock_result = MagicMock()
    mock_result.rowcount = rowcount
    mock_session.execute_result = mock_result

    patches = [
        patch(
            "apps.worker.tasks.ops_cleanup._get_superuser_factory",
            return_value=MagicMock(),
        ),
        patch(
            "apps.worker.tasks.ops_cleanup.session_scope",
            return_value=AsyncCtxMgr(mock_session),
        ),
        patch(
            "packages.common.tenant_guc.set_dept_guc",
            return_value=_async_noop(),
        ),
        patch(
            "packages.common.tenant_guc.set_user_guc",
            return_value=_async_noop(),
        ),
        patch("apps.worker.tasks.get_system_guc", return_value=(uuid4(), uuid4())),
        patch("apps.worker.tasks.ops_cleanup.SystemClock"),
    ]
    return patches, mock_session


def test_audit_retention_cleanup_success() -> None:
    """审计清理成功时返回包含 deleted_count 的结果字典。"""
    from apps.worker.tasks.ops_cleanup import audit_retention_cleanup

    patches, _ = _ops_cleanup_patches(rowcount=42)
    for p in patches:
        p.start()
    try:
        import apps.worker.tasks.ops_cleanup as ops_mod

        mock_clock = MagicMock()
        mock_clock.now.return_value = datetime.now(UTC)
        ops_mod.SystemClock.return_value = mock_clock

        with patch.dict("os.environ", {"IRIP_AUDIT_RETENTION_DAYS": "30"}):
            result = audit_retention_cleanup()
    finally:
        for p in patches:
            p.stop()

    assert result["status"] == "ok"
    assert result["deleted_count"] == 42
    assert result["retention_days"] == 30
    assert "cutoff" in result
    assert "executed_at" in result


def test_audit_retention_cleanup_default_retention() -> None:
    """未配置 IRIP_AUDIT_RETENTION_DAYS 时使用默认值 90。"""
    from apps.worker.tasks.ops_cleanup import audit_retention_cleanup

    patches, _ = _ops_cleanup_patches(rowcount=0)
    for p in patches:
        p.start()
    try:
        import apps.worker.tasks.ops_cleanup as ops_mod

        mock_clock = MagicMock()
        mock_clock.now.return_value = datetime.now(UTC)
        ops_mod.SystemClock.return_value = mock_clock

        with patch.dict("os.environ", {}, clear=True):
            result = audit_retention_cleanup()
    finally:
        for p in patches:
            p.stop()

    assert result["retention_days"] == 90
    assert result["deleted_count"] == 0


def test_audit_retention_cleanup_propagates_exception() -> None:
    """清理过程中抛出异常时重新 raise。"""
    from apps.worker.tasks.ops_cleanup import audit_retention_cleanup

    clock_patch = patch("apps.worker.tasks.ops_cleanup.SystemClock")
    clock_patch.start()
    import apps.worker.tasks.ops_cleanup as ops_mod

    mock_clock = MagicMock()
    mock_clock.now.return_value = datetime.now(UTC)
    ops_mod.SystemClock.return_value = mock_clock

    factory_patch = patch(
        "apps.worker.tasks.ops_cleanup._get_superuser_factory",
        return_value=MagicMock(),
    )
    factory_patch.start()

    session_scope_patch = patch(
        "apps.worker.tasks.ops_cleanup.session_scope",
        side_effect=RuntimeError("db connection lost"),
    )
    session_scope_patch.start()

    try:
        with patch.dict("os.environ", {"IRIP_AUDIT_RETENTION_DAYS": "90"}):
            with pytest.raises(RuntimeError, match="db connection lost"):
                audit_retention_cleanup()
    finally:
        clock_patch.stop()
        factory_patch.stop()
        session_scope_patch.stop()


def test_audit_retention_cleanup_cutoff_calculation() -> None:
    """executed_at 来自 SystemClock.now()，cutoff 来自 datetime.now(UTC) - retention_days。"""
    from apps.worker.tasks.ops_cleanup import audit_retention_cleanup

    fixed_now = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)

    class FakeDateTime(datetime):
        """datetime 子类，覆盖 now() 返回固定时间。"""

        @classmethod
        def now(cls, tz: object | None = None) -> datetime:
            return fixed_now

    patches, _ = _ops_cleanup_patches(rowcount=10)
    patches.append(patch("apps.worker.tasks.ops_cleanup.datetime", FakeDateTime))
    for p in patches:
        p.start()
    try:
        import apps.worker.tasks.ops_cleanup as ops_mod

        mock_clock = MagicMock()
        mock_clock.now.return_value = fixed_now
        ops_mod.SystemClock.return_value = mock_clock

        with patch.dict("os.environ", {"IRIP_AUDIT_RETENTION_DAYS": "30"}):
            result = audit_retention_cleanup()
    finally:
        for p in patches:
            p.stop()

    expected_cutoff = fixed_now - timedelta(days=30)
    assert result["cutoff"] == expected_cutoff.isoformat()
    assert result["executed_at"] == fixed_now.isoformat()


# ---------------------------------------------------------------------------
# ops_cleanup.py — _get_superuser_factory
# ---------------------------------------------------------------------------


def test_get_superuser_factory_caches_singleton() -> None:
    """_get_superuser_factory 缓存 factory 单例，多次调用返回同一实例。"""
    import apps.worker.tasks.ops_cleanup as ops_cleanup

    original = ops_cleanup._superuser_factory
    ops_cleanup._superuser_factory = None

    try:
        with patch.dict(
            "os.environ", {"IRIP_ALEMBIC_DATABASE_URL": "postgresql+psycopg://u:p@h:5432/db"}
        ):
            factory1 = ops_cleanup._get_superuser_factory()
            factory2 = ops_cleanup._get_superuser_factory()
            assert factory1 is factory2
    finally:
        ops_cleanup._superuser_factory = original


def test_get_superuser_factory_raises_when_no_url() -> None:
    """无 IRIP_ALEMBIC_DATABASE_URL 且无 get_database_url 回退时 raise RuntimeError。"""
    import apps.worker.tasks.ops_cleanup as ops_cleanup

    original = ops_cleanup._superuser_factory
    ops_cleanup._superuser_factory = None

    try:
        with (
            patch.dict("os.environ", {}, clear=True),
            patch(
                "packages.common.database.get_database_url",
                return_value="",
            ),
        ):
            with pytest.raises(RuntimeError, match="无法获取超级用户连接"):
                ops_cleanup._get_superuser_factory()
    finally:
        ops_cleanup._superuser_factory = original


def test_get_superuser_factory_url_conversion() -> None:
    """psycopg 同步 URL 被转为 psycopg_async 异步 URL 构建引擎。"""
    import apps.worker.tasks.ops_cleanup as ops_cleanup

    original = ops_cleanup._superuser_factory
    ops_cleanup._superuser_factory = None

    try:
        with (
            patch.dict(
                "os.environ",
                {"IRIP_ALEMBIC_DATABASE_URL": "postgresql+psycopg://u:p@h:5432/db"},
            ),
            patch(
                "sqlalchemy.ext.asyncio.create_async_engine",
            ) as mock_create_engine,
            patch("sqlalchemy.ext.asyncio.async_sessionmaker") as mock_maker,
        ):
            ops_cleanup._get_superuser_factory()
            mock_create_engine.assert_called_once()
            call_url = mock_create_engine.call_args[0][0]
            assert call_url.startswith("postgresql+psycopg_async://")
            mock_maker.assert_called_once()
    finally:
        ops_cleanup._superuser_factory = original


# ---------------------------------------------------------------------------
# Helper mocks
# ---------------------------------------------------------------------------


class AsyncMockSession:
    """模拟异步 SQLAlchemy Session。"""

    def __init__(self, execute_result: object | None = None) -> None:
        self.execute_result = execute_result

    async def execute(self, statement: object, params: object | None = None) -> object:
        return self.execute_result


class AsyncCtxMgr:
    """异步上下文管理器，同时可被调用（每次调用返回自身）。

    session_scope(factory) 返回此对象，async with 进入时返回 mock session。
    """

    def __init__(self, session: AsyncMockSession) -> None:
        self._session = session

    def __call__(self, *args: object, **kwargs: object) -> AsyncCtxMgr:
        """支持 session_scope(factory) 调用模式，返回自身。"""
        return self

    async def __aenter__(self) -> AsyncMockSession:
        return self._session

    async def __aexit__(self, *args: object) -> None:
        return None


def _async_noop() -> Any:
    """返回一个异步空函数，用于 mock set_dept_guc / set_user_guc。"""

    async def _noop(*args: object, **kwargs: object) -> None:
        return None

    return _noop
