"""Publication 层集成测试。

覆盖以下模块（真实连接测试数据库，经 ``async_session_factory`` fixture）：
- ``packages.research.publication._base`` — _require_actor / _check_result_visible
- ``packages.research.publication.publisher`` — publish_result / publish_new_version /
  preview_publish / _collect_product_refs / _validate_run_statuses / _compute_content_hash
- ``packages.research.publication.acl`` — update_acl
- ``packages.research.publication.revision`` — withdraw_result /
  update_result_metadata / get_result_detail / get_version_detail / list_versions /
  list_acl_revisions
- ``packages.research.publication.reuse`` — get_result_internal_object /
  add_to_workspace / new_workspace_from_result / toggle_favorite
- ``packages.research.publication.knowledge_reference`` — save_reference /
  list_references_by_run / list_references_by_insight / get_reference
- ``packages.research.publication.knowledge_provider`` — MockKnowledgeProvider /
  KnowledgeProviderService.search / search_all / _merge_and_deduplicate

DB 依赖：通过 ``IRIP_TEST_DATABASE_URL`` 连接测试库（tests/integration/conftest.py
的 ``async_session_factory`` fixture）。
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
from packages.research.dtos import (
    KnowledgeSearchOptions,
    KnowledgeSearchResult,
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
)
from packages.research.lineage.lineage import LineageEdgeService
from packages.research.publication._base import _PublicationBase
from packages.research.publication.knowledge_provider import (
    KnowledgeProviderService,
    MockKnowledgeProvider,
)
from packages.research.publication.knowledge_reference import (
    SNIPPET_INLINE_THRESHOLD,
    SNIPPET_MAX_SIZE,
    TRUNCATION_SUFFIX,
    KnowledgeReferenceService,
)
from packages.research.publication.publisher import PublicationService
from packages.research.repository import ResearchRepository
from packages.research.timeline.entities import ResearchTurn

# ============================================================
# 共享 seed / cleanup 辅助
# ============================================================


@dataclass(frozen=True)
class _Seed:
    """最小发布场景的 ID 集合。"""

    workspace_id: UUID
    snapshot_id: UUID
    plan_id: UUID
    run_id: UUID
    dataset_id: UUID
    view_id: UUID
    insight_id: UUID
    candidate_id: UUID


def _valid_three_segment() -> dict:
    """返回一份通过 ThreeSegmentValidator 校验的三段式数据。"""
    return {
        "metadata": {"title": "测试报告", "sample": "铝合金-001"},
        "points": [
            {"name": "抗拉强度", "value": 320, "unit": "MPa"},
            {"name": "屈服强度", "value": 280, "unit": "MPa"},
        ],
        "series": [
            {
                "name": "拉伸曲线",
                "columns": ["strain", "stress"],
                "rows": [[0.0, 0], [0.01, 320]],
            }
        ],
    }


async def _seed_full_scenario(factory, user, *, run_status: str = "succeeded") -> _Seed:
    """插入最小可发布场景并返回 ID 集合。

    按 FK 依赖分层 flush：workspace → snapshot/plan → run → dataset/view/insight + 版本
    + 候选。
    """
    owner_id: UUID = user.user_id
    dept_id: UUID = user.department_id
    async with factory() as session:
        async with session.begin():
            ws = ResearchWorkspace(
                id=new_id(),
                owner_user_id=owner_id,
                department_id=dept_id,
                name="publication-test-ws",
            )
            session.add(ws)
            await session.flush()

            snap = ResearchEvidenceSnapshot(
                id=new_id(),
                workspace_id=ws.id,
                snapshot_number=1,
                content_hash="a" * 64,
                permission_envelope={},  # 空 → 包络 ACL = all
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
                question_text_snapshot="publication test question",
                question_origin="manual",
                evidence_snapshot_id=snap.id,
                idempotency_key=f"pub-{ws.id}",
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
                status=run_status,
                image_digest="llm-only",
                created_by=owner_id,
                turn_id=turn.id,
                attempt_number=1,
            )
            session.add(run)
            await session.flush()

            # Dataset（confirmed）+ v1
            dataset = ResearchDerivedDataset(
                id=new_id(),
                workspace_id=ws.id,
                owner_user_id=owner_id,
                name="拉伸测试数据集",
                summary="铝合金拉伸测试",
                tags=["拉伸", "铝合金"],
                status="confirmed",
                current_version=1,
                source_run_id=run.id,
                source_snapshot_id=snap.id,
            )
            session.add(dataset)
            await session.flush()

            import hashlib

            content = _valid_three_segment()
            content_hash = hashlib.sha256(
                json.dumps(content, sort_keys=True).encode("utf-8")
            ).hexdigest()
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
                    content_hash=content_hash,
                    created_by=owner_id,
                )
            )
            await session.flush()

            # View（confirmed）+ v1
            view = ResearchView(
                id=new_id(),
                workspace_id=ws.id,
                owner_user_id=owner_id,
                name="拉伸曲线图",
                caption="应力-应变曲线",
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
                    image_storage_path="research/views/x.png",
                    image_format="png",
                    image_content_hash="b" * 64,
                    source_run_id=run.id,
                    source_step_id=None,
                    source_artifact_id=None,
                    created_by=owner_id,
                )
            )
            await session.flush()

            # Insight（confirmed）+ v1
            insight = ResearchInsight(
                id=new_id(),
                workspace_id=ws.id,
                owner_user_id=owner_id,
                name="抗拉强度结论",
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
                    conclusion="抗拉强度为 320MPa，满足设计要求",
                    scope="适用于铝合金-001",
                    evidence_refs=[],
                    method_refs=[],
                    confidence_level="high",
                    limitations="仅测试单批次样品",
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
                step_id=None,
                conclusion="候选结论：延伸率 15%",
                scope="铝合金-001",
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
                dataset_id=dataset.id,
                view_id=view.id,
                insight_id=insight.id,
                candidate_id=candidate.id,
            )


async def _cleanup_workspace(factory, workspace_id: UUID) -> None:
    """删除工作空间（CASCADE 清理其下 snapshot/run/dataset/view/insight/result 等）。

    research_lineage_edge 以 workspace_id 为外键但非 CASCADE，单独清理。
    research_workspace_evidence_ref 由 new_workspace_from_result 创建，随 workspace CASCADE。
    同时清理本部门产生的 audit_event 行，避免 department 删除时被
    fk_audit_event_department_id 约束阻断。
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


def _make_publication_service(factory, user) -> PublicationService:
    """构造绑定到 test_user 的 PublicationService。"""
    return PublicationService(
        session_factory=factory,
        department_id=user.department_id,
        actor_id=user.user_id,
        product_service=None,
        lineage_service=LineageEdgeService(factory),
    )


@pytest.fixture
async def seeded(factory_and_user):
    """提供已发布场景并在测试后清理。"""
    factory, user = factory_and_user
    seed = await _seed_full_scenario(factory, user)
    try:
        yield seed, factory, user
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.fixture
async def factory_and_user(async_session_factory, test_user):
    """透传 async_session_factory + test_user。"""
    yield async_session_factory, test_user


# ============================================================
# _base.py
# ============================================================


@pytest.mark.integration
async def test_require_actor_raises_when_none(async_session_factory, test_user) -> None:
    """actor_id 为 None 时 _require_actor 抛出 forbidden。"""
    svc = _PublicationBase(
        session_factory=async_session_factory,
        department_id=test_user.department_id,
        actor_id=None,
        product_service=None,
        lineage_service=LineageEdgeService(async_session_factory),
    )
    with pytest.raises(AppError) as exc_info:
        svc._require_actor()
    assert exc_info.value.code == "forbidden"


@pytest.mark.integration
async def test_require_actor_returns_id_when_set(async_session_factory, test_user) -> None:
    """actor_id 已设置时 _require_actor 返回该 ID。"""
    svc = _PublicationBase(
        session_factory=async_session_factory,
        department_id=test_user.department_id,
        actor_id=test_user.user_id,
        product_service=None,
        lineage_service=LineageEdgeService(async_session_factory),
    )
    assert svc._require_actor() == test_user.user_id


@pytest.mark.integration
async def test_check_result_visible_all_acl_types(async_session_factory, test_user) -> None:
    """_check_result_visible 对 private/explicit/tree/all 的可见性判定。"""
    svc = _PublicationBase(
        session_factory=async_session_factory,
        department_id=test_user.department_id,
        actor_id=test_user.user_id,
        product_service=None,
        lineage_service=LineageEdgeService(async_session_factory),
    )
    other = UUID(int=1234)

    # private: 仅 owner 可见
    private_owner = SimpleNamespace(current_acl_type="private", owner_user_id=test_user.user_id)
    assert svc._check_result_visible(private_owner, test_user.user_id) is True
    assert svc._check_result_visible(private_owner, other) is False

    # tree: 同部门可见（简化为 True）
    tree = SimpleNamespace(current_acl_type="tree", owner_user_id=other)
    assert svc._check_result_visible(tree, test_user.user_id) is True

    # all: 全部可见
    all_acl = SimpleNamespace(current_acl_type="all", owner_user_id=other)
    assert svc._check_result_visible(all_acl, test_user.user_id) is True

    # explicit: 指定用户可见 + owner 可见
    explicit = SimpleNamespace(
        current_acl_type="explicit",
        owner_user_id=test_user.user_id,
        current_explicit_user_ids=[str(other)],
    )
    assert svc._check_result_visible(explicit, other) is True
    assert svc._check_result_visible(explicit, test_user.user_id) is True
    assert svc._check_result_visible(explicit, UUID(int=9999)) is False

    # 未知 ACL：保守不可见
    unknown = SimpleNamespace(current_acl_type="secret", owner_user_id=test_user.user_id)
    assert svc._check_result_visible(unknown, test_user.user_id) is False


# ============================================================
# publisher.py — publish_result / publish_new_version / preview_publish
# ============================================================


@pytest.mark.integration
async def test_publish_result_creates_result_and_v1(seeded) -> None:
    """publish_result 成功创建 ResearchResult + v1 + 初始 ACL Revision + 溯源边。"""
    seed, factory, user = seeded
    svc = _make_publication_service(factory, user)
    request = PublishRequest(
        title="成果包 v1",
        summary="首次发布",
        tags=["铝合金", "拉伸"],
        dataset_ids=[seed.dataset_id],
        requested_acl="private",
    )

    ref = await svc.publish_result(seed.workspace_id, request)

    assert ref.result_id is not None
    assert ref.version_number == 1
    assert ref.title == "成果包 v1"
    assert ref.status == "active"

    async with factory() as session:
        result = await ResearchRepository.get_result(session, ref.result_id)
        assert result is not None
        assert result.current_version == 1
        assert result.current_acl_type == "private"
        assert result.owner_user_id == user.user_id

        versions = await ResearchRepository.list_result_versions(session, result.id)
        assert len(versions) == 1
        assert versions[0].version_number == 1
        assert versions[0].status == "active"
        assert versions[0].dataset_version_refs[0]["dataset_id"] == str(seed.dataset_id)

        revisions = await ResearchRepository.list_acl_revisions(session, result.id)
        assert len(revisions) == 1
        assert revisions[0].revision_number == 1
        assert revisions[0].acl_type == "private"

    await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_publish_result_requires_dataset_or_view(seeded) -> None:
    """仅含 Insight（无 dataset/view）时 publish_result 校验失败。"""
    seed, factory, user = seeded
    svc = _make_publication_service(factory, user)
    request = PublishRequest(
        title="仅 Insight",
        insight_ids=[seed.insight_id],
        requested_acl="private",
    )
    with pytest.raises(AppError) as exc_info:
        await svc.publish_result(seed.workspace_id, request)
    assert exc_info.value.code == "validation_failed"

    await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_publish_result_dataset_not_in_workspace(seeded) -> None:
    """dataset 不存在或不属于该 workspace 时校验失败。"""
    seed, factory, user = seeded
    svc = _make_publication_service(factory, user)
    request = PublishRequest(
        title="不存在数据集",
        dataset_ids=[new_id()],  # 随机不存在的 ID
        requested_acl="private",
    )
    with pytest.raises(AppError) as exc_info:
        await svc.publish_result(seed.workspace_id, request)
    assert exc_info.value.code == "validation_failed"

    await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_publish_result_run_not_succeeded(factory_and_user) -> None:
    """来源 Run 状态非 succeeded/partially_succeeded 时禁止发布。"""
    factory, user = factory_and_user
    seed = await _seed_full_scenario(factory, user, run_status="failed")
    try:
        svc = _make_publication_service(factory, user)
        request = PublishRequest(
            title="失败 Run 发布",
            dataset_ids=[seed.dataset_id],
            requested_acl="private",
        )
        with pytest.raises(AppError) as exc_info:
            await svc.publish_result(seed.workspace_id, request)
        assert exc_info.value.code == "validation_failed"
        assert "succeeded" in str(exc_info.value)
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_publish_new_version_supersedes_old(seeded) -> None:
    """publish_new_version 标记旧版本为 superseded 并创建新版本。"""
    seed, factory, user = seeded
    svc = _make_publication_service(factory, user)
    request_v1 = PublishRequest(
        title="成果包 v1",
        dataset_ids=[seed.dataset_id],
        requested_acl="private",
    )
    ref_v1 = await svc.publish_result(seed.workspace_id, request_v1)

    request_v2 = PublishRequest(
        title="成果包 v2",
        summary="修订发布",
        dataset_ids=[seed.dataset_id],
        requested_acl="private",
    )
    ref_v2 = await svc.publish_new_version(ref_v1.result_id, seed.workspace_id, request_v2)

    assert ref_v2.version_number == 2
    assert ref_v2.status == "active"

    async with factory() as session:
        versions = await ResearchRepository.list_result_versions(session, ref_v1.result_id)
        # v2(active) 在前，v1(superseded) 在后（按版本号降序）
        assert len(versions) == 2
        v2, v1 = versions
        assert v2.version_number == 2 and v2.status == "active"
        assert v1.version_number == 1 and v1.status == "superseded"

        result = await ResearchRepository.get_result(session, ref_v1.result_id)
        assert result is not None
        assert result.current_version == 2

    await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_publish_new_version_non_owner_forbidden(seeded) -> None:
    """非 owner 调用 publish_new_version 抛出 forbidden。"""
    seed, factory, user = seeded
    svc = _make_publication_service(factory, user)
    request = PublishRequest(
        title="成果包",
        dataset_ids=[seed.dataset_id],
        requested_acl="private",
    )
    ref = await svc.publish_result(seed.workspace_id, request)

    # 用另一个 actor 构造 service
    other_svc = PublicationService(
        session_factory=factory,
        department_id=user.department_id,
        actor_id=new_id(),
        product_service=None,
        lineage_service=LineageEdgeService(factory),
    )
    with pytest.raises(AppError) as exc_info:
        await other_svc.publish_new_version(ref.result_id, seed.workspace_id, request)
    assert exc_info.value.code == "forbidden"

    await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_publish_new_version_result_not_found(seeded) -> None:
    """成果包不存在时 publish_new_version 抛出 not_found。"""
    seed, factory, user = seeded
    svc = _make_publication_service(factory, user)
    request = PublishRequest(
        title="x",
        dataset_ids=[seed.dataset_id],
        requested_acl="private",
    )
    with pytest.raises(AppError) as exc_info:
        await svc.publish_new_version(new_id(), seed.workspace_id, request)
    assert exc_info.value.code == "not_found"

    await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_preview_publish_returns_envelope(seeded) -> None:
    """preview_publish 返回产物引用 + 权限包络，不创建数据。"""
    seed, factory, user = seeded
    svc = _make_publication_service(factory, user)
    request = PublishRequest(
        title="预览",
        dataset_ids=[seed.dataset_id],
        requested_acl="private",
    )
    preview = await svc.preview_publish(seed.workspace_id, request)
    assert preview.envelope.acl_type == "all"  # 空 permission_envelope → all
    assert preview.validation.valid is True
    assert preview.product_refs.dataset_version_refs[0]["dataset_id"] == str(seed.dataset_id)

    # 确认未创建任何成果包
    async with factory() as session:
        results = await ResearchRepository.list_results_by_workspace(session, seed.workspace_id)
        assert len(results) == 0

    await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_compute_content_hash_deterministic(seeded) -> None:
    """_compute_content_hash 对相同输入返回相同哈希，且随 tags 排序稳定。"""
    seed, factory, user = seeded
    svc = _make_publication_service(factory, user)
    request = PublishRequest(
        title="哈希测试",
        summary="s",
        tags=["b", "a"],  # 乱序
        dataset_ids=[seed.dataset_id],
        requested_acl="private",
    )
    async with factory() as session:
        product_refs = await svc._collect_product_refs(session, seed.workspace_id, request)
    h1 = svc._compute_content_hash(request, product_refs)
    h2 = svc._compute_content_hash(request, product_refs)
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex

    # tags 顺序不同但内容相同 → 哈希一致（sorted）
    request_rev = PublishRequest(
        title="哈希测试",
        summary="s",
        tags=["a", "b"],
        dataset_ids=[seed.dataset_id],
        requested_acl="private",
    )
    h3 = svc._compute_content_hash(request_rev, product_refs)
    assert h3 == h1

    await _cleanup_workspace(factory, seed.workspace_id)


# ============================================================
# acl.py — update_acl
# ============================================================


async def _publish_one(seeded) -> tuple:
    """发布一个 private 成果包，返回 (result_id, factory, user, svc)。"""
    seed, factory, user = seeded
    svc = _make_publication_service(factory, user)
    request = PublishRequest(
        title="ACL 测试成果包",
        dataset_ids=[seed.dataset_id],
        requested_acl="private",
    )
    ref = await svc.publish_result(seed.workspace_id, request)
    return ref.result_id, seed, factory, user, svc


@pytest.mark.integration
async def test_update_acl_owner_changes_to_tree(seeded) -> None:
    """owner 将 ACL 从 private 改为 tree（在包络 all 内）。"""
    result_id, seed, factory, user, svc = await _publish_one(seeded)
    ref = await svc.update_acl(
        result_id=result_id,
        acl_type="tree",
        explicit_user_ids=None,
        reason="部门共享",
        is_declassify=False,
        declassify_reason=None,
    )
    assert ref.revision_number == 2
    assert ref.acl_type == "tree"
    assert ref.previous_acl_type == "private"

    async with factory() as session:
        result = await ResearchRepository.get_result(session, result_id)
        assert result.current_acl_type == "tree"
        revisions = await ResearchRepository.list_acl_revisions(session, result_id)
        assert len(revisions) == 2

    await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_update_acl_non_owner_forbidden(seeded) -> None:
    """非 owner 修改 ACL 抛出 forbidden。"""
    result_id, seed, factory, user, svc = await _publish_one(seeded)
    other_svc = PublicationService(
        session_factory=factory,
        department_id=user.department_id,
        actor_id=new_id(),
        product_service=None,
        lineage_service=LineageEdgeService(factory),
    )
    with pytest.raises(AppError) as exc_info:
        await other_svc.update_acl(
            result_id=result_id,
            acl_type="tree",
            explicit_user_ids=None,
            reason="x",
            is_declassify=False,
            declassify_reason=None,
        )
    assert exc_info.value.code == "forbidden"

    await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_update_acl_not_found(seeded) -> None:
    """成果包不存在时 update_acl 抛出 not_found。"""
    seed, factory, user = seeded
    svc = _make_publication_service(factory, user)
    with pytest.raises(AppError) as exc_info:
        await svc.update_acl(
            result_id=new_id(),
            acl_type="tree",
            explicit_user_ids=None,
            reason="x",
            is_declassify=False,
            declassify_reason=None,
        )
    assert exc_info.value.code == "not_found"

    await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_update_acl_exceeds_envelope_without_declassify(seeded) -> None:
    """请求 all 超出包络（all=3 == 包络 all=3，实际在包络内）—— 改为请求 all 且包络为 all 应成功。

    这里改为构造一个超出包络的场景：将 snapshot 的 permission_envelope 设为含非 active
    条目，使包络收紧为 private，再请求 tree（超出）且非 declassify → 抛错。
    """
    seed, factory, user = seeded
    # 将 snapshot 的 permission_envelope 改为含非 active 条目
    async with factory() as session:
        async with session.begin():
            await session.execute(
                sa.update(ResearchEvidenceSnapshot)
                .where(ResearchEvidenceSnapshot.id == seed.snapshot_id)
                .values(permission_envelope={"fact_x": {"status": "archived"}})
            )

    svc = _make_publication_service(factory, user)
    request = PublishRequest(
        title="收紧包络成果包",
        dataset_ids=[seed.dataset_id],
        requested_acl="private",  # private 始终在包络内
    )
    ref = await svc.publish_result(seed.workspace_id, request)

    # 现在包络为 private，请求 tree（rank 2 > 0）超出
    with pytest.raises(AppError) as exc_info:
        await svc.update_acl(
            result_id=ref.result_id,
            acl_type="tree",
            explicit_user_ids=None,
            reason="x",
            is_declassify=False,
            declassify_reason=None,
        )
    assert exc_info.value.code == "acl_exceeds_envelope"

    await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_update_acl_declassify_allows_exceeding(seeded) -> None:
    """declassify 操作允许超出包络（需提供 declassify_reason）。"""
    seed, factory, user = seeded
    async with factory() as session:
        async with session.begin():
            await session.execute(
                sa.update(ResearchEvidenceSnapshot)
                .where(ResearchEvidenceSnapshot.id == seed.snapshot_id)
                .values(permission_envelope={"fact_x": {"status": "archived"}})
            )

    svc = _make_publication_service(factory, user)
    request = PublishRequest(
        title="declassify 成果包",
        dataset_ids=[seed.dataset_id],
        requested_acl="private",
    )
    ref = await svc.publish_result(seed.workspace_id, request)

    ref_acl = await svc.update_acl(
        result_id=ref.result_id,
        acl_type="all",
        explicit_user_ids=None,
        reason="已脱敏",
        is_declassify=True,
        declassify_reason="数据已公开",
    )
    assert ref_acl.acl_type == "all"
    assert ref_acl.is_declassify is True

    await _cleanup_workspace(factory, seed.workspace_id)


# ============================================================
# revision.py
# ============================================================


@pytest.mark.integration
async def test_withdraw_result_single_version(seeded) -> None:
    """撤回指定版本：状态变为 withdrawn。"""
    result_id, seed, factory, user, svc = await _publish_one(seeded)
    await svc.withdraw_result(result_id, version_number=1, reason="内容错误")

    async with factory() as session:
        version = await ResearchRepository.get_result_version(session, result_id, 1)
        assert version is not None
        assert version.status == "withdrawn"

    await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_withdraw_result_all_versions(seeded) -> None:
    """version_number=None 撤回全部版本，且 ResearchResult.status 变为 withdrawn。"""
    result_id, seed, factory, user, svc = await _publish_one(seeded)
    # 发布第二个版本以便有多版本
    request_v2 = PublishRequest(
        title="v2",
        dataset_ids=[seed.dataset_id],
        requested_acl="private",
    )
    await svc.publish_new_version(result_id, seed.workspace_id, request_v2)

    await svc.withdraw_result(result_id, version_number=None, reason="整体撤回")

    async with factory() as session:
        versions = await ResearchRepository.list_result_versions(session, result_id)
        # withdraw_result(version_number=None) 仅将 active 版本标记为 withdrawn；
        # 已 superseded 的旧版本保持 superseded。确认无版本处于 active。
        assert all(v.status != "active" for v in versions)
        # 最新版本（v2）应为 withdrawn
        assert versions[0].status == "withdrawn"
        result = await ResearchRepository.get_result(session, result_id)
        assert result.status == "withdrawn"

    await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_withdraw_result_version_not_found(seeded) -> None:
    """撤回不存在的版本号抛出 not_found。"""
    result_id, seed, factory, user, svc = await _publish_one(seeded)
    with pytest.raises(AppError) as exc_info:
        await svc.withdraw_result(result_id, version_number=999, reason="x")
    assert exc_info.value.code == "not_found"

    await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_withdraw_result_non_owner_forbidden(seeded) -> None:
    """非 owner 撤回抛出 forbidden。"""
    result_id, seed, factory, user, _ = await _publish_one(seeded)
    other_svc = PublicationService(
        session_factory=factory,
        department_id=user.department_id,
        actor_id=new_id(),
        product_service=None,
        lineage_service=LineageEdgeService(factory),
    )
    with pytest.raises(AppError) as exc_info:
        await other_svc.withdraw_result(result_id, version_number=1)
    assert exc_info.value.code == "forbidden"

    await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_update_result_metadata(seeded) -> None:
    """编辑成果包名称。"""
    result_id, seed, factory, user, svc = await _publish_one(seeded)
    ref = await svc.update_result_metadata(result_id, name="新名称")
    assert ref.name == "新名称"

    async with factory() as session:
        result = await ResearchRepository.get_result(session, result_id)
        assert result.name == "新名称"

    await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_update_result_metadata_non_owner_forbidden(seeded) -> None:
    """非 owner 编辑元数据抛出 forbidden。"""
    result_id, seed, factory, user, _ = await _publish_one(seeded)
    other_svc = PublicationService(
        session_factory=factory,
        department_id=user.department_id,
        actor_id=new_id(),
        product_service=None,
        lineage_service=LineageEdgeService(factory),
    )
    with pytest.raises(AppError) as exc_info:
        await other_svc.update_result_metadata(result_id, name="x")
    assert exc_info.value.code == "forbidden"

    await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_get_result_detail(seeded) -> None:
    """get_result_detail 返回当前版本 + 版本历史 + ACL 历史 + 收藏状态。"""
    result_id, seed, factory, user, svc = await _publish_one(seeded)
    detail = await svc.get_result_detail(result_id)
    assert detail.result_ref.result_id == result_id
    assert detail.current_version is not None
    assert detail.current_version.version_number == 1
    assert len(detail.version_history) == 1
    assert len(detail.acl_revisions) == 1
    assert detail.is_favorited is False

    await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_get_result_detail_not_found(seeded) -> None:
    """成果包不存在时 get_result_detail 抛出 not_found。"""
    seed, factory, user = seeded
    svc = _make_publication_service(factory, user)
    with pytest.raises(AppError) as exc_info:
        await svc.get_result_detail(new_id())
    assert exc_info.value.code == "not_found"

    await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_get_version_detail(seeded) -> None:
    """get_version_detail 返回指定版本详情。"""
    result_id, seed, factory, user, svc = await _publish_one(seeded)
    detail = await svc.get_version_detail(result_id, 1)
    assert detail.version_number == 1
    assert detail.title == "ACL 测试成果包"
    # publisher 解析为 app_user.display_name（_version_to_detail 查询 app_user）
    assert detail.publisher == "Test User"

    await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_get_version_detail_not_found(seeded) -> None:
    """版本不存在时 get_version_detail 抛出 not_found。"""
    result_id, seed, factory, user, svc = await _publish_one(seeded)
    with pytest.raises(AppError) as exc_info:
        await svc.get_version_detail(result_id, 999)
    assert exc_info.value.code == "not_found"

    await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_list_versions(seeded) -> None:
    """list_versions 返回版本历史列表。"""
    result_id, seed, factory, user, svc = await _publish_one(seeded)
    versions = await svc.list_versions(result_id)
    assert len(versions) == 1
    assert versions[0].version_number == 1

    await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_list_acl_revisions(seeded) -> None:
    """list_acl_revisions 返回 ACL 变更记录列表。"""
    result_id, seed, factory, user, svc = await _publish_one(seeded)
    await svc.update_acl(
        result_id=result_id,
        acl_type="tree",
        explicit_user_ids=None,
        reason="共享",
        is_declassify=False,
        declassify_reason=None,
    )
    revisions = await svc.list_acl_revisions(result_id)
    assert len(revisions) == 2
    assert revisions[0].revision_number == 1
    assert revisions[1].revision_number == 2
    assert revisions[1].acl_type == "tree"

    await _cleanup_workspace(factory, seed.workspace_id)


# ============================================================
# reuse.py
# ============================================================


@pytest.mark.integration
async def test_toggle_favorite_add_and_remove(seeded) -> None:
    """收藏后 is_favorited=True，取消后 False。"""
    result_id, seed, factory, user, svc = await _publish_one(seeded)
    await svc.toggle_favorite(result_id, is_favorite=True)
    async with factory() as session:
        assert await ResearchRepository.check_favorite(session, result_id, user.user_id)

    await svc.toggle_favorite(result_id, is_favorite=False)
    async with factory() as session:
        assert not await ResearchRepository.check_favorite(session, result_id, user.user_id)

    await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_toggle_favorite_idempotent(seeded) -> None:
    """重复收藏保持单条记录。"""
    result_id, seed, factory, user, svc = await _publish_one(seeded)
    await svc.toggle_favorite(result_id, is_favorite=True)
    await svc.toggle_favorite(result_id, is_favorite=True)  # 幂等
    async with factory() as session:
        count = await session.scalar(
            sa.text(
                "SELECT count(*) FROM research_result_favorite "
                "WHERE result_id = :rid AND user_id = :uid"
            ),
            {"rid": str(result_id), "uid": str(user.user_id)},
        )
        assert int(count) == 1

    await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_toggle_favorite_result_not_found(seeded) -> None:
    """收藏不存在的成果包抛出 not_found。"""
    seed, factory, user = seeded
    svc = _make_publication_service(factory, user)
    with pytest.raises(AppError) as exc_info:
        await svc.toggle_favorite(new_id(), is_favorite=True)
    assert exc_info.value.code == "not_found"

    await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_get_result_internal_object_dataset(seeded) -> None:
    """get_result_internal_object 返回成果包内数据集详情。"""
    result_id, seed, factory, user, svc = await _publish_one(seeded)
    detail = await svc.get_result_internal_object(result_id, "dataset", seed.dataset_id)
    assert detail["object_type"] == "dataset"
    assert detail["dataset_id"] == str(seed.dataset_id)
    assert detail["name"] == "拉伸测试数据集"

    await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_get_result_internal_object_view(seeded) -> None:
    """get_result_internal_object 返回成果包内视图详情。"""
    result_id, seed, factory, user, svc = await _publish_one(seeded)
    # 发布包含 view 的成果包
    request = PublishRequest(
        title="含视图成果包",
        view_ids=[seed.view_id],
        requested_acl="private",
    )
    ref = await svc.publish_result(seed.workspace_id, request)
    detail = await svc.get_result_internal_object(ref.result_id, "view", seed.view_id)
    assert detail["object_type"] == "view"
    assert detail["view_id"] == str(seed.view_id)

    await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_get_result_internal_object_unsupported_type(seeded) -> None:
    """不支持的对象类型抛出 validation_failed。"""
    result_id, seed, factory, user, svc = await _publish_one(seeded)
    with pytest.raises(AppError) as exc_info:
        await svc.get_result_internal_object(result_id, "unknown", seed.dataset_id)
    assert exc_info.value.code == "validation_failed"

    await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_get_result_internal_object_not_in_version(seeded) -> None:
    """对象不在成果包版本引用中时抛出 not_found。"""
    result_id, seed, factory, user, svc = await _publish_one(seeded)
    with pytest.raises(AppError) as exc_info:
        await svc.get_result_internal_object(result_id, "dataset", new_id())
    assert exc_info.value.code == "not_found"

    await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_add_to_workspace(seeded) -> None:
    """add_to_workspace 将成果包内数据集作为证据加入目标工作空间。"""
    result_id, seed, factory, user, svc = await _publish_one(seeded)
    # 新建一个目标工作空间
    target_ws = new_id()
    async with factory() as session:
        async with session.begin():
            session.add(
                ResearchWorkspace(
                    id=target_ws,
                    owner_user_id=user.user_id,
                    department_id=user.department_id,
                    name="reuse-target",
                )
            )

    try:
        ref = await svc.add_to_workspace(result_id, target_ws, seed.dataset_id)
        assert ref.source_namespace == "research:published_derived"
        assert ref.source_id == seed.dataset_id
        async with factory() as session:
            count = await session.scalar(
                sa.text(
                    "SELECT count(*) FROM research_workspace_evidence_ref WHERE workspace_id = :wid"
                ),
                {"wid": str(target_ws)},
            )
            assert int(count) == 1
    finally:
        await _cleanup_workspace(factory, target_ws)
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_add_to_workspace_dataset_not_in_result(seeded) -> None:
    """数据集不在成果包版本中时 add_to_workspace 抛出 not_found。"""
    result_id, seed, factory, user, svc = await _publish_one(seeded)
    with pytest.raises(AppError) as exc_info:
        await svc.add_to_workspace(result_id, seed.workspace_id, new_id())
    assert exc_info.value.code == "not_found"

    await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_new_workspace_from_result(seeded) -> None:
    """new_workspace_from_result 创建新工作空间并加入全部数据集为证据。"""
    result_id, seed, factory, user, svc = await _publish_one(seeded)
    ref = await svc.new_workspace_from_result(
        result_id, workspace_name="衍生研究", question_text="继续研究"
    )
    assert ref.name == "衍生研究"
    assert ref.status == "draft"

    # 新工作空间应有 1 条证据引用
    async with factory() as session:
        count = await session.scalar(
            sa.text(
                "SELECT count(*) FROM research_workspace_evidence_ref WHERE workspace_id = :wid"
            ),
            {"wid": str(ref.workspace_id)},
        )
        assert int(count) == 1

    await _cleanup_workspace(factory, ref.workspace_id)
    await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_new_workspace_from_result_not_found(seeded) -> None:
    """成果包不存在时 new_workspace_from_result 抛出 not_found。"""
    seed, factory, user = seeded
    svc = _make_publication_service(factory, user)
    with pytest.raises(AppError) as exc_info:
        await svc.new_workspace_from_result(new_id(), "x", "y")
    assert exc_info.value.code == "not_found"

    await _cleanup_workspace(factory, seed.workspace_id)


# ============================================================
# knowledge_reference.py
# ============================================================


class _FakeLineageWriter:
    """假 LineageWriterService：记录 on_knowledge_referenced 调用。"""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def on_knowledge_referenced(self, reference_id, insight_id) -> None:
        self.calls.append((reference_id, insight_id))


class _FakeS3:
    """假 S3 客户端：记录 put/get 调用。"""

    def __init__(self) -> None:
        self.put: dict[str, bytes] = {}

    def put_object(self, key, data, content_type=None) -> None:
        self.put[key] = data

    def get_object(self, key) -> bytes:
        return self.put.get(key, b"{}")


@pytest.mark.integration
async def test_save_reference_inline_snippet(factory_and_user) -> None:
    """短文本（≤4KB）直接存 PostgreSQL，不调用 MinIO。"""
    factory, user = factory_and_user
    seed = await _seed_full_scenario(factory, user)
    try:
        lineage_writer = _FakeLineageWriter()
        s3 = _FakeS3()
        svc = KnowledgeReferenceService(
            session_factory=factory,
            department_id=user.department_id,
            actor_id=user.user_id,
            lineage_writer=lineage_writer,
            s3=s3,
        )
        search_result = KnowledgeSearchResult(
            document_id="doc-001",
            document_version="2024.03",
            title="铝合金热处理",
            section="第3章",
            page=45,
            chunk_id="doc-001_chunk_1",
            relevance_score=0.9,
            source_uri="https://example.com/doc-001",
            content_hash="c" * 64,
            snippet="铝合金退火温度通常在 350-450°C 范围内。",
        )
        ref = await svc.save_reference(
            workspace_id=seed.workspace_id,
            run_id=seed.run_id,
            step_id=None,
            search_result=search_result,
            research_question_context="研究问题",
            provider_name="mock",
        )
        assert ref.reference_id is not None
        assert ref.document_id == "doc-001"
        assert ref.content_hash  # 非空
        assert len(lineage_writer.calls) == 1
        assert len(s3.put) == 0  # 短文本不写 MinIO

        # 数据库中应有该引用记录
        async with factory() as session:
            db_ref = await ResearchRepository.get_knowledge_reference(session, ref.reference_id)
            assert db_ref is not None
            assert db_ref.snippet_text == search_result.snippet
            assert db_ref.snippet_storage_path is None
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_save_reference_long_snippet_to_minio(factory_and_user) -> None:
    """长文本（>4KB）存 MinIO 并回填 storage_path。"""
    factory, user = factory_and_user
    seed = await _seed_full_scenario(factory, user)
    try:
        s3 = _FakeS3()
        svc = KnowledgeReferenceService(
            session_factory=factory,
            department_id=user.department_id,
            actor_id=user.user_id,
            lineage_writer=_FakeLineageWriter(),
            s3=s3,
        )
        long_snippet = "x" * (SNIPPET_INLINE_THRESHOLD + 100)
        search_result = KnowledgeSearchResult(
            document_id="doc-002",
            document_version="2024.01",
            title="长文档",
            snippet=long_snippet,
            content_hash="d" * 64,
            source_uri="https://example.com/doc-002",
        )
        ref = await svc.save_reference(
            workspace_id=seed.workspace_id,
            run_id=seed.run_id,
            step_id=None,
            search_result=search_result,
        )
        assert ref.reference_id is not None
        assert len(s3.put) == 1  # 写入 MinIO

        async with factory() as session:
            db_ref = await ResearchRepository.get_knowledge_reference(session, ref.reference_id)
            assert db_ref is not None
            assert db_ref.snippet_text is None  # 长文本 inline 为空
            assert db_ref.snippet_storage_path is not None
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_save_reference_truncates_oversized_snippet(factory_and_user) -> None:
    """超过 64KB 的文本被截断并标注。"""
    factory, user = factory_and_user
    seed = await _seed_full_scenario(factory, user)
    try:
        svc = KnowledgeReferenceService(
            session_factory=factory,
            department_id=user.department_id,
            actor_id=user.user_id,
            lineage_writer=_FakeLineageWriter(),
            s3=_FakeS3(),
        )
        oversized = "y" * (SNIPPET_MAX_SIZE + 1000)
        truncated = svc._truncate_snippet(oversized)
        assert TRUNCATION_SUFFIX in truncated
        assert len(truncated.encode("utf-8")) <= SNIPPET_MAX_SIZE
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_list_references_by_run(factory_and_user) -> None:
    """list_references_by_run 返回指定 Run 的引用列表。"""
    factory, user = factory_and_user
    seed = await _seed_full_scenario(factory, user)
    try:
        svc = KnowledgeReferenceService(
            session_factory=factory,
            department_id=user.department_id,
            actor_id=user.user_id,
            lineage_writer=_FakeLineageWriter(),
            s3=_FakeS3(),
        )
        for i in range(3):
            await svc.save_reference(
                workspace_id=seed.workspace_id,
                run_id=seed.run_id,
                step_id=None,
                search_result=KnowledgeSearchResult(
                    document_id=f"doc-{i}",
                    document_version="1",
                    title=f"文档 {i}",
                    snippet="短文本",
                    content_hash=f"h{i}",
                    source_uri="u",
                ),
            )
        refs = await svc.list_references_by_run(seed.run_id)
        assert len(refs) == 3
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_list_references_by_insight(factory_and_user) -> None:
    """list_references_by_insight 返回关联到 Insight 的引用列表。"""
    factory, user = factory_and_user
    seed = await _seed_full_scenario(factory, user)
    try:
        svc = KnowledgeReferenceService(
            session_factory=factory,
            department_id=user.department_id,
            actor_id=user.user_id,
            lineage_writer=_FakeLineageWriter(),
            s3=_FakeS3(),
        )
        ref = await svc.save_reference(
            workspace_id=seed.workspace_id,
            run_id=seed.run_id,
            step_id=None,
            search_result=KnowledgeSearchResult(
                document_id="doc-x",
                document_version="1",
                title="文档",
                snippet="文本",
                content_hash="hx",
                source_uri="u",
            ),
        )
        # 手动关联到 insight
        async with factory() as session:
            async with session.begin():
                await session.execute(
                    sa.text(
                        "UPDATE research_knowledge_reference SET insight_id = :iid WHERE id = :rid"
                    ),
                    {"iid": str(seed.insight_id), "rid": str(ref.reference_id)},
                )
        details = await svc.list_references_by_insight(seed.insight_id, include_full_content=True)
        assert len(details) == 1
        assert details[0].ref.insight_id == seed.insight_id
        assert details[0].snippet_text == "文本"
    finally:
        await _cleanup_workspace(factory, seed.workspace_id)


@pytest.mark.integration
async def test_get_reference_not_found(factory_and_user) -> None:
    """get_reference 对不存在的 ID 返回 None。"""
    factory, user = factory_and_user
    svc = KnowledgeReferenceService(
        session_factory=factory,
        department_id=user.department_id,
        actor_id=user.user_id,
        lineage_writer=_FakeLineageWriter(),
        s3=_FakeS3(),
    )
    result = await svc.get_reference(new_id())
    assert result is None


# ============================================================
# knowledge_provider.py
# ============================================================


@pytest.mark.integration
async def test_mock_provider_search_matches_keyword() -> None:
    """MockKnowledgeProvider.search 按关键词匹配返回结果。"""
    provider = MockKnowledgeProvider()
    results = await provider.search("铝合金")
    assert len(results) > 0
    assert all(r.document_id.startswith("mock_doc") for r in results)


@pytest.mark.integration
async def test_mock_provider_search_no_match_returns_all() -> None:
    """无匹配时返回全部 Mock 文档。"""
    provider = MockKnowledgeProvider()
    results = await provider.search("zzz不存在的关键词")
    assert len(results) == 3


@pytest.mark.integration
async def test_mock_provider_get_document() -> None:
    """MockKnowledgeProvider.get_document 返回文档元数据。"""
    provider = MockKnowledgeProvider()
    doc = await provider.get_document("mock_doc_001")
    assert doc is not None
    assert doc.document_id == "mock_doc_001"
    assert await provider.get_document("nonexistent") is None


@pytest.mark.integration
async def test_mock_provider_health_check() -> None:
    """MockKnowledgeProvider.health_check 始终返回 True。"""
    provider = MockKnowledgeProvider()
    assert await provider.health_check() is True


@pytest.mark.integration
async def test_provider_service_search_all_merges_and_dedup(async_session_factory) -> None:
    """KnowledgeProviderService.search_all 合并多 Provider 结果并去重。"""
    p1 = MockKnowledgeProvider("p1")
    p2 = MockKnowledgeProvider("p2")
    svc = KnowledgeProviderService(async_session_factory, {"p1": p1, "p2": p2})
    results = await svc.search_all("铝合金")
    # 两个 provider 返回相同 content_hash 的结果，去重后应等于单个 provider 的数量
    single = await p1.search("铝合金")
    assert len(results) == len(single)
    # 按 relevance_score 降序
    scores = [r.relevance_score for r in results]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.integration
async def test_provider_service_search_specific_providers(async_session_factory) -> None:
    """search 指定 provider_names 只查询这些 provider。"""
    p1 = MockKnowledgeProvider("p1")
    p2 = MockKnowledgeProvider("p2")
    svc = KnowledgeProviderService(async_session_factory, {"p1": p1, "p2": p2})
    results = await svc.search("铝合金", provider_names=["p1"])
    assert len(results) > 0


@pytest.mark.integration
async def test_provider_service_search_no_providers_returns_empty(async_session_factory) -> None:
    """无 provider 时返回空列表。"""
    svc = KnowledgeProviderService(async_session_factory, {})
    assert await svc.search("x") == []


@pytest.mark.integration
async def test_provider_service_merge_deduplicate_directly(async_session_factory) -> None:
    """直接测试 _merge_and_deduplicate：按 content_hash 去重，保留最高分。"""
    svc = KnowledgeProviderService(async_session_factory, {})
    r_high = KnowledgeSearchResult(
        document_id="d1",
        document_version="1",
        title="t",
        content_hash="same",
        relevance_score=0.9,
        snippet="s",
    )
    r_low = KnowledgeSearchResult(
        document_id="d2",
        document_version="1",
        title="t",
        content_hash="same",
        relevance_score=0.5,
        snippet="s2",
    )
    r_other = KnowledgeSearchResult(
        document_id="d3",
        document_version="1",
        title="t",
        content_hash="other",
        relevance_score=0.7,
        snippet="s3",
    )
    merged = svc._merge_and_deduplicate([[r_high, r_low, r_other]])
    assert len(merged) == 2
    # same hash 保留高分
    same = [m for m in merged if m.content_hash == "same"][0]
    assert same.relevance_score == 0.9
    # 降序
    assert merged[0].relevance_score >= merged[1].relevance_score


@pytest.mark.integration
async def test_provider_service_dedup_fallback_key(async_session_factory) -> None:
    """content_hash 为空时按 document_id:chunk_id 去重。"""
    svc = KnowledgeProviderService(async_session_factory, {})
    r1 = KnowledgeSearchResult(
        document_id="d1",
        document_version="1",
        title="t",
        chunk_id="c1",
        content_hash="",
        relevance_score=0.8,
        snippet="s",
    )
    r2 = KnowledgeSearchResult(
        document_id="d1",
        document_version="1",
        title="t",
        chunk_id="c1",
        content_hash="",
        relevance_score=0.6,
        snippet="s",
    )
    merged = svc._merge_and_deduplicate([[r1, r2]])
    assert len(merged) == 1


@pytest.mark.integration
async def test_provider_service_handles_failing_provider(async_session_factory) -> None:
    """Provider 抛异常时降级处理，不影响其他 Provider 结果。"""

    class _BoomProvider:
        async def search(self, query, options=None):
            raise RuntimeError("boom")

        async def get_document(self, document_id):
            return None

        async def health_check(self):
            return False

    svc = KnowledgeProviderService(
        async_session_factory, {"boom": _BoomProvider(), "mock": MockKnowledgeProvider()}
    )
    results = await svc.search_all("铝合金")
    assert len(results) > 0  # mock provider 的结果仍返回


@pytest.mark.integration
async def test_provider_service_handles_timeout(async_session_factory) -> None:
    """Provider 超时（asyncio.wait_for）时降级处理。"""
    import asyncio

    class _SlowProvider:
        async def search(self, query, options=None):
            await asyncio.sleep(10)
            return []

        async def get_document(self, document_id):
            return None

        async def health_check(self):
            return True

    svc = KnowledgeProviderService(
        async_session_factory, {"slow": _SlowProvider(), "mock": MockKnowledgeProvider()}
    )
    opts = KnowledgeSearchOptions(timeout=1)  # 1 秒超时
    results = await svc.search_all("铝合金", options=opts)
    assert len(results) > 0  # mock provider 的结果仍返回
