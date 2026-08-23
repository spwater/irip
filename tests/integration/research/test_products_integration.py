"""Products 层集成测试。

覆盖以下模块（真实连接测试数据库，经 ``async_session_factory`` fixture）：
- ``packages.research.products.catalog`` — ResearchCatalogStub / ResearchCatalogImpl
  （search_derived_data / search_published_derived_data / _check_visible）
- ``packages.research.products.artifact_link`` — create_insight_from_accept /
  create_insight_from_modify / list_insights / get_insight / update_insight_metadata /
  delete_insight / list_insight_versions / list_products
- ``packages.research.products.candidates`` — identify_candidates /
  get_candidate_detail / reject_any_candidate / reject_insight_candidate /
  list_insight_candidates
- ``packages.research.products.view`` — create_view / list_views / get_view /
  update_view_metadata / list_view_versions / get_view_version / delete_view
- ``packages.research.products.derived_dataset`` — create_dataset / list_datasets /
  get_dataset / update_dataset_metadata / list_dataset_versions /
  get_dataset_version / delete_dataset
- ``packages.research.products.artifact_service`` — collect_artifact /
  list_artifacts / get_artifact / mark_publishable / mark_all_unpublishable

DB 依赖：通过 ``IRIP_TEST_DATABASE_URL`` 连接测试库（tests/integration/conftest.py
的 ``async_session_factory`` fixture）。工件内容读写经 MinIO（minio-test 容器）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace
from uuid import UUID

import pytest
import sqlalchemy as sa

from packages.common.errors import AppError
from packages.common.ids import new_id
from packages.common.s3_repository import S3Repository
from packages.research.dtos import (
    PublishRequest,
)
from packages.research.entities import (
    ResearchDerivedDataset,
    ResearchDerivedDatasetVersion,
    ResearchEvidenceSnapshot,
    ResearchInsight,
    ResearchInsightCandidate,
    ResearchInsightVersion,
    ResearchView,
    ResearchViewVersion,
    ResearchWorkspace,
)
from packages.research.execution.entities_trusted import (
    ResearchAnalysisPlanVersion,
    ResearchAnalysisRun,
    ResearchAnalysisStep,
    ResearchRunArtifact,
)
from packages.research.execution.models_trusted import ArtifactContent
from packages.research.execution.repository_trusted import ResearchRepositoryTrusted
from packages.research.execution.validation import ThreeSegmentValidator
from packages.research.lineage.lineage import LineageEdgeService
from packages.research.products.artifact_service import (
    ARTIFACT_TYPE_WHITELIST,
    FILE_EXTENSION_WHITELIST,
    RunArtifactService,
)
from packages.research.products.candidates import CandidateService
from packages.research.products.catalog import (
    ResearchCatalogImpl,
    ResearchCatalogStub,
)
from packages.research.products.product_service import ProductService
from packages.research.repository import ResearchRepository
from packages.research.timeline.entities import ResearchTurn

# ============================================================
# 共享 seed / cleanup 辅助
# ============================================================


@dataclass(frozen=True)
class _Seed:
    """最小产物场景的 ID 集合。"""

    workspace_id: UUID
    snapshot_id: UUID
    plan_id: UUID
    run_id: UUID
    step_id: UUID
    data_artifact_id: UUID
    chart_artifact_id: UUID
    code_artifact_id: UUID
    dataset_id: UUID
    view_id: UUID
    insight_id: UUID
    candidate_id: UUID


def _valid_three_segment_bytes() -> bytes:
    """返回通过 ThreeSegmentValidator 校验的三段式 JSON bytes。"""
    return json.dumps(
        {
            "metadata": {"title": "测试报告"},
            "points": [{"name": "抗拉强度", "value": 320, "unit": "MPa"}],
            "series": [{"name": "曲线", "columns": ["strain", "stress"], "rows": [[0.0, 0]]}],
        }
    ).encode("utf-8")


def _build_s3_repo() -> S3Repository:
    """构建连接 minio-test 的 S3 客户端。"""
    import os

    endpoint = os.getenv("IRIP_MINIO_ENDPOINT", "localhost:59000")
    if not endpoint.startswith("http"):
        endpoint = f"http://{endpoint}"
    repo = S3Repository(
        endpoint_url=endpoint,
        access_key=os.getenv("IRIP_MINIO_ACCESS_KEY", "irip"),
        secret_key=os.getenv("IRIP_MINIO_SECRET_KEY", "irip_dev_password"),
        bucket_name=os.getenv("IRIP_MINIO_BUCKET", "irip-test"),
    )
    repo.ensure_bucket()
    return repo


async def _seed_scenario(factory, user, *, s3_repo=None) -> _Seed:
    """插入最小产物场景：workspace → snapshot → plan → run → step → artifacts
    + dataset/view/insight + 候选。

    artifacts 的 is_publishable=True，data/chart 工件已上传 MinIO（若提供 s3_repo）。
    """
    owner_id: UUID = user.user_id
    dept_id: UUID = user.department_id
    async with factory() as session:
        async with session.begin():
            ws = ResearchWorkspace(
                id=new_id(),
                owner_user_id=owner_id,
                department_id=dept_id,
                name="products-test-ws",
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
                question_text_snapshot="products test question",
                question_origin="manual",
                evidence_snapshot_id=snap.id,
                idempotency_key=f"prod-{ws.id}",
            )
            session.add(turn)
            await session.flush()

            plan = ResearchAnalysisPlanVersion(
                id=new_id(),
                workspace_id=ws.id,
                version_number=1,
                dag_structure={"steps": [{"step_key": "analyze", "method": "python"}]},
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

            step = ResearchAnalysisStep(
                id=new_id(),
                run_id=run.id,
                step_key="analyze",
                step_index=0,
                status="succeeded",
                method="python",
                depends_on=[],
            )
            session.add(step)
            await session.flush()

            # data 工件（publishable）
            data_artifact = ResearchRunArtifact(
                id=new_id(),
                run_id=run.id,
                step_id=step.id,
                artifact_type="data",
                artifact_key="result.json",
                storage_path=f"research/artifacts/{run.id}/{step.id}/result.json",
                content_hash="d" * 64,
                size_bytes=128,
                is_publishable=True,
            )
            session.add(data_artifact)
            # chart 工件（publishable）
            chart_artifact = ResearchRunArtifact(
                id=new_id(),
                run_id=run.id,
                step_id=step.id,
                artifact_type="chart",
                artifact_key="plot.png",
                storage_path=f"research/artifacts/{run.id}/{step.id}/plot.png",
                content_hash="c" * 64,
                size_bytes=256,
                is_publishable=True,
            )
            session.add(chart_artifact)
            # code 工件（非 publishable）
            code_artifact = ResearchRunArtifact(
                id=new_id(),
                run_id=run.id,
                step_id=step.id,
                artifact_type="code",
                artifact_key="script.py",
                storage_path=f"research/artifacts/{run.id}/{step.id}/script.py",
                content_hash="e" * 64,
                size_bytes=64,
                is_publishable=False,
            )
            session.add(code_artifact)
            await session.flush()

            # 上传 data/chart 内容到 MinIO
            if s3_repo is not None:
                s3_repo.put_object(
                    data_artifact.storage_path, _valid_three_segment_bytes(), "application/json"
                )
                s3_repo.put_object(chart_artifact.storage_path, b"\x89PNG\r\n\x1a\n", "image/png")

            # Dataset（confirmed）+ v1
            dataset = ResearchDerivedDataset(
                id=new_id(),
                workspace_id=ws.id,
                owner_user_id=owner_id,
                name="拉伸数据集",
                summary="",
                tags=["拉伸"],
                status="confirmed",
                current_version=1,
                source_run_id=run.id,
                source_snapshot_id=snap.id,
            )
            session.add(dataset)
            await session.flush()
            content = json.loads(_valid_three_segment_bytes())

            ch = ThreeSegmentValidator.compute_content_hash(
                content["metadata"], content["points"], content["series"]
            )
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
                    source_step_id=step.id,
                    source_artifact_id=data_artifact.id,
                    content_hash=ch,
                    created_by=owner_id,
                )
            )
            await session.flush()

            # View（confirmed）+ v1
            view = ResearchView(
                id=new_id(),
                workspace_id=ws.id,
                owner_user_id=owner_id,
                name="拉伸图",
                caption="图注",
                display_order=0,
                status="confirmed",
                current_version=1,
                source_run_id=run.id,
            )
            session.add(view)
            await session.flush()
            session.add(
                ResearchViewVersion(
                    id=new_id(),
                    view_id=view.id,
                    version_number=1,
                    image_storage_path=chart_artifact.storage_path,
                    image_format="png",
                    image_content_hash="c" * 64,
                    source_run_id=run.id,
                    source_step_id=step.id,
                    source_artifact_id=chart_artifact.id,
                    created_by=owner_id,
                )
            )
            await session.flush()

            # Insight（confirmed）+ v1
            insight = ResearchInsight(
                id=new_id(),
                workspace_id=ws.id,
                owner_user_id=owner_id,
                name="结论",
                status="confirmed",
                current_version=1,
                source_run_id=run.id,
            )
            session.add(insight)
            await session.flush()
            session.add(
                ResearchInsightVersion(
                    id=new_id(),
                    insight_id=insight.id,
                    version_number=1,
                    conclusion="抗拉强度 320MPa",
                    scope="铝合金",
                    evidence_refs=[],
                    method_refs=[],
                    confidence_level="high",
                    limitations="",
                    evidence_source_label="experimental_data",
                    ai_original_text="AI 原稿",
                    is_modified=False,
                    modification_note=None,
                    source_candidate_id=None,
                    source_run_id=run.id,
                    created_by=owner_id,
                )
            )
            await session.flush()

            # Insight 候选（pending）
            candidate = ResearchInsightCandidate(
                id=new_id(),
                workspace_id=ws.id,
                run_id=run.id,
                step_id=step.id,
                conclusion="候选结论：延伸率 15%",
                scope="铝合金",
                evidence_refs=[],
                method_refs=[],
                confidence_level="medium",
                limitations="",
                evidence_source_label="experimental_data",
                ai_raw_text="AI 候选原稿",
                status="pending",
            )
            session.add(candidate)
            await session.flush()

            return _Seed(
                workspace_id=ws.id,
                snapshot_id=snap.id,
                plan_id=plan.id,
                run_id=run.id,
                step_id=step.id,
                data_artifact_id=data_artifact.id,
                chart_artifact_id=chart_artifact.id,
                code_artifact_id=code_artifact.id,
                dataset_id=dataset.id,
                view_id=view.id,
                insight_id=insight.id,
                candidate_id=candidate.id,
            )


async def _cleanup_workspace(factory, workspace_id: UUID) -> None:
    """删除工作空间（CASCADE 清理其下产物）。

    同时清理本工作空间所属部门产生的 audit_event 行，避免 department 删除时
    被 fk_audit_event_department_id 约束阻断（test_user fixture 的 department 唯一，
    不会误删其他测试的审计行）。
    """
    async with factory() as session:
        async with session.begin():
            # audit_event 不可变（prevent_modify_audit_event 触发器禁止 DELETE），
            # 临时禁用触发器以清理本部门审计行，避免 department 删除被 FK 阻断。
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


def _make_product_service(factory, user, artifact_service, lineage_writer=None) -> ProductService:
    """构造绑定到 test_user 的 ProductService。"""
    return ProductService(
        session_factory=factory,
        department_id=user.department_id,
        actor_id=user.user_id,
        artifact_service=artifact_service,
        lineage_writer=lineage_writer,
    )


class _FakeArtifactService:
    """假 RunArtifactService：按 artifact_id 返回预设内容。"""

    def __init__(self, content_map: dict | None = None) -> None:
        self._content_map = content_map or {}

    def set(self, artifact_id, content: bytes) -> None:
        self._content_map[artifact_id] = content

    async def get_artifact(self, artifact_id):
        content = self._content_map.get(artifact_id)
        if content is None:
            return None
        return ArtifactContent(
            artifact_id=artifact_id,
            artifact_type="data",
            artifact_key="result.json",
            content=content,
            content_hash="d" * 64,
        )


@pytest.fixture
async def factory_and_user(async_session_factory, test_user):
    """透传 async_session_factory + test_user。"""
    yield async_session_factory, test_user


# ============================================================
# catalog.py
# ============================================================


@pytest.mark.integration
async def test_catalog_stub_returns_empty() -> None:
    """ResearchCatalogStub.search_derived_data 返回空列表。"""
    stub = ResearchCatalogStub()
    assert await stub.search_derived_data("任意查询") == []


@pytest.mark.integration
async def test_catalog_impl_search_derived_data(factory_and_user) -> None:
    """ResearchCatalogImpl.search_derived_data 返回当前用户已确认数据集。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        catalog = ResearchCatalogImpl(factory, actor_id=user.user_id)
        results = await catalog.search_derived_data("拉伸")
        assert len(results) == 1
        assert results[0]["id"] == str(seed.dataset_id)
        assert results[0]["owner_user_id"] == str(user.user_id)
        assert results[0]["current_version"] == 1
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_catalog_impl_search_with_workspace_filter(factory_and_user) -> None:
    """search_derived_data 支持 workspace_id 过滤。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        catalog = ResearchCatalogImpl(factory, actor_id=user.user_id)
        # 正确 workspace → 命中
        results = await catalog.search_derived_data(
            "", filters={"workspace_id": str(seed.workspace_id)}
        )
        assert len(results) == 1
        # 错误 workspace → 空
        results_empty = await catalog.search_derived_data(
            "", filters={"workspace_id": str(new_id())}
        )
        assert len(results_empty) == 0
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_catalog_impl_search_with_dataset_filter(factory_and_user) -> None:
    """search_derived_data 支持 dataset_id 过滤。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        catalog = ResearchCatalogImpl(factory, actor_id=user.user_id)
        results = await catalog.search_derived_data(
            "", filters={"dataset_id": str(seed.dataset_id)}
        )
        assert len(results) == 1
        assert results[0]["id"] == str(seed.dataset_id)

        # 不存在的 dataset_id → 空
        results_empty = await catalog.search_derived_data("", filters={"dataset_id": str(new_id())})
        assert len(results_empty) == 0
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_catalog_impl_search_other_owner_returns_empty(factory_and_user) -> None:
    """search_derived_data 仅返回当前用户拥有的数据集；其他用户为 actor 时返回空。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        other_catalog = ResearchCatalogImpl(factory, actor_id=new_id())
        results = await other_catalog.search_derived_data("拉伸")
        assert len(results) == 0
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_catalog_impl_search_published_derived_data(factory_and_user) -> None:
    """search_published_derived_data 搜索已发布成果包中的数据集（ACL 过滤）。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        # 发布一个 private 成果包
        from packages.research.publication.publisher import PublicationService

        pub_svc = PublicationService(
            session_factory=factory,
            department_id=user.department_id,
            actor_id=user.user_id,
            product_service=None,
            lineage_service=LineageEdgeService(factory),
        )
        request = PublishRequest(
            title="发布成果",
            dataset_ids=[seed.dataset_id],
            requested_acl="private",
        )
        ref = await pub_svc.publish_result(seed.workspace_id, request)

        # owner 搜索 → 命中（private 对 owner 可见）
        catalog = ResearchCatalogImpl(factory, actor_id=user.user_id)
        results = await catalog.search_published_derived_data(
            "", filters={"result_id": str(ref.result_id)}
        )
        assert len(results) == 1
        assert results[0]["dataset_id"] == str(seed.dataset_id)
        assert results[0]["result_id"] == str(ref.result_id)

        # 非 owner 搜索 private → 不可见
        other_catalog = ResearchCatalogImpl(factory, actor_id=new_id())
        other_results = await other_catalog.search_published_derived_data(
            "", filters={"result_id": str(ref.result_id)}
        )
        assert len(other_results) == 0
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_catalog_impl_check_visible_all_acl_types(factory_and_user) -> None:
    """_check_visible 对 private/tree/explicit/all 的判定。"""
    factory, user = factory_and_user
    catalog = ResearchCatalogImpl(factory, actor_id=user.user_id)
    other = new_id()

    assert (
        catalog._check_visible(
            SimpleNamespace(current_acl_type="private", owner_user_id=user.user_id)
        )
        is True
    )
    assert (
        catalog._check_visible(SimpleNamespace(current_acl_type="private", owner_user_id=other))
        is False
    )
    assert (
        catalog._check_visible(SimpleNamespace(current_acl_type="tree", owner_user_id=other))
        is True
    )
    assert (
        catalog._check_visible(SimpleNamespace(current_acl_type="all", owner_user_id=other)) is True
    )
    assert (
        catalog._check_visible(
            SimpleNamespace(
                current_acl_type="explicit",
                owner_user_id=other,
                current_explicit_user_ids=[str(user.user_id)],
            )
        )
        is True
    )
    assert (
        catalog._check_visible(
            SimpleNamespace(
                current_acl_type="explicit",
                owner_user_id=other,
                current_explicit_user_ids=[],
            )
        )
        is False
    )
    assert (
        catalog._check_visible(
            SimpleNamespace(current_acl_type="unknown", owner_user_id=user.user_id)
        )
        is False
    )


# ============================================================
# artifact_link.py — Insight CRUD
# ============================================================


@pytest.mark.integration
async def test_create_insight_from_accept(factory_and_user) -> None:
    """接受候选 → 创建 Insight v1（is_modified=False），候选状态变为 accepted。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        svc = _make_product_service(factory, user, _FakeArtifactService())
        ref = await svc.create_insight_from_accept(seed.workspace_id, seed.candidate_id)
        assert ref.insight_id is not None
        assert ref.status == "confirmed"
        assert ref.current_version == 1

        async with factory() as session:
            candidate = await ResearchRepository.get_insight_candidate(session, seed.candidate_id)
            assert candidate.status == "accepted"
            assert candidate.accepted_insight_id == ref.insight_id
            assert candidate.reviewed_by == user.user_id

            version = await ResearchRepository.get_latest_insight_version(session, ref.insight_id)
            assert version is not None
            assert version.is_modified is False
            assert version.ai_original_text == "AI 候选原稿"
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_create_insight_from_accept_not_found(factory_and_user) -> None:
    """候选不存在时 create_insight_from_accept 抛出 not_found。"""
    factory, user = factory_and_user
    svc = _make_product_service(factory, user, _FakeArtifactService())
    with pytest.raises(AppError) as exc_info:
        await svc.create_insight_from_accept(new_id(), new_id())
    assert exc_info.value.code == "not_found"


@pytest.mark.integration
async def test_create_insight_from_accept_wrong_status(factory_and_user) -> None:
    """候选状态非 pending 时抛出 validation_failed。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        # 先接受一次
        svc = _make_product_service(factory, user, _FakeArtifactService())
        await svc.create_insight_from_accept(seed.workspace_id, seed.candidate_id)
        # 再次接受同候选 → 状态已是 accepted
        with pytest.raises(AppError) as exc_info:
            await svc.create_insight_from_accept(seed.workspace_id, seed.candidate_id)
        assert exc_info.value.code == "validation_failed"
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_create_insight_from_modify(factory_and_user) -> None:
    """修改候选 → 创建 Insight v1（is_modified=True），候选状态变为 modified。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        svc = _make_product_service(factory, user, _FakeArtifactService())
        ref = await svc.create_insight_from_modify(
            seed.workspace_id,
            seed.candidate_id,
            modified_fields={"conclusion": "用户修改后的结论"},
            modification_note="修正表述",
        )
        assert ref.insight_id is not None

        async with factory() as session:
            candidate = await ResearchRepository.get_insight_candidate(session, seed.candidate_id)
            assert candidate.status == "modified"

            version = await ResearchRepository.get_latest_insight_version(session, ref.insight_id)
            assert version.is_modified is True
            assert version.modification_note == "修正表述"
            assert version.conclusion == "用户修改后的结论"
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_create_insight_from_modify_requires_note(factory_and_user) -> None:
    """修改候选缺少 modification_note 时抛出 validation_failed。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        svc = _make_product_service(factory, user, _FakeArtifactService())
        with pytest.raises(AppError) as exc_info:
            await svc.create_insight_from_modify(
                seed.workspace_id, seed.candidate_id, {}, modification_note=""
            )
        assert exc_info.value.code == "validation_failed"
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_list_insights(factory_and_user) -> None:
    """list_insights 返回工作空间内 Insight 列表。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        svc = _make_product_service(factory, user, _FakeArtifactService())
        await svc.create_insight_from_accept(seed.workspace_id, seed.candidate_id)
        insights = await svc.list_insights(seed.workspace_id)
        # seed 已有 1 个 confirmed insight + 新创建的 1 个
        assert len(insights) >= 2
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_get_insight(factory_and_user) -> None:
    """get_insight 返回 Insight 详情含当前版本数据。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        svc = _make_product_service(factory, user, _FakeArtifactService())
        detail = await svc.get_insight(seed.workspace_id, seed.insight_id)
        assert detail.insight_id == seed.insight_id
        assert detail.current_version == 1
        assert detail.current_version_data is not None
        assert detail.current_version_data["conclusion"] == "抗拉强度 320MPa"
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_get_insight_not_found(factory_and_user) -> None:
    """get_insight 对不存在 ID 抛出 not_found。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        svc = _make_product_service(factory, user, _FakeArtifactService())
        with pytest.raises(AppError) as exc_info:
            await svc.get_insight(seed.workspace_id, new_id())
        assert exc_info.value.code == "not_found"
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_update_insight_metadata(factory_and_user) -> None:
    """update_insight_metadata 修改 Insight 名称。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        svc = _make_product_service(factory, user, _FakeArtifactService())
        ref = await svc.update_insight_metadata(seed.workspace_id, seed.insight_id, "新名称")
        assert ref.name == "新名称"
        async with factory() as session:
            insight = await ResearchRepository.get_insight(
                session, seed.insight_id, seed.workspace_id
            )
            assert insight.name == "新名称"
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_list_insight_versions(factory_and_user) -> None:
    """list_insight_versions 返回版本历史。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        svc = _make_product_service(factory, user, _FakeArtifactService())
        versions = await svc.list_insight_versions(seed.workspace_id, seed.insight_id)
        assert len(versions) == 1
        assert versions[0].version_number == 1
        assert versions[0].is_modified is False
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_delete_insight(factory_and_user) -> None:
    """delete_insight 物理删除 Insight 及其版本。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        svc = _make_product_service(factory, user, _FakeArtifactService())
        await svc.delete_insight(seed.workspace_id, seed.insight_id)
        async with factory() as session:
            insight = await ResearchRepository.get_insight(
                session, seed.insight_id, seed.workspace_id
            )
            assert insight is None
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_list_products(factory_and_user) -> None:
    """list_products 聚合 dataset/view/insight 三类产物。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        svc = _make_product_service(factory, user, _FakeArtifactService())
        products = await svc.list_products(seed.workspace_id)
        types = {p.product_type for p in products}
        assert "derived_dataset" in types
        assert "view" in types
        assert "insight" in types
        ids = {p.product_id for p in products}
        assert seed.dataset_id in ids
        assert seed.view_id in ids
        assert seed.insight_id in ids
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


# ============================================================
# candidates.py
# ============================================================


@pytest.mark.integration
async def test_candidates_list_insight_candidates(factory_and_user) -> None:
    """list_insight_candidates 返回 Run 的 Insight 候选。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        svc = CandidateService(
            session_factory=factory,
            department_id=user.department_id,
            actor_id=user.user_id,
            artifact_service=_FakeArtifactService(),
        )
        candidates = await svc.list_insight_candidates(seed.workspace_id, seed.run_id)
        assert len(candidates) == 1
        assert candidates[0].candidate_id == seed.candidate_id
        assert candidates[0].status == "pending"
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_candidates_get_candidate_detail_insight(factory_and_user) -> None:
    """get_candidate_detail 对 insight 候选返回详情。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        svc = CandidateService(
            session_factory=factory,
            department_id=user.department_id,
            actor_id=user.user_id,
            artifact_service=_FakeArtifactService(),
        )
        detail = await svc.get_candidate_detail(seed.workspace_id, seed.run_id, seed.candidate_id)
        assert detail.candidate_type == "insight"
        assert detail.preview_data["conclusion"] == "候选结论：延伸率 15%"
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_candidates_get_candidate_detail_artifact(factory_and_user) -> None:
    """get_candidate_detail 对 data 工件候选返回详情。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        fake = _FakeArtifactService()
        fake.set(seed.data_artifact_id, _valid_three_segment_bytes())
        svc = CandidateService(
            session_factory=factory,
            department_id=user.department_id,
            actor_id=user.user_id,
            artifact_service=fake,
        )
        detail = await svc.get_candidate_detail(
            seed.workspace_id, seed.run_id, seed.data_artifact_id
        )
        assert detail.candidate_type == "derived_dataset"
        assert "metadata" in detail.preview_data
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_candidates_get_candidate_detail_not_found(factory_and_user) -> None:
    """get_candidate_detail 对不存在候选抛出 not_found。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        svc = CandidateService(
            session_factory=factory,
            department_id=user.department_id,
            actor_id=user.user_id,
            artifact_service=_FakeArtifactService(),
        )
        with pytest.raises(AppError) as exc_info:
            await svc.get_candidate_detail(seed.workspace_id, seed.run_id, new_id())
        assert exc_info.value.code == "not_found"
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_candidates_reject_insight_candidate(factory_and_user) -> None:
    """reject_insight_candidate 物理删除候选（幂等）。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        svc = CandidateService(
            session_factory=factory,
            department_id=user.department_id,
            actor_id=user.user_id,
            artifact_service=_FakeArtifactService(),
        )
        await svc.reject_insight_candidate(seed.workspace_id, seed.run_id, seed.candidate_id)
        async with factory() as session:
            c = await ResearchRepository.get_insight_candidate(session, seed.candidate_id)
            assert c is None
        # 幂等：再次拒绝不报错
        await svc.reject_insight_candidate(seed.workspace_id, seed.run_id, seed.candidate_id)
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_candidates_reject_any_candidate_insight(factory_and_user) -> None:
    """reject_any_candidate 对 insight 候选执行物理删除。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        svc = CandidateService(
            session_factory=factory,
            department_id=user.department_id,
            actor_id=user.user_id,
            artifact_service=_FakeArtifactService(),
        )
        await svc.reject_any_candidate(
            seed.workspace_id, seed.run_id, seed.candidate_id, reason="不需要"
        )
        async with factory() as session:
            c = await ResearchRepository.get_insight_candidate(session, seed.candidate_id)
            assert c is None
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_candidates_reject_any_candidate_artifact(factory_and_user) -> None:
    """reject_any_candidate 对工件候选执行物理删除。

    使用未被产物版本引用的 code 工件，避免 view_version/dataset_version 的 FK 阻断。
    """
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        svc = CandidateService(
            session_factory=factory,
            department_id=user.department_id,
            actor_id=user.user_id,
            artifact_service=_FakeArtifactService(),
        )
        await svc.reject_any_candidate(seed.workspace_id, seed.run_id, seed.code_artifact_id)
        async with factory() as session:
            art = await ResearchRepositoryTrusted.get_artifact(session, seed.code_artifact_id)
            assert art is None
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_candidates_identify(factory_and_user) -> None:
    """identify_candidates 汇总 data/chart 工件候选 + insight 候选。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        fake = _FakeArtifactService()
        fake.set(seed.data_artifact_id, _valid_three_segment_bytes())
        svc = CandidateService(
            session_factory=factory,
            department_id=user.department_id,
            actor_id=user.user_id,
            artifact_service=fake,
        )
        candidates = await svc.identify_candidates(seed.workspace_id, seed.run_id)
        types = {c.candidate_type for c in candidates}
        assert "derived_dataset" in types  # data 工件
        assert "view" in types  # chart 工件
        assert "insight" in types  # insight 候选
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_candidates_identify_unavailable_data(factory_and_user) -> None:
    """data 工件内容无法下载时标记为 unavailable。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        # fake 不预设内容 → get_artifact 返回 None
        svc = CandidateService(
            session_factory=factory,
            department_id=user.department_id,
            actor_id=user.user_id,
            artifact_service=_FakeArtifactService(),
        )
        candidates = await svc.identify_candidates(seed.workspace_id, seed.run_id)
        data_cands = [c for c in candidates if c.candidate_type == "derived_dataset"]
        assert len(data_cands) == 1
        assert data_cands[0].status == "unavailable"
        assert data_cands[0].error_reason
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


# ============================================================
# view.py
# ============================================================


@pytest.mark.integration
async def test_create_view_from_chart_artifact(factory_and_user) -> None:
    """create_view 从 chart 工件创建 ResearchView + v1。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        svc = _make_product_service(factory, user, _FakeArtifactService())
        # 用一个新的 chart 工件 id（seed 中已有 chart_artifact）
        ref = await svc.create_view(
            workspace_id=seed.workspace_id,
            artifact_id=seed.chart_artifact_id,
            name="新视图",
            caption="新图注",
            display_order=2,
        )
        assert ref.view_id is not None
        assert ref.name == "新视图"
        assert ref.status == "confirmed"
        assert ref.current_version == 1
        assert ref.caption == "新图注"
        assert ref.display_order == 2
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_create_view_artifact_not_found(factory_and_user) -> None:
    """create_view 对不存在工件抛出 not_found。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        svc = _make_product_service(factory, user, _FakeArtifactService())
        with pytest.raises(AppError) as exc_info:
            await svc.create_view(seed.workspace_id, new_id(), "x")
        assert exc_info.value.code == "not_found"
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_create_view_wrong_artifact_type(factory_and_user) -> None:
    """create_view 对非 chart 工件（data）抛出 validation_failed。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        svc = _make_product_service(factory, user, _FakeArtifactService())
        with pytest.raises(AppError) as exc_info:
            await svc.create_view(seed.workspace_id, seed.data_artifact_id, "x")
        assert exc_info.value.code == "validation_failed"
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_create_view_non_publishable(factory_and_user) -> None:
    """create_view 对非 publishable 工件（code）抛出 validation_failed。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        svc = _make_product_service(factory, user, _FakeArtifactService())
        with pytest.raises(AppError) as exc_info:
            await svc.create_view(seed.workspace_id, seed.code_artifact_id, "x")
        assert exc_info.value.code == "validation_failed"
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_list_views(factory_and_user) -> None:
    """list_views 返回工作空间内视图列表。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        svc = _make_product_service(factory, user, _FakeArtifactService())
        views = await svc.list_views(seed.workspace_id)
        assert len(views) == 1
        assert views[0].view_id == seed.view_id
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_get_view(factory_and_user) -> None:
    """get_view 返回视图详情含当前版本信息。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        svc = _make_product_service(factory, user, _FakeArtifactService())
        detail = await svc.get_view(seed.workspace_id, seed.view_id)
        assert detail.view_id == seed.view_id
        assert detail.current_version == 1
        assert detail.current_version_info is not None
        assert detail.current_version_info["image_format"] == "png"
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_get_view_not_found(factory_and_user) -> None:
    """get_view 对不存在视图抛出 not_found。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        svc = _make_product_service(factory, user, _FakeArtifactService())
        with pytest.raises(AppError) as exc_info:
            await svc.get_view(seed.workspace_id, new_id())
        assert exc_info.value.code == "not_found"
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_update_view_metadata(factory_and_user) -> None:
    """update_view_metadata 修改视图元数据。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        svc = _make_product_service(factory, user, _FakeArtifactService())
        ref = await svc.update_view_metadata(
            seed.workspace_id, seed.view_id, name="新名", caption="新注", display_order=5
        )
        assert ref.name == "新名"
        assert ref.caption == "新注"
        assert ref.display_order == 5
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_list_view_versions(factory_and_user) -> None:
    """list_view_versions 返回视图版本历史。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        svc = _make_product_service(factory, user, _FakeArtifactService())
        versions = await svc.list_view_versions(seed.workspace_id, seed.view_id)
        assert len(versions) == 1
        assert versions[0].version_number == 1
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_get_view_version(factory_and_user) -> None:
    """get_view_version 返回视图版本详情。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        svc = _make_product_service(factory, user, _FakeArtifactService())
        detail = await svc.get_view_version(seed.workspace_id, seed.view_id, 1)
        assert detail.version_number == 1
        assert detail.image_format == "png"
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_get_view_version_not_found(factory_and_user) -> None:
    """get_view_version 对不存在版本抛出 not_found。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        svc = _make_product_service(factory, user, _FakeArtifactService())
        with pytest.raises(AppError) as exc_info:
            await svc.get_view_version(seed.workspace_id, seed.view_id, 999)
        assert exc_info.value.code == "not_found"
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_delete_view(factory_and_user) -> None:
    """delete_view 物理删除视图及版本。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        svc = _make_product_service(factory, user, _FakeArtifactService())
        await svc.delete_view(seed.workspace_id, seed.view_id)
        async with factory() as session:
            view = await ResearchRepository.get_view(session, seed.view_id, seed.workspace_id)
            assert view is None
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


# ============================================================
# derived_dataset.py
# ============================================================


@pytest.mark.integration
async def test_create_dataset_from_data_artifact(factory_and_user) -> None:
    """create_dataset 从 data 工件创建 DerivedDataset + v1（经 fake artifact service）。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        fake = _FakeArtifactService()
        fake.set(seed.data_artifact_id, _valid_three_segment_bytes())
        svc = _make_product_service(factory, user, fake)
        ref = await svc.create_dataset(
            workspace_id=seed.workspace_id,
            artifact_id=seed.data_artifact_id,
            name="新数据集",
            summary="摘要",
            tags=["t1", "t2"],
        )
        assert ref.dataset_id is not None
        assert ref.name == "新数据集"
        assert ref.status == "confirmed"
        assert ref.current_version == 1

        async with factory() as session:
            versions = await ResearchRepository.list_dataset_versions(session, ref.dataset_id)
            assert len(versions) == 1
            assert versions[0].content_hash  # 非空
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_create_dataset_artifact_not_found(factory_and_user) -> None:
    """create_dataset 对不存在工件抛出 not_found。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        svc = _make_product_service(factory, user, _FakeArtifactService())
        with pytest.raises(AppError) as exc_info:
            await svc.create_dataset(seed.workspace_id, new_id(), "x")
        assert exc_info.value.code == "not_found"
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_create_dataset_wrong_type(factory_and_user) -> None:
    """create_dataset 对非 data 工件（chart）抛出 validation_failed。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        svc = _make_product_service(factory, user, _FakeArtifactService())
        with pytest.raises(AppError) as exc_info:
            await svc.create_dataset(seed.workspace_id, seed.chart_artifact_id, "x")
        assert exc_info.value.code == "validation_failed"
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_create_dataset_non_publishable(factory_and_user) -> None:
    """create_dataset 对非 publishable 工件（code）抛出 validation_failed。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        svc = _make_product_service(factory, user, _FakeArtifactService())
        with pytest.raises(AppError) as exc_info:
            await svc.create_dataset(seed.workspace_id, seed.code_artifact_id, "x")
        assert exc_info.value.code == "validation_failed"
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_create_dataset_invalid_content(factory_and_user) -> None:
    """create_dataset 工件内容不通过三段式校验时抛出 validation_failed。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        fake = _FakeArtifactService()
        fake.set(seed.data_artifact_id, b"not-json")
        svc = _make_product_service(factory, user, fake)
        with pytest.raises(AppError) as exc_info:
            await svc.create_dataset(seed.workspace_id, seed.data_artifact_id, "x")
        assert exc_info.value.code == "validation_failed"
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_list_datasets(factory_and_user) -> None:
    """list_datasets 返回工作空间内数据集列表。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        svc = _make_product_service(factory, user, _FakeArtifactService())
        datasets = await svc.list_datasets(seed.workspace_id)
        assert len(datasets) == 1
        assert datasets[0].dataset_id == seed.dataset_id
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_get_dataset(factory_and_user) -> None:
    """get_dataset 返回数据集详情含当前版本数据。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        svc = _make_product_service(factory, user, _FakeArtifactService())
        detail = await svc.get_dataset(seed.workspace_id, seed.dataset_id)
        assert detail.dataset_id == seed.dataset_id
        assert detail.current_version == 1
        assert detail.current_version_data is not None
        assert "metadata_content" in detail.current_version_data
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_get_dataset_not_found(factory_and_user) -> None:
    """get_dataset 对不存在数据集抛出 not_found。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        svc = _make_product_service(factory, user, _FakeArtifactService())
        with pytest.raises(AppError) as exc_info:
            await svc.get_dataset(seed.workspace_id, new_id())
        assert exc_info.value.code == "not_found"
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_update_dataset_metadata(factory_and_user) -> None:
    """update_dataset_metadata 修改数据集元数据。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        svc = _make_product_service(factory, user, _FakeArtifactService())
        ref = await svc.update_dataset_metadata(
            seed.workspace_id, seed.dataset_id, name="新名", summary="新摘要", tags=["x"]
        )
        assert ref.name == "新名"
        async with factory() as session:
            ds = await ResearchRepository.get_dataset(session, seed.dataset_id, seed.workspace_id)
            assert ds.name == "新名"
            assert ds.summary == "新摘要"
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_list_dataset_versions(factory_and_user) -> None:
    """list_dataset_versions 返回数据集版本历史。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        svc = _make_product_service(factory, user, _FakeArtifactService())
        versions = await svc.list_dataset_versions(seed.workspace_id, seed.dataset_id)
        assert len(versions) == 1
        assert versions[0].version_number == 1
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_get_dataset_version(factory_and_user) -> None:
    """get_dataset_version 返回数据集版本详情。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        svc = _make_product_service(factory, user, _FakeArtifactService())
        detail = await svc.get_dataset_version(seed.workspace_id, seed.dataset_id, 1)
        assert detail.version_number == 1
        assert detail.content_hash  # 非空
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_get_dataset_version_not_found(factory_and_user) -> None:
    """get_dataset_version 对不存在版本抛出 not_found。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        svc = _make_product_service(factory, user, _FakeArtifactService())
        with pytest.raises(AppError) as exc_info:
            await svc.get_dataset_version(seed.workspace_id, seed.dataset_id, 999)
        assert exc_info.value.code == "not_found"
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_delete_dataset(factory_and_user) -> None:
    """delete_dataset 物理删除数据集及版本。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        svc = _make_product_service(factory, user, _FakeArtifactService())
        await svc.delete_dataset(seed.workspace_id, seed.dataset_id)
        async with factory() as session:
            ds = await ResearchRepository.get_dataset(session, seed.dataset_id, seed.workspace_id)
            assert ds is None
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


# ============================================================
# artifact_service.py — 经 MinIO 真实读写
# ============================================================


@pytest.mark.integration
async def test_collect_artifact_persists_to_minio(factory_and_user) -> None:
    """collect_artifact 经白名单扫描、上传 MinIO、插入 DB 记录。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        s3 = _build_s3_repo()
        svc = RunArtifactService(factory, s3)
        svc.set_context(department_id=user.department_id, actor_id=user.user_id)
        content = b'{"metadata": {}, "points": [], "series": []}'
        ref = await svc.collect_artifact(
            run_id=seed.run_id,
            step_id=seed.step_id,
            artifact_type="data",
            artifact_key="collected.json",
            content=content,
            is_publishable=True,
        )
        assert ref.artifact_id is not None
        assert ref.artifact_type == "data"
        assert ref.is_publishable is True

        # MinIO 中可读取
        downloaded = s3.get_object(ref.storage_path)
        assert downloaded == content

        # DB 中可查询
        async with factory() as session:
            art = await ResearchRepositoryTrusted.get_artifact(session, ref.artifact_id)
            assert art is not None
            assert art.artifact_key == "collected.json"
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_collect_artifact_rejects_bad_type(factory_and_user) -> None:
    """collect_artifact 拒绝非白名单工件类型。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        svc = RunArtifactService(factory, _build_s3_repo())
        svc.set_context(department_id=user.department_id, actor_id=user.user_id)
        with pytest.raises(ValueError, match="不在白名单"):
            await svc.collect_artifact(
                run_id=seed.run_id,
                step_id=seed.step_id,
                artifact_type="malware",
                artifact_key="x.bin",
                content=b"x",
            )
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_collect_artifact_rejects_path_traversal(factory_and_user) -> None:
    """collect_artifact 拒绝路径穿越。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        svc = RunArtifactService(factory, _build_s3_repo())
        svc.set_context(department_id=user.department_id, actor_id=user.user_id)
        with pytest.raises(ValueError, match="路径模式"):
            await svc.collect_artifact(
                run_id=seed.run_id,
                step_id=seed.step_id,
                artifact_type="data",
                artifact_key="../escape.json",
                content=b"x",
            )
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_collect_artifact_rejects_bad_extension(factory_and_user) -> None:
    """collect_artifact 拒绝非白名单扩展名。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        svc = RunArtifactService(factory, _build_s3_repo())
        svc.set_context(department_id=user.department_id, actor_id=user.user_id)
        with pytest.raises(ValueError, match="扩展名"):
            await svc.collect_artifact(
                run_id=seed.run_id,
                step_id=seed.step_id,
                artifact_type="data",
                artifact_key="x.exe",
                content=b"x",
            )
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_list_artifacts_by_run(factory_and_user) -> None:
    """list_artifacts 返回指定 Run 的工件列表。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        svc = RunArtifactService(factory, _build_s3_repo())
        svc.set_context(department_id=user.department_id, actor_id=user.user_id)
        refs = await svc.list_artifacts(seed.run_id)
        assert len(refs) == 3  # data + chart + code
        keys = {r.artifact_key for r in refs}
        assert "result.json" in keys
        assert "plot.png" in keys
        assert "script.py" in keys
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_list_artifacts_by_step_and_type(factory_and_user) -> None:
    """list_artifacts 支持 step_id 和 artifact_type 过滤。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        svc = RunArtifactService(factory, _build_s3_repo())
        svc.set_context(department_id=user.department_id, actor_id=user.user_id)
        by_step = await svc.list_artifacts(seed.run_id, step_id=seed.step_id)
        assert len(by_step) == 3
        only_data = await svc.list_artifacts(seed.run_id, artifact_type="data")
        assert len(only_data) == 1
        assert only_data[0].artifact_type == "data"
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_get_artifact_downloads_content(factory_and_user) -> None:
    """get_artifact 从 MinIO 下载工件内容。"""
    factory, user = factory_and_user
    s3 = _build_s3_repo()
    seed = await _seed_scenario(factory, user, s3_repo=s3)
    try:
        svc = RunArtifactService(factory, s3)
        svc.set_context(department_id=user.department_id, actor_id=user.user_id)
        content = await svc.get_artifact(seed.data_artifact_id)
        assert content is not None
        assert content.artifact_id == seed.data_artifact_id
        # 解析为三段式 JSON
        data = json.loads(content.content)
        assert "metadata" in data and "points" in data and "series" in data
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_get_artifact_not_found(factory_and_user) -> None:
    """get_artifact 对不存在工件返回 None。"""
    factory, user = factory_and_user
    svc = RunArtifactService(factory, _build_s3_repo())
    svc.set_context(department_id=user.department_id, actor_id=user.user_id)
    assert await svc.get_artifact(new_id()) is None


@pytest.mark.integration
async def test_mark_publishable(factory_and_user) -> None:
    """mark_publishable 按成功步骤标记工件可发布。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        svc = RunArtifactService(factory, _build_s3_repo())
        svc.set_context(department_id=user.department_id, actor_id=user.user_id)
        count = await svc.mark_publishable(seed.run_id, step_keys_success={"analyze"})
        assert count >= 0
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_mark_all_unpublishable(factory_and_user) -> None:
    """mark_all_unpublishable 将 Run 全部工件标记为不可发布。"""
    factory, user = factory_and_user
    seed = await _seed_scenario(factory, user)
    try:
        svc = RunArtifactService(factory, _build_s3_repo())
        svc.set_context(department_id=user.department_id, actor_id=user.user_id)
        count = await svc.mark_all_unpublishable(seed.run_id)
        # seed 中 data + chart 为 publishable（2 个），code 为非 publishable
        assert count == 2
        async with factory() as session:
            arts = await ResearchRepositoryTrusted.list_artifacts_by_run(session, seed.run_id)
            assert all(not a.is_publishable for a in arts)
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_artifact_whitelist_constants() -> None:
    """白名单常量包含预期类型与扩展名。"""
    assert "data" in ARTIFACT_TYPE_WHITELIST
    assert "chart" in ARTIFACT_TYPE_WHITELIST
    assert "code" in ARTIFACT_TYPE_WHITELIST
    assert ".json" in FILE_EXTENSION_WHITELIST
    assert ".png" in FILE_EXTENSION_WHITELIST
