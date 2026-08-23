"""ResultSearchService 集成测试：成果包搜索。

覆盖 ``packages.research.publication.search`` 的核心逻辑：
- search: 全部 / 我发布的 / 我收藏的 三种视图
- 关键词搜索（title/summary/tags ILIKE 匹配）
- 筛选器（publisher / date / tags / data_type / workspace_id）
- 权限过滤（private / tree / explicit / all）
- list_results: 无关键词列表
- _match_query / _check_result_visible 单元逻辑
- 分页（page / page_size 边界）

DB 依赖：通过 ``async_session_factory`` fixture 连接测试库。
"""

from __future__ import annotations

import hashlib
import json
from uuid import UUID

import pytest
import sqlalchemy as sa

from packages.common.ids import new_id
from packages.research.dtos import PublishRequest
from packages.research.entities import (
    ResearchDerivedDataset,
    ResearchDerivedDatasetVersion,
    ResearchEvidenceSnapshot,
    ResearchWorkspace,
)
from packages.research.execution.entities_trusted import (
    ResearchAnalysisPlanVersion,
    ResearchAnalysisRun,
)
from packages.research.lineage.lineage import LineageEdgeService
from packages.research.publication.publisher import PublicationService
from packages.research.publication.search import ResultSearchService
from packages.research.timeline.entities import ResearchTurn

# ============================================================
# 共享 seed / cleanup
# ============================================================


def _valid_three_segment() -> dict:
    return {
        "metadata": {"title": "测试报告", "sample": "铝合金-001"},
        "points": [{"name": "抗拉强度", "value": 320, "unit": "MPa"}],
        "series": [{"name": "拉伸曲线", "columns": ["strain", "stress"], "rows": [[0.0, 0]]}],
    }


async def _seed_and_publish(
    factory, user, *, title="搜索成果包", tags=None, acl="all"
) -> tuple[UUID, UUID]:
    """插入完整场景并发布一个成果包，返回 (workspace_id, result_id)。"""
    owner_id = user.user_id
    dept_id = user.department_id
    async with factory() as session:
        async with session.begin():
            ws = ResearchWorkspace(
                id=new_id(),
                owner_user_id=owner_id,
                department_id=dept_id,
                name="search-test-ws",
            )
            session.add(ws)
            await session.flush()

            snap = ResearchEvidenceSnapshot(
                id=new_id(),
                workspace_id=ws.id,
                snapshot_number=1,
                content_hash="a" * 64,
                permission_envelope={},
                field_manifest={},
                source_refs=[],
                created_by=owner_id,
            )
            session.add(snap)
            await session.flush()

            turn = ResearchTurn(
                id=new_id(),
                workspace_id=ws.id,
                turn_number=1,
                kind="analysis",
                status="queued",
                question_text_snapshot="search test",
                question_origin="manual",
                evidence_snapshot_id=snap.id,
                idempotency_key=f"search-{ws.id}",
            )
            session.add(turn)
            await session.flush()

            plan = ResearchAnalysisPlanVersion(
                id=new_id(),
                workspace_id=ws.id,
                version_number=1,
                dag_structure={"steps": []},
                status="confirmed",
                created_by=owner_id,
                turn_id=turn.id,
            )
            session.add(plan)
            await session.flush()

            run = ResearchAnalysisRun(
                id=new_id(),
                workspace_id=ws.id,
                plan_version_id=plan.id,
                snapshot_id=snap.id,
                run_number=1,
                status="succeeded",
                image_digest="llm-only",
                created_by=owner_id,
                turn_id=turn.id,
                attempt_number=1,
            )
            session.add(run)
            await session.flush()

            dataset = ResearchDerivedDataset(
                id=new_id(),
                workspace_id=ws.id,
                owner_user_id=owner_id,
                name="搜索数据集",
                summary="搜索测试",
                tags=[],
                status="confirmed",
                current_version=1,
                source_run_id=run.id,
                source_snapshot_id=snap.id,
            )
            session.add(dataset)
            await session.flush()

            content = _valid_three_segment()
            ch = hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()
            session.add(
                ResearchDerivedDatasetVersion(
                    id=new_id(),
                    dataset_id=dataset.id,
                    version_number=1,
                    metadata_content=content["metadata"],
                    points_content=content["points"],
                    series_content=content["series"],
                    field_manifest=[],
                    source_run_id=run.id,
                    source_step_id=None,
                    source_artifact_id=None,
                    content_hash=ch,
                    created_by=owner_id,
                )
            )
            await session.flush()
            dataset_id = dataset.id
            ws_id = ws.id

    pub_svc = PublicationService(
        session_factory=factory,
        department_id=dept_id,
        actor_id=owner_id,
        product_service=None,
        lineage_service=LineageEdgeService(factory),
    )
    request = PublishRequest(
        title=title,
        summary="搜索摘要",
        tags=tags or ["铝合金"],
        dataset_ids=[dataset_id],
        requested_acl=acl,
    )
    ref = await pub_svc.publish_result(ws_id, request)
    return ws_id, ref.result_id


async def _cleanup(factory, workspace_id: UUID) -> None:
    """清理溯源边 + 审计 + 工作空间。"""
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
                sa.text("DELETE FROM research_lineage_edge WHERE workspace_id = :wid"),
                {"wid": str(workspace_id)},
            )
            await session.execute(
                sa.text("DELETE FROM research_workspace WHERE id = :wid"),
                {"wid": str(workspace_id)},
            )


def _make_search_service(factory, user) -> ResultSearchService:
    return ResultSearchService(
        session_factory=factory,
        department_id=user.department_id,
        actor_id=user.user_id,
    )


# ============================================================
# search
# ============================================================


@pytest.mark.integration
async def test_search_all_returns_published_result(async_session_factory, test_user) -> None:
    """search 视图 all 返回已发布成果包。"""
    ws_id, result_id = await _seed_and_publish(async_session_factory, test_user, acl="all")
    try:
        svc = _make_search_service(async_session_factory, test_user)
        page = await svc.search(query=None, filters=None, view_mode="all", page=1, page_size=20)
        assert page.total >= 1
        assert any(item.result_id == result_id for item in page.items)
    finally:
        await _cleanup(async_session_factory, ws_id)


@pytest.mark.integration
async def test_search_mine_returns_owner_only(async_session_factory, test_user) -> None:
    """search 视图 mine 仅返回当前用户发布的成果包。"""
    ws_id, result_id = await _seed_and_publish(async_session_factory, test_user, acl="all")
    try:
        svc = _make_search_service(async_session_factory, test_user)
        page = await svc.search(query=None, filters=None, view_mode="mine", page=1, page_size=20)
        assert any(item.result_id == result_id for item in page.items)
    finally:
        await _cleanup(async_session_factory, ws_id)


@pytest.mark.integration
async def test_search_favorites_returns_empty_initially(async_session_factory, test_user) -> None:
    """未收藏时 favorites 视图返回空。"""
    ws_id, _ = await _seed_and_publish(async_session_factory, test_user, acl="all")
    try:
        svc = _make_search_service(async_session_factory, test_user)
        page = await svc.search(
            query=None, filters=None, view_mode="favorites", page=1, page_size=20
        )
        assert page.total == 0
    finally:
        await _cleanup(async_session_factory, ws_id)


@pytest.mark.integration
async def test_search_keyword_matches_title(async_session_factory, test_user) -> None:
    """关键词匹配 title。"""
    ws_id, result_id = await _seed_and_publish(
        async_session_factory, test_user, title="铝合金拉伸研究", acl="all"
    )
    try:
        svc = _make_search_service(async_session_factory, test_user)
        page = await svc.search(query="铝合金", filters=None, view_mode="all", page=1, page_size=20)
        assert any(item.result_id == result_id for item in page.items)
        # 不匹配的关键词
        page_empty = await svc.search(
            query="zzz不存在", filters=None, view_mode="all", page=1, page_size=20
        )
        assert all(item.result_id != result_id for item in page_empty.items)
    finally:
        await _cleanup(async_session_factory, ws_id)


@pytest.mark.integration
async def test_search_filter_by_tags(async_session_factory, test_user) -> None:
    """tags 筛选器匹配任一标签。"""
    ws_id, result_id = await _seed_and_publish(
        async_session_factory, test_user, tags=["铝合金", "拉伸"], acl="all"
    )
    try:
        svc = _make_search_service(async_session_factory, test_user)
        page = await svc.search(
            query=None, filters={"tags": ["铝合金"]}, view_mode="all", page=1, page_size=20
        )
        assert any(item.result_id == result_id for item in page.items)
        page_no = await svc.search(
            query=None, filters={"tags": ["不存在的标签"]}, view_mode="all", page=1, page_size=20
        )
        assert all(item.result_id != result_id for item in page_no.items)
    finally:
        await _cleanup(async_session_factory, ws_id)


@pytest.mark.integration
async def test_search_filter_by_data_type(async_session_factory, test_user) -> None:
    """data_type 筛选器按存在性过滤。"""
    ws_id, result_id = await _seed_and_publish(async_session_factory, test_user, acl="all")
    try:
        svc = _make_search_service(async_session_factory, test_user)
        # 含 dataset 的成果包
        page_ds = await svc.search(
            query=None, filters={"data_type": "dataset"}, view_mode="all", page=1, page_size=20
        )
        assert any(item.result_id == result_id for item in page_ds.items)
        # 含 view 的成果包（本场景无 view）→ 不应包含
        page_view = await svc.search(
            query=None, filters={"data_type": "view"}, view_mode="all", page=1, page_size=20
        )
        assert all(item.result_id != result_id for item in page_view.items)
    finally:
        await _cleanup(async_session_factory, ws_id)


@pytest.mark.integration
async def test_search_filter_by_workspace_id(async_session_factory, test_user) -> None:
    """workspace_id 筛选器按来源工作空间过滤。"""
    ws_id, result_id = await _seed_and_publish(async_session_factory, test_user, acl="all")
    try:
        svc = _make_search_service(async_session_factory, test_user)
        page = await svc.search(
            query=None,
            filters={"workspace_id": str(ws_id)},
            view_mode="all",
            page=1,
            page_size=20,
        )
        assert any(item.result_id == result_id for item in page.items)
        page_other = await svc.search(
            query=None,
            filters={"workspace_id": str(new_id())},
            view_mode="all",
            page=1,
            page_size=20,
        )
        assert all(item.result_id != result_id for item in page_other.items)
    finally:
        await _cleanup(async_session_factory, ws_id)


@pytest.mark.integration
async def test_search_private_not_visible_to_others(async_session_factory, test_user) -> None:
    """private 成果包对非 owner 不可见。"""
    ws_id, _ = await _seed_and_publish(async_session_factory, test_user, acl="private")
    try:
        other_svc = ResultSearchService(
            session_factory=async_session_factory,
            department_id=test_user.department_id,
            actor_id=new_id(),
        )
        page = await other_svc.search(
            query=None, filters=None, view_mode="all", page=1, page_size=20
        )
        assert all(item.workspace_id != ws_id for item in page.items)
    finally:
        await _cleanup(async_session_factory, ws_id)


@pytest.mark.integration
async def test_search_pagination(async_session_factory, test_user) -> None:
    """分页返回正确的切片。"""
    ws_id, _ = await _seed_and_publish(async_session_factory, test_user, acl="all")
    try:
        svc = _make_search_service(async_session_factory, test_user)
        page1 = await svc.search(query=None, filters=None, view_mode="all", page=1, page_size=1)
        assert len(page1.items) <= 1
        assert page1.page == 1
        assert page1.page_size == 1
    finally:
        await _cleanup(async_session_factory, ws_id)


# ============================================================
# list_results
# ============================================================


@pytest.mark.integration
async def test_list_results_delegates_to_search(async_session_factory, test_user) -> None:
    """list_results 等价于 search(query=None)。"""
    ws_id, _ = await _seed_and_publish(async_session_factory, test_user, acl="all")
    try:
        svc = _make_search_service(async_session_factory, test_user)
        page = await svc.list_results(view_mode="all", page=1, page_size=20)
        assert page.total >= 1
    finally:
        await _cleanup(async_session_factory, ws_id)


# ============================================================
# _match_query / _check_result_visible 单元逻辑
# ============================================================


@pytest.mark.integration
async def test_match_query_matches_title_summary_tags(async_session_factory, test_user) -> None:
    """_match_query 匹配 title / summary / tags。"""
    svc = _make_search_service(async_session_factory, test_user)
    version = type("V", (), {"title": "铝合金研究", "summary": None, "tags": []})()
    assert svc._match_query(version, "铝合金") is True
    version2 = type("V", (), {"title": None, "summary": "关于拉伸的摘要", "tags": []})()
    assert svc._match_query(version2, "拉伸") is True
    version3 = type("V", (), {"title": None, "summary": None, "tags": ["钢材"]})()
    assert svc._match_query(version3, "钢材") is True
    version4 = type("V", (), {"title": None, "summary": None, "tags": []})()
    assert svc._match_query(version4, "不存在") is False


@pytest.mark.integration
async def test_check_result_visible_all_acl_types(async_session_factory, test_user) -> None:
    """_check_result_visible 对 private/tree/explicit/all 的判定。"""
    svc = _make_search_service(async_session_factory, test_user)
    me = test_user.user_id
    other = new_id()

    private = type("R", (), {"current_acl_type": "private", "owner_user_id": me})()
    assert svc._check_result_visible(private, me) is True
    assert svc._check_result_visible(private, other) is False

    tree = type("R", (), {"current_acl_type": "tree", "owner_user_id": other})()
    assert svc._check_result_visible(tree, me) is True

    all_acl = type("R", (), {"current_acl_type": "all", "owner_user_id": other})()
    assert svc._check_result_visible(all_acl, me) is True

    explicit = type(
        "R",
        (),
        {
            "current_acl_type": "explicit",
            "owner_user_id": me,
            "current_explicit_user_ids": [str(other)],
        },
    )()
    assert svc._check_result_visible(explicit, other) is True
    assert svc._check_result_visible(explicit, me) is True

    unknown = type("R", (), {"current_acl_type": "secret", "owner_user_id": me})()
    assert svc._check_result_visible(unknown, me) is False


@pytest.mark.integration
async def test_search_requires_actor(async_session_factory, test_user) -> None:
    """actor_id 为 None 时 search 抛出 forbidden。"""
    svc = ResultSearchService(
        session_factory=async_session_factory,
        department_id=test_user.department_id,
        actor_id=None,
    )
    with pytest.raises(Exception) as exc_info:
        await svc.search(query=None, filters=None, view_mode="all", page=1, page_size=20)
    assert "forbidden" in str(exc_info.value) or "已认证" in str(exc_info.value)
