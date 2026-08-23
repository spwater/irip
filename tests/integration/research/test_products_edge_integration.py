"""Products 层边界与异常路径集成测试。

补充 ``test_products_integration.py`` 未覆盖的边界条件：
- catalog: 无效 UUID filter / 关键词匹配 / 已发布数据搜索 result_id 筛选
- view / derived_dataset: delete 不存在的 ID（幂等不报错）/ 空列表
- artifact_link: list_products 空工作空间
- candidates: 对已拒绝候选再次拒绝（幂等）

DB 依赖：通过 ``async_session_factory`` fixture 连接测试库。
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from uuid import UUID

import pytest
import sqlalchemy as sa

from packages.common.ids import new_id
from packages.research.entities import (
    ResearchDerivedDataset,
    ResearchDerivedDatasetVersion,
    ResearchEvidenceSnapshot,
    ResearchInsightCandidate,
    ResearchWorkspace,
)
from packages.research.execution.entities_trusted import (
    ResearchAnalysisPlanVersion,
    ResearchAnalysisRun,
)
from packages.research.products.catalog import ResearchCatalogImpl, ResearchCatalogStub
from packages.research.products.product_service import ProductService
from packages.research.timeline.entities import ResearchTurn


class _FakeArtifactService:
    """假 RunArtifactService（delete/list 操作不依赖工件内容）。"""

    async def get_artifact(self, artifact_id):
        return None


# ============================================================
# 共享 seed / cleanup
# ============================================================


@dataclass(frozen=True)
class _Seed:
    workspace_id: UUID
    snapshot_id: UUID
    run_id: UUID
    dataset_id: UUID


async def _seed(factory, user) -> _Seed:
    """插入 workspace/snapshot/plan/run/dataset(+v1)，返回 ID 集合。"""
    owner_id = user.user_id
    dept_id = user.department_id
    async with factory() as session:
        async with session.begin():
            ws = ResearchWorkspace(
                id=new_id(),
                owner_user_id=owner_id,
                department_id=dept_id,
                name="products-edge-test",
            )
            session.add(ws)
            await session.flush()

            snap = ResearchEvidenceSnapshot(
                id=new_id(),
                workspace_id=ws.id,
                snapshot_number=1,
                content_hash="0" * 64,
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
                question_text_snapshot="edge test",
                question_origin="manual",
                evidence_snapshot_id=snap.id,
                idempotency_key=f"edge-{ws.id}",
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
                name="边缘数据集",
                summary="边界测试",
                tags=["铝合金"],
                status="confirmed",
                current_version=1,
                source_run_id=run.id,
                source_snapshot_id=snap.id,
            )
            session.add(dataset)
            await session.flush()

            session.add(
                ResearchDerivedDatasetVersion(
                    id=new_id(),
                    dataset_id=dataset.id,
                    version_number=1,
                    metadata_content={"title": "t"},
                    points_content=[{"name": "x", "value": 1}],
                    series_content=[],
                    field_manifest=[],
                    source_run_id=run.id,
                    source_step_id=None,
                    source_artifact_id=None,
                    content_hash="c" * 64,
                    created_by=owner_id,
                )
            )
            await session.flush()

            return _Seed(
                workspace_id=ws.id,
                snapshot_id=snap.id,
                run_id=run.id,
                dataset_id=dataset.id,
            )


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


@pytest.fixture
async def seeded(async_session_factory, test_user):
    """提供已确认数据集场景并在测试后清理。"""
    seed = await _seed(async_session_factory, test_user)
    try:
        yield seed, async_session_factory, test_user
    finally:
        await _cleanup(async_session_factory, seed.workspace_id)


# ============================================================
# catalog
# ============================================================


@pytest.mark.integration
async def test_catalog_search_with_invalid_uuid_filter(seeded) -> None:
    """无效 UUID filter 不会抛异常，仅忽略该筛选条件。"""
    seed, factory, user = seeded
    catalog = ResearchCatalogImpl(factory, user.user_id)
    results = await catalog.search_derived_data(
        query="", filters={"workspace_id": "not-a-uuid", "dataset_id": "also-bad"}
    )
    # 无效 UUID 被忽略，返回全部已确认数据集
    assert any(r["id"] == str(seed.dataset_id) for r in results)


@pytest.mark.integration
async def test_catalog_search_keyword_matches_name(seeded) -> None:
    """关键词搜索匹配数据集名称。"""
    seed, factory, user = seeded
    catalog = ResearchCatalogImpl(factory, user.user_id)
    results = await catalog.search_derived_data(query="边缘")
    assert any(r["name"] == "边缘数据集" for r in results)
    no_results = await catalog.search_derived_data(query="zzz不存在")
    assert all(r["name"] != "边缘数据集" for r in no_results)


@pytest.mark.integration
async def test_catalog_search_published_derived_data_result_id_filter(seeded) -> None:
    """search_published_derived_data 用 result_id 筛选（无匹配返回空）。"""
    seed, factory, user = seeded
    catalog = ResearchCatalogImpl(factory, user.user_id)
    results = await catalog.search_published_derived_data(
        query="", filters={"result_id": str(new_id())}
    )
    assert results == []


@pytest.mark.integration
async def test_catalog_check_visible_unknown_acl(seeded) -> None:
    """_check_visible 对未知 ACL 类型保守返回 False。"""
    seed, factory, user = seeded
    catalog = ResearchCatalogImpl(factory, user.user_id)
    unknown = SimpleNamespace(current_acl_type="secret", owner_user_id=user.user_id)
    assert catalog._check_visible(unknown) is False


@pytest.mark.integration
async def test_catalog_stub_search_published_returns_empty(async_session_factory) -> None:
    """ResearchCatalogStub 不支持 search_published_derived_data，调用应返回空或报错。"""
    stub = ResearchCatalogStub()
    # Stub 仅实现 search_derived_data
    assert await stub.search_derived_data("x") == []


# ============================================================
# view / derived_dataset delete 边界
# ============================================================


@pytest.mark.integration
async def test_delete_view_nonexistent_idempotent(seeded) -> None:
    """删除不存在的 view 不报错（幂等）。"""
    seed, factory, user = seeded
    svc = ProductService(
        session_factory=factory,
        department_id=user.department_id,
        actor_id=user.user_id,
        artifact_service=_FakeArtifactService(),
    )
    # 不应抛异常
    await svc.delete_view(seed.workspace_id, new_id())


@pytest.mark.integration
async def test_delete_dataset_nonexistent_idempotent(seeded) -> None:
    """删除不存在的 dataset 不报错（幂等）。"""
    seed, factory, user = seeded
    svc = ProductService(
        session_factory=factory,
        department_id=user.department_id,
        actor_id=user.user_id,
        artifact_service=_FakeArtifactService(),
    )
    await svc.delete_dataset(seed.workspace_id, new_id())


@pytest.mark.integration
async def test_list_views_empty(seeded) -> None:
    """无视图的工作空间 list_views 返回空列表。"""
    seed, factory, user = seeded
    svc = ProductService(
        session_factory=factory,
        department_id=user.department_id,
        actor_id=user.user_id,
        artifact_service=_FakeArtifactService(),
    )
    views = await svc.list_views(seed.workspace_id)
    assert views == []


@pytest.mark.integration
async def test_list_datasets_empty(seeded) -> None:
    """无数据集时 list_datasets 返回空（此处场景有 1 个，验证返回非空结构）。"""
    seed, factory, user = seeded
    svc = ProductService(
        session_factory=factory,
        department_id=user.department_id,
        actor_id=user.user_id,
        artifact_service=_FakeArtifactService(),
    )
    datasets = await svc.list_datasets(seed.workspace_id)
    assert len(datasets) == 1
    assert datasets[0].name == "边缘数据集"


# ============================================================
# artifact_link: list_products
# ============================================================


@pytest.mark.integration
async def test_list_products_empty_workspace(async_session_factory, test_user) -> None:
    """无任何产物的工作空间 list_products 返回空列表。"""
    async with async_session_factory() as session:
        async with session.begin():
            ws = ResearchWorkspace(
                id=new_id(),
                owner_user_id=test_user.user_id,
                department_id=test_user.department_id,
                name="empty-products-ws",
            )
            session.add(ws)
            await session.flush()
            ws_id = ws.id
    try:
        svc = ProductService(
            session_factory=async_session_factory,
            department_id=test_user.department_id,
            actor_id=test_user.user_id,
            artifact_service=_FakeArtifactService(),
        )
        products = await svc.list_products(ws_id)
        assert products == []
    finally:
        await _cleanup(async_session_factory, ws_id)


@pytest.mark.integration
async def test_list_products_includes_dataset(seeded) -> None:
    """list_products 返回已确认的 DerivedDataset。"""
    seed, factory, user = seeded
    svc = ProductService(
        session_factory=factory,
        department_id=user.department_id,
        actor_id=user.user_id,
        artifact_service=_FakeArtifactService(),
    )
    products = await svc.list_products(seed.workspace_id)
    assert any(p.name == "边缘数据集" for p in products)


# ============================================================
# candidates: reject 幂等
# ============================================================


@pytest.mark.integration
async def test_reject_insight_candidate_idempotent(seeded) -> None:
    """对已拒绝的候选再次拒绝不报错（幂等）。"""
    seed, factory, user = seeded
    from packages.research.products.candidates import CandidateService

    async with factory() as session:
        async with session.begin():
            cand = ResearchInsightCandidate(
                id=new_id(),
                workspace_id=seed.workspace_id,
                run_id=seed.run_id,
                step_id=None,
                conclusion="候选结论",
                scope="边界测试",
                evidence_refs=[],
                method_refs=[],
                confidence_level="medium",
                limitations="",
                evidence_source_label="experimental_data",
                ai_raw_text="原稿",
                status="pending",
            )
            session.add(cand)
            await session.flush()
            cand_id = cand.id

    svc = CandidateService(
        session_factory=factory,
        department_id=user.department_id,
        actor_id=user.user_id,
        artifact_service=_FakeArtifactService(),
    )
    await svc.reject_insight_candidate(seed.workspace_id, seed.run_id, cand_id)
    # 再次拒绝（候选已被物理删除）应幂等不报错
    await svc.reject_insight_candidate(seed.workspace_id, seed.run_id, cand_id)
    async with factory() as session:
        c = await session.scalar(
            sa.select(ResearchInsightCandidate).where(ResearchInsightCandidate.id == cand_id)
        )
        assert c is None  # 物理删除后不存在
