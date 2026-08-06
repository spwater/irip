"""阶段 4：发布与复用 — 核心测试。

覆盖：
- PermissionEnvelopeCalculator: 权限包络计算与 ACL 校验
- PublicationService: 成果包发布 / 新版本 / 内容哈希 / 可见性
- ResultSearchService: 搜索 / 视图模式 / ACL 过滤

纯逻辑测试（不需要数据库），DB 相关部分使用 Mock 对象。

参照架构设计 arch-research-publish.md 3.3 节。
"""

import hashlib
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from packages.research.envelope import PermissionEnvelopeCalculator
from packages.research.models import (
    EnvelopeValidationResult,
    PermissionEnvelope,
    ProductRefCollection,
    PublishRequest,
    ResultVersionRef,
)
from packages.research.publication import PublicationService
from packages.research.search import ResultSearchService


# ============================================================
# Helpers
# ============================================================


def _make_snapshot_envelope(status: str = "active") -> dict:
    """构造 permission_envelope 字典（模拟 Evidence Snapshot 的格式）。"""
    fact_id = str(uuid4())
    return {
        fact_id: {
            "fact_type": "experiment_run",
            "status": status,
            "department_name": "test_dept",
        }
    }


class FakeSnapshotRow:
    """模拟 sa.Row，返回 permission_envelope。"""

    def __init__(self, envelope: dict | None):
        self._envelope = envelope

    def __getitem__(self, idx: int):
        return self._envelope

    def first(self):
        return self if self._envelope is not None else None


class FakeResult:
    """模拟 ResearchResult ORM 实体。"""

    def __init__(
        self,
        result_id: UUID,
        owner_user_id: UUID,
        current_acl_type: str = "private",
        current_explicit_user_ids: list | None = None,
        status: str = "published",
        current_version: int = 1,
        name: str = "test_result",
        workspace_id: UUID | None = None,
    ):
        self.id = result_id
        self.owner_user_id = owner_user_id
        self.current_acl_type = current_acl_type
        self.current_explicit_user_ids = current_explicit_user_ids or []
        self.status = status
        self.current_version = current_version
        self.name = name
        self.workspace_id = workspace_id or uuid4()


class FakeVersion:
    """模拟 ResearchResultVersion ORM 实体。"""

    def __init__(
        self,
        result_id: UUID,
        version_number: int = 1,
        title: str = "Test Result",
        summary: str = "",
        tags: list | None = None,
        status: str = "active",
        dataset_version_refs: list | None = None,
        view_version_refs: list | None = None,
        insight_version_refs: list | None = None,
        content_hash: str = "abc123",
        published_at: datetime | None = None,
    ):
        self.id = uuid4()
        self.result_id = result_id
        self.version_number = version_number
        self.title = title
        self.summary = summary
        self.tags = tags or []
        self.status = status
        self.dataset_version_refs = dataset_version_refs or []
        self.view_version_refs = view_version_refs or []
        self.insight_version_refs = insight_version_refs or []
        self.evidence_snapshot_ids = []
        self.analysis_run_ids = []
        self.source_run_statuses = {}
        self.content_hash = content_hash
        self.published_at = published_at or datetime.now(UTC)
        self.publisher = uuid4()
        self.release_notes = ""
        self.published_permission_envelope = {}


# ============================================================
# 1. PermissionEnvelopeCalculator
# ============================================================


class TestPermissionEnvelopeCalculator:
    """权限包络计算器测试。"""

    @pytest.mark.asyncio
    async def test_calculate_envelope_no_snapshots(self):
        """无源数据时，包络应为 all（不限制）。"""
        envelope = await PermissionEnvelopeCalculator.calculate_envelope(
            [], session=AsyncMock()
        )
        assert envelope.acl_type == "all"
        assert envelope.source_details == []

    @pytest.mark.asyncio
    async def test_calculate_envelope_single_snapshot_active(self):
        """单个 snapshot（源数据状态 active）应返回 tree。"""
        snap_id = uuid4()
        env_data = _make_snapshot_envelope(status="active")

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.first.return_value = FakeSnapshotRow(env_data)
        mock_session.execute = AsyncMock(return_value=mock_result)

        envelope = await PermissionEnvelopeCalculator.calculate_envelope(
            [snap_id], session=mock_session
        )

        assert envelope.acl_type == "tree"
        assert len(envelope.source_details) == 1
        assert envelope.source_details[0]["snapshot_id"] == str(snap_id)

    @pytest.mark.asyncio
    async def test_calculate_envelope_single_snapshot_inactive(self):
        """单个 snapshot（源数据状态非 active）应返回 private（收紧）。"""
        snap_id = uuid4()
        env_data = _make_snapshot_envelope(status="archived")

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.first.return_value = FakeSnapshotRow(env_data)
        mock_session.execute = AsyncMock(return_value=mock_result)

        envelope = await PermissionEnvelopeCalculator.calculate_envelope(
            [snap_id], session=mock_session
        )

        assert envelope.acl_type == "private"

    @pytest.mark.asyncio
    async def test_calculate_envelope_multiple_snapshots_intersection(self):
        """多个 snapshot 交集：一个 private + 一个 tree → private（最严格）。"""
        snap_id_1 = uuid4()
        snap_id_2 = uuid4()
        env_active = _make_snapshot_envelope(status="active")
        env_archived = _make_snapshot_envelope(status="archived")

        call_count = [0]

        async def mock_execute(stmt):
            call_count[0] += 1
            mock_res = MagicMock()
            if call_count[0] == 1:
                mock_res.first.return_value = FakeSnapshotRow(env_active)
            else:
                mock_res.first.return_value = FakeSnapshotRow(env_archived)
            return mock_res

        mock_session = AsyncMock()
        mock_session.execute = mock_execute

        envelope = await PermissionEnvelopeCalculator.calculate_envelope(
            [snap_id_1, snap_id_2], session=mock_session
        )

        # 交集 = min(tree, private) = private
        assert envelope.acl_type == "private"
        assert len(envelope.source_details) == 2

    @pytest.mark.asyncio
    async def test_calculate_envelope_snapshot_not_found(self):
        """snapshot 不存在时，保守处理为 private。"""
        snap_id = uuid4()

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.first.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        envelope = await PermissionEnvelopeCalculator.calculate_envelope(
            [snap_id], session=mock_session
        )

        assert envelope.acl_type == "private"
        assert envelope.source_details[0]["reason"] == "snapshot_not_found"

    @pytest.mark.asyncio
    async def test_calculate_envelope_empty_envelope(self):
        """snapshot 存在但 permission_envelope 为空时，视为不限制（all）。"""
        snap_id = uuid4()

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.first.return_value = FakeSnapshotRow({})
        mock_session.execute = AsyncMock(return_value=mock_result)

        envelope = await PermissionEnvelopeCalculator.calculate_envelope(
            [snap_id], session=mock_session
        )

        assert envelope.acl_type == "all"

    def test_validate_requested_acl_within_envelope(self):
        """ACL 在包络内时，校验通过。"""
        envelope = PermissionEnvelope(
            acl_type="tree",
            source_details=[
                {"snapshot_id": "x", "acl_type": "tree"},
                {"snapshot_id": "y", "acl_type": "all"},
            ],
        )
        result = PermissionEnvelopeCalculator.validate_requested_acl(
            "private", [], envelope
        )
        assert result.valid is True
        assert result.effective_acl == "private"

    def test_validate_requested_acl_exceeds_envelope(self):
        """ACL 超出包络时，校验不通过。"""
        envelope = PermissionEnvelope(
            acl_type="private",
            source_details=[
                {"snapshot_id": "x", "acl_type": "private"},
            ],
        )
        result = PermissionEnvelopeCalculator.validate_requested_acl(
            "tree", [], envelope
        )
        assert result.valid is False
        assert result.effective_acl == "private"
        assert "exceeds" in result.reason.lower()

    def test_validate_requested_acl_all_in_all_envelope(self):
        """请求 all 且包络为 all 时，校验通过。"""
        envelope = PermissionEnvelope(acl_type="all")
        result = PermissionEnvelopeCalculator.validate_requested_acl(
            "all", [], envelope
        )
        assert result.valid is True

    def test_validate_requested_acl_all_exceeds_tree_envelope(self):
        """请求 all 但包络为 tree 时，校验不通过。"""
        envelope = PermissionEnvelope(acl_type="tree")
        result = PermissionEnvelopeCalculator.validate_requested_acl(
            "all", [], envelope
        )
        assert result.valid is False

    def test_acl_rank_ordering(self):
        """ACL 严格度排序：private < explicit < tree < all。"""
        ranks = PermissionEnvelopeCalculator._ACL_RANKS
        assert ranks["private"] < ranks["explicit"]
        assert ranks["explicit"] < ranks["tree"]
        assert ranks["tree"] < ranks["all"]

    def test_acl_rank_unknown_type(self):
        """未知 ACL 类型返回 0（最严格）。"""
        assert PermissionEnvelopeCalculator._acl_rank("unknown") == 0

    def test_intersect_acl_types_empty(self):
        """空列表交集返回 all（默认不限制）。"""
        result = PermissionEnvelopeCalculator._intersect_acl_types([])
        assert result == "all"

    def test_intersect_acl_types_single(self):
        """单个 ACL 类型的交集为自身。"""
        result = PermissionEnvelopeCalculator._intersect_acl_types(["tree"])
        assert result == "tree"

    def test_intersect_acl_types_mixed(self):
        """混合 ACL 类型的交集为最严格的。"""
        result = PermissionEnvelopeCalculator._intersect_acl_types(
            ["all", "private", "tree"]
        )
        assert result == "private"

    def test_extract_acl_from_envelope_empty(self):
        """空 envelope 返回 all（不限制）。"""
        assert PermissionEnvelopeCalculator._extract_acl_from_envelope({}) == "all"

    def test_extract_acl_from_envelope_all_active(self):
        """全部源数据状态 active 时返回 tree。"""
        env = {"fact1": {"status": "active"}, "fact2": {"status": "active"}}
        assert PermissionEnvelopeCalculator._extract_acl_from_envelope(env) == "tree"

    def test_extract_acl_from_envelope_non_active(self):
        """存在非 active 状态时返回 private。"""
        env = {"fact1": {"status": "active"}, "fact2": {"status": "archived"}}
        assert PermissionEnvelopeCalculator._extract_acl_from_envelope(env) == "private"


# ============================================================
# 2. PublicationService — 内容哈希与可见性
# ============================================================


class TestPublicationServiceContentHash:
    """PublicationService 内容哈希计算测试。"""

    def _make_service(self) -> PublicationService:
        """创建带 Mock 依赖的 PublicationService 实例。"""
        mock_factory = MagicMock()
        mock_product_service = MagicMock()
        mock_lineage_service = MagicMock()
        return PublicationService(
            session_factory=mock_factory,
            department_id=uuid4(),
            actor_id=uuid4(),
            product_service=mock_product_service,
            lineage_service=mock_lineage_service,
        )

    def test_content_hash_deterministic(self):
        """相同输入应产生相同的内容哈希。"""
        svc = self._make_service()
        request = PublishRequest(
            title="Test Result",
            summary="A summary",
            tags=["alpha", "beta"],
            release_notes="v1",
        )
        refs = ProductRefCollection(
            dataset_version_refs=[
                {"dataset_id": str(uuid4()), "version_number": 1, "content_hash": "hash1"},
            ],
            view_version_refs=[
                {"view_id": str(uuid4()), "version_number": 1, "image_content_hash": "hash2"},
            ],
            insight_version_refs=[
                {"insight_id": str(uuid4()), "version_number": 1},
            ],
        )

        hash1 = svc._compute_content_hash(request, refs)
        hash2 = svc._compute_content_hash(request, refs)

        assert hash1 == hash2
        assert len(hash1) == 64  # SHA-256 十六进制长度

    def test_content_hash_differs_on_title_change(self):
        """标题不同应产生不同的哈希。"""
        svc = self._make_service()
        refs = ProductRefCollection()
        h1 = svc._compute_content_hash(
            PublishRequest(title="Title A"), refs
        )
        h2 = svc._compute_content_hash(
            PublishRequest(title="Title B"), refs
        )
        assert h1 != h2

    def test_content_hash_differs_on_refs_change(self):
        """产物引用不同应产生不同的哈希。"""
        svc = self._make_service()
        request = PublishRequest(title="Test")

        refs1 = ProductRefCollection(
            dataset_version_refs=[
                {"dataset_id": str(uuid4()), "version_number": 1, "content_hash": "h1"},
            ],
        )
        refs2 = ProductRefCollection(
            dataset_version_refs=[
                {"dataset_id": str(uuid4()), "version_number": 1, "content_hash": "h2"},
            ],
        )

        h1 = svc._compute_content_hash(request, refs1)
        h2 = svc._compute_content_hash(request, refs2)
        assert h1 != h2

    def test_content_hash_is_sha256_hex(self):
        """内容哈希应为合法的 SHA-256 十六进制字符串。"""
        svc = self._make_service()
        request = PublishRequest(title="Test", tags=["x"])
        refs = ProductRefCollection()
        h = svc._compute_content_hash(request, refs)

        # 验证是有效的十六进制字符串
        int(h, 16)
        assert len(h) == 64

    def test_content_hash_tags_order_independent(self):
        """标签顺序不影响哈希（内部排序）。"""
        svc = self._make_service()
        refs = ProductRefCollection()

        h1 = svc._compute_content_hash(
            PublishRequest(title="T", tags=["a", "b", "c"]), refs
        )
        h2 = svc._compute_content_hash(
            PublishRequest(title="T", tags=["c", "b", "a"]), refs
        )
        assert h1 == h2


class TestPublicationServiceVisibility:
    """PublicationService ACL 可见性校验测试。"""

    def _make_service(self) -> PublicationService:
        mock_factory = MagicMock()
        return PublicationService(
            session_factory=mock_factory,
            department_id=uuid4(),
            actor_id=uuid4(),
            product_service=MagicMock(),
            lineage_service=MagicMock(),
        )

    def test_private_visible_to_owner(self):
        """private ACL: owner 可见。"""
        svc = self._make_service()
        owner = uuid4()
        result = FakeResult(uuid4(), owner, current_acl_type="private")
        assert svc._check_result_visible(result, owner) is True

    def test_private_not_visible_to_others(self):
        """private ACL: 非 owner 不可见。"""
        svc = self._make_service()
        owner = uuid4()
        other = uuid4()
        result = FakeResult(uuid4(), owner, current_acl_type="private")
        assert svc._check_result_visible(result, other) is False

    def test_tree_visible_to_all(self):
        """tree ACL: 同部门用户可见。"""
        svc = self._make_service()
        result = FakeResult(uuid4(), uuid4(), current_acl_type="tree")
        assert svc._check_result_visible(result, uuid4()) is True

    def test_explicit_visible_to_listed_user(self):
        """explicit ACL: 指定用户可见。"""
        svc = self._make_service()
        owner = uuid4()
        listed = uuid4()
        result = FakeResult(
            uuid4(),
            owner,
            current_acl_type="explicit",
            current_explicit_user_ids=[str(listed)],
        )
        assert svc._check_result_visible(result, listed) is True

    def test_explicit_visible_to_owner(self):
        """explicit ACL: owner 始终可见。"""
        svc = self._make_service()
        owner = uuid4()
        result = FakeResult(
            uuid4(),
            owner,
            current_acl_type="explicit",
            current_explicit_user_ids=[],
        )
        assert svc._check_result_visible(result, owner) is True

    def test_explicit_not_visible_to_unlisted(self):
        """explicit ACL: 未指定用户不可见。"""
        svc = self._make_service()
        owner = uuid4()
        unlisted = uuid4()
        result = FakeResult(
            uuid4(),
            owner,
            current_acl_type="explicit",
            current_explicit_user_ids=[],
        )
        assert svc._check_result_visible(result, unlisted) is False

    def test_all_visible_to_everyone(self):
        """all ACL: 全部可见。"""
        svc = self._make_service()
        result = FakeResult(uuid4(), uuid4(), current_acl_type="all")
        assert svc._check_result_visible(result, uuid4()) is True

    def test_unknown_acl_not_visible(self):
        """未知 ACL 类型：保守为不可见。"""
        svc = self._make_service()
        result = FakeResult(uuid4(), uuid4(), current_acl_type="unknown_type")
        assert svc._check_result_visible(result, uuid4()) is False

    def test_require_actor_raises_when_none(self):
        """actor_id 为 None 时抛出 AppError。"""
        from packages.common.errors import AppError

        svc = PublicationService(
            session_factory=MagicMock(),
            department_id=uuid4(),
            actor_id=None,
            product_service=MagicMock(),
            lineage_service=MagicMock(),
        )
        with pytest.raises(AppError) as exc_info:
            svc._require_actor()
        assert exc_info.value.code == "forbidden"


# ============================================================
# 3. ResultSearchService — 搜索与 ACL 过滤
# ============================================================


class TestResultSearchService:
    """成果包搜索服务测试。"""

    def _make_service(self, actor_id: UUID | None = None) -> ResultSearchService:
        return ResultSearchService(
            session_factory=MagicMock(),
            department_id=uuid4(),
            actor_id=actor_id or uuid4(),
        )

    def test_check_result_visible_private_owner(self):
        """搜索 ACL 过滤：private 仅 owner 可见。"""
        svc = self._make_service()
        actor = svc._actor_id
        result = FakeResult(uuid4(), actor, current_acl_type="private")
        assert svc._check_result_visible(result, actor) is True

    def test_check_result_visible_private_not_owner(self):
        """搜索 ACL 过滤：private 非 owner 不可见。"""
        svc = self._make_service()
        result = FakeResult(uuid4(), uuid4(), current_acl_type="private")
        assert svc._check_result_visible(result, svc._actor_id) is False

    def test_check_result_visible_all(self):
        """搜索 ACL 过滤：all 对所有人可见。"""
        svc = self._make_service()
        result = FakeResult(uuid4(), uuid4(), current_acl_type="all")
        assert svc._check_result_visible(result, svc._actor_id) is True

    def test_match_query_title(self):
        """关键词匹配标题。"""
        svc = self._make_service()
        version = FakeVersion(uuid4(), title="Machine Learning Results")
        assert svc._match_query(version, "machine") is True
        assert svc._match_query(version, "Machine") is True

    def test_match_query_summary(self):
        """关键词匹配摘要。"""
        svc = self._make_service()
        version = FakeVersion(uuid4(), title="X", summary="Deep learning analysis")
        assert svc._match_query(version, "deep") is True

    def test_match_query_tags(self):
        """关键词匹配标签。"""
        svc = self._make_service()
        version = FakeVersion(uuid4(), title="X", tags=["physics", "quantum"])
        assert svc._match_query(version, "quantum") is True

    def test_match_query_no_match(self):
        """无匹配时返回 False。"""
        svc = self._make_service()
        version = FakeVersion(uuid4(), title="Alpha", summary="Beta", tags=["gamma"])
        assert svc._match_query(version, "delta") is False

    def test_match_query_empty_query(self):
        """空关键词匹配所有（空字符串是子串）。"""
        svc = self._make_service()
        version = FakeVersion(uuid4(), title="Anything")
        assert svc._match_query(version, "") is True

    def test_match_query_case_insensitive(self):
        """关键词匹配不区分大小写。"""
        svc = self._make_service()
        version = FakeVersion(uuid4(), title="UPPER CASE Title")
        assert svc._match_query(version, "upper case") is True
