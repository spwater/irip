"""WorkspaceService 集成测试。

覆盖 ``packages.research.service`` 的核心业务方法：
- create_workspace / list_workspaces / update_workspace_name / get_workspace
- archive_workspace / restore_workspace / delete_workspace
- add_evidence / remove_evidence / list_evidence / search_facts

DB 依赖：通过 ``async_session_factory`` fixture 连接测试库。
"""

from __future__ import annotations

from uuid import UUID

import pytest
import sqlalchemy as sa

from packages.common.errors import AppError
from packages.common.ids import new_id
from packages.research.dtos import CreateWorkspaceCommand, FactSummary
from packages.research.entities import ResearchWorkspace
from packages.research.service import WorkspaceService

# ============================================================
# Helpers
# ============================================================


class FakeFactProvider:
    """假 CoreFactProvider：模拟 search_facts / get_fact_summary / get_fact_fields。"""

    def __init__(self, summary: FactSummary | None = None, fields: list[str] | None = None) -> None:
        self._summary = summary or FactSummary(
            fact_id=new_id(),
            fact_type="core:fact",
            subject_id="测试数据集",
            status="confirmed",
            department_name="研发部",
        )
        self._fields = fields or ["温度", "压力"]
        self.search_calls: list[tuple] = []

    async def search_facts(self, query, filters=None, cursor=None, page_size=20):
        self.search_calls.append((query, filters, cursor, page_size))
        return ([self._summary], None)

    async def get_fact_summary(self, fact_id):
        return self._summary

    async def get_fact_fields(self, fact_id):
        return self._fields


async def _cleanup(factory, workspace_id: UUID) -> None:
    """清理审计 + 工作空间。"""
    async with factory() as session:
        async with session.begin():
            await session.execute(sa.text("ALTER TABLE audit_event DISABLE TRIGGER ALL"))
            await session.execute(
                sa.text(
                    "DELETE FROM audit_event WHERE department_id = "
                    "(SELECT department_id FROM research_workspace WHERE id = :wid)"
                ),
                {"wid": str(workspace_id)},
            )
            await session.execute(sa.text("ALTER TABLE audit_event ENABLE TRIGGER ALL"))
            await session.execute(
                sa.text("DELETE FROM research_workspace WHERE id = :wid"),
                {"wid": str(workspace_id)},
            )


def _make_service(factory, user, fact_provider=None, catalog=None) -> WorkspaceService:
    """构造 WorkspaceService。"""
    return WorkspaceService(
        session_factory=factory,
        department_id=user.department_id,
        actor_id=user.user_id,
        fact_provider=fact_provider or FakeFactProvider(),
        research_catalog=catalog,
    )


# ============================================================
# create_workspace
# ============================================================


@pytest.mark.integration
async def test_create_workspace_success(async_session_factory, test_user) -> None:
    """创建工作空间：status=draft，turn_count=0。"""
    svc = _make_service(async_session_factory, test_user)
    ref = await svc.create_workspace(CreateWorkspaceCommand(name="测试空间"))
    assert ref.name == "测试空间"
    assert ref.status == "draft"
    assert ref.turn_count == 0
    assert ref.active_run_status is None
    await _cleanup(async_session_factory, ref.workspace_id)


@pytest.mark.integration
async def test_create_workspace_requires_actor(async_session_factory, test_user) -> None:
    """actor_id 为 None 时创建抛出 forbidden。"""
    svc = WorkspaceService(
        session_factory=async_session_factory,
        department_id=test_user.department_id,
        actor_id=None,
        fact_provider=FakeFactProvider(),
    )
    with pytest.raises(AppError) as exc_info:
        await svc.create_workspace(CreateWorkspaceCommand(name="x"))
    assert exc_info.value.code == "forbidden"


# ============================================================
# list_workspaces
# ============================================================


@pytest.mark.integration
async def test_list_workspaces_returns_owner_only(async_session_factory, test_user) -> None:
    """list_workspaces 仅返回当前用户的工作空间。"""
    svc = _make_service(async_session_factory, test_user)
    ref1 = await svc.create_workspace(CreateWorkspaceCommand(name="空间A"))
    ref2 = await svc.create_workspace(CreateWorkspaceCommand(name="空间B"))
    try:
        refs, cursor = await svc.list_workspaces()
        names = {r.name for r in refs}
        assert {"空间A", "空间B"}.issubset(names)
        assert all(r.status == "draft" for r in refs)
    finally:
        await _cleanup(async_session_factory, ref1.workspace_id)
        await _cleanup(async_session_factory, ref2.workspace_id)


@pytest.mark.integration
async def test_list_workspaces_status_filter(async_session_factory, test_user) -> None:
    """status 过滤仅返回匹配状态的工作空间。"""
    svc = _make_service(async_session_factory, test_user)
    ref = await svc.create_workspace(CreateWorkspaceCommand(name="归档空间"))
    await svc.archive_workspace(ref.workspace_id)
    try:
        refs, _ = await svc.list_workspaces(status="archived")
        assert any(r.workspace_id == ref.workspace_id for r in refs)
        refs_draft, _ = await svc.list_workspaces(status="draft")
        assert all(r.workspace_id != ref.workspace_id for r in refs_draft)
    finally:
        await _cleanup(async_session_factory, ref.workspace_id)


# ============================================================
# update_workspace_name / get_workspace
# ============================================================


@pytest.mark.integration
async def test_update_workspace_name(async_session_factory, test_user) -> None:
    """更新工作空间名称。"""
    svc = _make_service(async_session_factory, test_user)
    ref = await svc.create_workspace(CreateWorkspaceCommand(name="旧名"))
    try:
        updated = await svc.update_workspace_name(ref.workspace_id, "新名")
        assert updated.name == "新名"
        async with async_session_factory() as session:
            ws = await session.scalar(
                sa.select(ResearchWorkspace).where(ResearchWorkspace.id == ref.workspace_id)
            )
            assert ws.name == "新名"
    finally:
        await _cleanup(async_session_factory, ref.workspace_id)


@pytest.mark.integration
async def test_update_workspace_name_not_found(async_session_factory, test_user) -> None:
    """工作空间不存在时更新名称抛出 not_found。"""
    svc = _make_service(async_session_factory, test_user)
    with pytest.raises(AppError) as exc_info:
        await svc.update_workspace_name(new_id(), "x")
    assert exc_info.value.code == "not_found"


@pytest.mark.integration
async def test_get_workspace_detail(async_session_factory, test_user) -> None:
    """get_workspace 返回详情含 evidence_count/snapshots/turn_count。"""
    svc = _make_service(async_session_factory, test_user)
    ref = await svc.create_workspace(CreateWorkspaceCommand(name="详情空间"))
    try:
        detail = await svc.get_workspace(ref.workspace_id)
        assert detail.workspace_id == ref.workspace_id
        assert detail.evidence_count == 0
        assert detail.turn_count == 0
        assert detail.active_run_status is None
        assert detail.snapshots == []
    finally:
        await _cleanup(async_session_factory, ref.workspace_id)


@pytest.mark.integration
async def test_get_workspace_not_found(async_session_factory, test_user) -> None:
    """工作空间不存在时 get_workspace 抛出 not_found。"""
    svc = _make_service(async_session_factory, test_user)
    with pytest.raises(AppError) as exc_info:
        await svc.get_workspace(new_id())
    assert exc_info.value.code == "not_found"


# ============================================================
# archive / restore / delete
# ============================================================


@pytest.mark.integration
async def test_archive_and_restore_workspace(async_session_factory, test_user) -> None:
    """归档后可恢复。"""
    svc = _make_service(async_session_factory, test_user)
    ref = await svc.create_workspace(CreateWorkspaceCommand(name="归档测试"))
    try:
        await svc.archive_workspace(ref.workspace_id)
        async with async_session_factory() as session:
            ws = await session.scalar(
                sa.select(ResearchWorkspace).where(ResearchWorkspace.id == ref.workspace_id)
            )
            assert ws.status == "archived"

        await svc.restore_workspace(ref.workspace_id)
        async with async_session_factory() as session:
            ws = await session.scalar(
                sa.select(ResearchWorkspace).where(ResearchWorkspace.id == ref.workspace_id)
            )
            assert ws.status == "draft"
    finally:
        await _cleanup(async_session_factory, ref.workspace_id)


@pytest.mark.integration
async def test_archive_workspace_not_found(async_session_factory, test_user) -> None:
    """归档不存在的工作空间抛出 not_found。"""
    svc = _make_service(async_session_factory, test_user)
    with pytest.raises(AppError) as exc_info:
        await svc.archive_workspace(new_id())
    assert exc_info.value.code == "not_found"


@pytest.mark.integration
async def test_delete_workspace_success(async_session_factory, test_user) -> None:
    """无已发布成果包的工作空间可删除。"""
    svc = _make_service(async_session_factory, test_user)
    ref = await svc.create_workspace(CreateWorkspaceCommand(name="删除测试"))
    ws_id = ref.workspace_id
    await svc.delete_workspace(ws_id)
    async with async_session_factory() as session:
        ws = await session.scalar(sa.select(ResearchWorkspace).where(ResearchWorkspace.id == ws_id))
        assert ws is None
    # 删除工作空间后清理本部门审计行，避免 test_user teardown 被 audit_event FK 阻断
    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(sa.text("ALTER TABLE audit_event DISABLE TRIGGER ALL"))
            await session.execute(
                sa.text("DELETE FROM audit_event WHERE department_id = :did"),
                {"did": str(test_user.department_id)},
            )
            await session.execute(sa.text("ALTER TABLE audit_event ENABLE TRIGGER ALL"))


@pytest.mark.integration
async def test_delete_workspace_not_found(async_session_factory, test_user) -> None:
    """删除不存在的工作空间抛出 not_found。"""
    svc = _make_service(async_session_factory, test_user)
    with pytest.raises(AppError) as exc_info:
        await svc.delete_workspace(new_id())
    assert exc_info.value.code == "not_found"


# ============================================================
# add_evidence / remove_evidence / list_evidence
# ============================================================


@pytest.mark.integration
async def test_add_evidence_core_fact(async_session_factory, test_user) -> None:
    """加入 core:fact 证据引用。"""
    fact_id = new_id()
    svc = _make_service(async_session_factory, test_user, fact_provider=FakeFactProvider())
    ref = await svc.create_workspace(CreateWorkspaceCommand(name="证据空间"))
    try:
        ev = await svc.add_evidence(ref.workspace_id, "core:fact", fact_id)
        assert ev.source_namespace == "core:fact"
        assert ev.source_id == fact_id
        assert ev.status == "active"
    finally:
        await _cleanup(async_session_factory, ref.workspace_id)


@pytest.mark.integration
async def test_add_evidence_unsupported_namespace(async_session_factory, test_user) -> None:
    """不支持的命名空间抛出 validation_failed。"""
    svc = _make_service(async_session_factory, test_user)
    ref = await svc.create_workspace(CreateWorkspaceCommand(name="证据空间"))
    try:
        with pytest.raises(AppError) as exc_info:
            await svc.add_evidence(ref.workspace_id, "unknown:ns", new_id())
        assert exc_info.value.code == "validation_failed"
    finally:
        await _cleanup(async_session_factory, ref.workspace_id)


@pytest.mark.integration
async def test_add_evidence_workspace_not_found(async_session_factory, test_user) -> None:
    """工作空间不存在时 add_evidence 抛出 not_found。"""
    svc = _make_service(async_session_factory, test_user)
    with pytest.raises(AppError) as exc_info:
        await svc.add_evidence(new_id(), "core:fact", new_id())
    assert exc_info.value.code == "not_found"


@pytest.mark.integration
async def test_add_and_remove_evidence(async_session_factory, test_user) -> None:
    """加入证据后软删除（status → removed）。"""
    fact_id = new_id()
    svc = _make_service(async_session_factory, test_user, fact_provider=FakeFactProvider())
    ref = await svc.create_workspace(CreateWorkspaceCommand(name="证据空间"))
    try:
        ev = await svc.add_evidence(ref.workspace_id, "core:fact", fact_id)

        # list 应包含
        refs = await svc.list_evidence(ref.workspace_id)
        assert len(refs) == 1

        # remove 软删除
        await svc.remove_evidence(ref.workspace_id, ev.ref_id)
        refs_after = await svc.list_evidence(ref.workspace_id)
        assert len(refs_after) == 0
    finally:
        await _cleanup(async_session_factory, ref.workspace_id)


@pytest.mark.integration
async def test_remove_evidence_not_found(async_session_factory, test_user) -> None:
    """证据引用不存在时 remove_evidence 抛出 not_found。"""
    svc = _make_service(async_session_factory, test_user)
    ref = await svc.create_workspace(CreateWorkspaceCommand(name="证据空间"))
    try:
        with pytest.raises(AppError) as exc_info:
            await svc.remove_evidence(ref.workspace_id, new_id())
        assert exc_info.value.code == "not_found"
    finally:
        await _cleanup(async_session_factory, ref.workspace_id)


@pytest.mark.integration
async def test_list_evidence_workspace_not_found(async_session_factory, test_user) -> None:
    """工作空间不存在时 list_evidence 抛出 not_found。"""
    svc = _make_service(async_session_factory, test_user)
    with pytest.raises(AppError) as exc_info:
        await svc.list_evidence(new_id())
    assert exc_info.value.code == "not_found"


# ============================================================
# search_facts
# ============================================================


@pytest.mark.integration
async def test_search_facts_delegates_to_provider(async_session_factory, test_user) -> None:
    """search_facts 委托 CoreFactProvider。"""
    provider = FakeFactProvider()
    svc = _make_service(async_session_factory, test_user, fact_provider=provider)
    results, cursor = await svc.search_facts("铝合金")
    assert len(results) == 1
    assert cursor is None
    assert len(provider.search_calls) == 1
    assert provider.search_calls[0][0] == "铝合金"
