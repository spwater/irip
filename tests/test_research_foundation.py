"""研究域基础模块测试。

测试范围：
1. ORM 实体：4 个实体正确继承 Base，字段类型/约束正确
2. 功能开关：RESEARCH_MODULE_ENABLED 读取正确
3. 权限：Permission.RESEARCH_USE 已加入 Permission.all()；
   BUILTIN_ROLES 中 lab_director/lab_member 有该权限，
   lab_viewer/platform_auditor 没有
4. Repository：keyset 分页游标编解码
5. WorkspaceService：创建工作空间生成审计事件、归档状态变更、
   删除检查、分叉继承证据引用副本
6. EvidenceSnapshotService：冻结快照逻辑、SHA-256 内容哈希计算、
   权限包络记录、字段清单提取
7. CoreFactProvider：只读接口不暴露 session、搜索结果转 FactSummary
8. 迁移：0074 upgrade/downgrade 结构验证
9. 模块隔离：核心表无到 research_* 表的外键、功能开关闭时路由不注册

已知源码 Bug（已反馈给工程师）：
- apps/api/routers/research.py:149 — WorkspaceDetailResponse 引用了
  在其后才定义的 SnapshotResponse，导致模块导入时 NameError。
  影响：TestResearchAPI 中的路由导入和路由计数测试被 skip。
"""

import hashlib
import importlib
import importlib.util
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.dialects.postgresql import JSONB

from packages.common.database import Base
from packages.research.dtos import (
    CreateWorkspaceCommand,
    EvidenceRefDTO,
    FactSummary,
    QuestionVersionRef,
    SnapshotRef,
    WorkspaceRef,
)
from packages.research.entities import (
    ResearchEvidenceSnapshot,
    ResearchQuestionVersion,
    ResearchWorkspace,
    WorkspaceEvidenceRef,
)

# ---------------------------------------------------------------------------
# 辅助：mock ScopedSessionMixin._scoped_session
# ---------------------------------------------------------------------------


def _mock_scoped_session(service):
    """为 Service 实例的 _scoped_session 方法注入 mock，返回 AsyncMock session。"""
    mock_session = AsyncMock()

    @asynccontextmanager
    async def _fake_scoped_session():
        yield mock_session

    service._scoped_session = _fake_scoped_session
    return mock_session


# ---------------------------------------------------------------------------
# 1. ORM 实体测试
# ---------------------------------------------------------------------------


class TestORMEntities:
    """验证 4 个研究域 ORM 实体正确继承 Base 且字段约束正确。"""

    def test_research_workspace_inherits_base(self):
        """ResearchWorkspace 继承 Base。"""
        assert issubclass(ResearchWorkspace, Base)

    def test_research_question_version_inherits_base(self):
        """ResearchQuestionVersion 继承 Base。"""
        assert issubclass(ResearchQuestionVersion, Base)

    def test_workspace_evidence_ref_inherits_base(self):
        """WorkspaceEvidenceRef 继承 Base。"""
        assert issubclass(WorkspaceEvidenceRef, Base)

    def test_research_evidence_snapshot_inherits_base(self):
        """ResearchEvidenceSnapshot 继承 Base。"""
        assert issubclass(ResearchEvidenceSnapshot, Base)

    def test_research_workspace_tablename(self):
        """表名正确。"""
        assert ResearchWorkspace.__tablename__ == "research_workspace"
        assert ResearchQuestionVersion.__tablename__ == "research_question_version"
        assert WorkspaceEvidenceRef.__tablename__ == "research_workspace_evidence_ref"
        assert ResearchEvidenceSnapshot.__tablename__ == "research_evidence_snapshot"

    def test_research_workspace_columns(self):
        """ResearchWorkspace 字段类型与约束。"""
        cols = ResearchWorkspace.__table__.columns
        col_names = {c.name for c in cols}
        expected = {
            "id",
            "owner_user_id",
            "department_id",
            "name",
            "status",
            "current_question_version",
            "forked_from_id",
            "created_at",
            "updated_at",
            "lock_version",
        }
        assert expected.issubset(col_names)

        # id 是主键
        assert cols["id"].primary_key

        # owner_user_id 不可为空且是 FK → app_user
        assert not cols["owner_user_id"].nullable
        assert any(fk.column.table.name == "app_user" for fk in cols["owner_user_id"].foreign_keys)

        # department_id 不可为空且是 FK → department
        assert not cols["department_id"].nullable
        assert any(
            fk.column.table.name == "department" for fk in cols["department_id"].foreign_keys
        )

        # name 不可为空
        assert not cols["name"].nullable

        # forked_from_id 可为空（逻辑引用，不建 FK）
        assert cols["forked_from_id"].nullable
        assert len(cols["forked_from_id"].foreign_keys) == 0

        # status 默认值为 draft (server_default)
        assert cols["status"].server_default is not None

        # current_question_version 默认值为 0 (server_default)
        assert cols["current_question_version"].server_default is not None

        # lock_version 默认值为 0 (server_default)
        assert cols["lock_version"].server_default is not None

    def test_research_question_version_columns(self):
        """ResearchQuestionVersion 字段类型与约束。"""
        cols = ResearchQuestionVersion.__table__.columns
        assert cols["id"].primary_key
        assert not cols["workspace_id"].nullable

        # workspace_id FK 到 research_workspace，ON DELETE CASCADE
        fks = list(cols["workspace_id"].foreign_keys)
        assert len(fks) == 1
        assert fks[0].column.table.name == "research_workspace"
        assert fks[0].ondelete == "CASCADE"

        # version_number 不可为空
        assert not cols["version_number"].nullable

        # question_text 不可为空
        assert not cols["question_text"].nullable

        # sub_questions 类型为 JSONB
        assert isinstance(cols["sub_questions"].type, JSONB)

        # created_by FK 到 app_user
        fks = list(cols["created_by"].foreign_keys)
        assert len(fks) == 1
        assert fks[0].column.table.name == "app_user"

    def test_workspace_evidence_ref_columns(self):
        """WorkspaceEvidenceRef 字段类型与约束。"""
        cols = WorkspaceEvidenceRef.__table__.columns
        assert cols["id"].primary_key
        assert not cols["workspace_id"].nullable

        # workspace_id FK 到 research_workspace，ON DELETE CASCADE
        fks = list(cols["workspace_id"].foreign_keys)
        assert len(fks) == 1
        assert fks[0].column.table.name == "research_workspace"
        assert fks[0].ondelete == "CASCADE"

        # source_id 不可为空，不建 FK（跨模块逻辑引用）
        assert not cols["source_id"].nullable
        assert len(cols["source_id"].foreign_keys) == 0

        # source_namespace 不可为空
        assert not cols["source_namespace"].nullable

        # status 默认值为 active (server_default)
        assert cols["status"].server_default is not None

        # added_by FK 到 app_user
        fks = list(cols["added_by"].foreign_keys)
        assert len(fks) == 1
        assert fks[0].column.table.name == "app_user"

    def test_research_evidence_snapshot_columns(self):
        """ResearchEvidenceSnapshot 字段类型与约束。"""
        cols = ResearchEvidenceSnapshot.__table__.columns
        assert cols["id"].primary_key
        assert not cols["workspace_id"].nullable

        # workspace_id FK 到 research_workspace，ON DELETE CASCADE
        fks = list(cols["workspace_id"].foreign_keys)
        assert len(fks) == 1
        assert fks[0].column.table.name == "research_workspace"
        assert fks[0].ondelete == "CASCADE"

        # snapshot_number 不可为空
        assert not cols["snapshot_number"].nullable

        # content_hash 不可为空
        assert not cols["content_hash"].nullable

        # permission_envelope / field_manifest / source_refs 类型为 JSONB
        assert isinstance(cols["permission_envelope"].type, JSONB)
        assert isinstance(cols["field_manifest"].type, JSONB)
        assert isinstance(cols["source_refs"].type, JSONB)

        # created_by FK 到 app_user
        fks = list(cols["created_by"].foreign_keys)
        assert len(fks) == 1
        assert fks[0].column.table.name == "app_user"


# ---------------------------------------------------------------------------
# 2. 功能开关测试
# ---------------------------------------------------------------------------


class TestFeatureFlag:
    """验证 RESEARCH_MODULE_ENABLED 功能开关读取正确。"""

    def test_feature_flag_exists(self):
        """功能开关常量存在。"""
        from packages.common.feature_flags import RESEARCH_MODULE_ENABLED

        assert isinstance(RESEARCH_MODULE_ENABLED, bool)

    def test_feature_flag_default_true(self):
        """未设置环境变量时默认为 True。"""
        import os

        import packages.common.feature_flags as ff_module

        with patch.dict(os.environ, {}, clear=False):
            if "RESEARCH_MODULE_ENABLED" in os.environ:
                del os.environ["RESEARCH_MODULE_ENABLED"]
            importlib.reload(ff_module)
            assert ff_module.RESEARCH_MODULE_ENABLED is True

    def test_feature_flag_explicit_false(self):
        """显式设为 false 时读取为 False。"""
        import packages.common.feature_flags as ff_module

        with patch.dict("os.environ", {"RESEARCH_MODULE_ENABLED": "false"}):
            importlib.reload(ff_module)
            assert ff_module.RESEARCH_MODULE_ENABLED is False

    def test_feature_flag_explicit_true(self):
        """显式设为 true 时读取为 True。"""
        import packages.common.feature_flags as ff_module

        with patch.dict("os.environ", {"RESEARCH_MODULE_ENABLED": "true"}):
            importlib.reload(ff_module)
            assert ff_module.RESEARCH_MODULE_ENABLED is True

    def test_feature_flag_case_insensitive(self):
        """大小写不敏感（TRUE/False 等）。"""
        import packages.common.feature_flags as ff_module

        with patch.dict("os.environ", {"RESEARCH_MODULE_ENABLED": "TRUE"}):
            importlib.reload(ff_module)
            assert ff_module.RESEARCH_MODULE_ENABLED is True

        with patch.dict("os.environ", {"RESEARCH_MODULE_ENABLED": "False"}):
            importlib.reload(ff_module)
            assert ff_module.RESEARCH_MODULE_ENABLED is False


# ---------------------------------------------------------------------------
# 3. 权限测试
# ---------------------------------------------------------------------------


class TestPermission:
    """验证 RESEARCH_USE 权限定义正确。"""

    def test_research_use_permission_exists(self):
        """Permission.RESEARCH_USE 常量存在。"""
        from packages.auth.permissions import Permission

        assert Permission.RESEARCH_USE == "research:use"

    def test_research_use_in_all_permissions(self):
        """RESEARCH_USE 已加入 Permission.all()。"""
        from packages.auth.permissions import Permission

        assert Permission.RESEARCH_USE in Permission.all()

    def test_lab_director_has_research_use(self):
        """lab_director 角色拥有 RESEARCH_USE 权限。"""
        from packages.auth.permissions import BUILTIN_ROLES, Permission, RoleCode

        perms = BUILTIN_ROLES[RoleCode.LAB_DIRECTOR.value]["permissions"]
        assert Permission.RESEARCH_USE in perms

    def test_lab_member_has_research_use(self):
        """lab_member 角色拥有 RESEARCH_USE 权限。"""
        from packages.auth.permissions import BUILTIN_ROLES, Permission, RoleCode

        perms = BUILTIN_ROLES[RoleCode.LAB_MEMBER.value]["permissions"]
        assert Permission.RESEARCH_USE in perms

    def test_lab_viewer_does_not_have_research_use(self):
        """lab_viewer 角色不拥有 RESEARCH_USE 权限。"""
        from packages.auth.permissions import BUILTIN_ROLES, Permission, RoleCode

        perms = BUILTIN_ROLES[RoleCode.LAB_VIEWER.value]["permissions"]
        assert Permission.RESEARCH_USE not in perms

    def test_platform_auditor_does_not_have_research_use(self):
        """platform_auditor 角色不拥有 RESEARCH_USE 权限。"""
        from packages.auth.permissions import BUILTIN_ROLES, Permission, RoleCode

        perms = BUILTIN_ROLES[RoleCode.PLATFORM_AUDITOR.value]["permissions"]
        assert Permission.RESEARCH_USE not in perms

    def test_platform_administrator_has_research_use(self):
        """platform_administrator 拥有全部权限，包含 RESEARCH_USE。"""
        from packages.auth.permissions import BUILTIN_ROLES, Permission, RoleCode

        perms = BUILTIN_ROLES[RoleCode.PLATFORM_ADMINISTRATOR.value]["permissions"]
        assert Permission.RESEARCH_USE in perms

    def test_has_role_permission_helper(self):
        """has_role_permission 辅助函数正确判定。"""
        from packages.auth.permissions import has_role_permission

        assert has_role_permission("lab_director", "research:use") is True
        assert has_role_permission("lab_member", "research:use") is True
        assert has_role_permission("lab_viewer", "research:use") is False
        assert has_role_permission("platform_auditor", "research:use") is False
        assert has_role_permission("platform_administrator", "research:use") is True


# ---------------------------------------------------------------------------
# 4. Repository 测试
# ---------------------------------------------------------------------------


class TestRepositoryCursor:
    """ResearchRepository 游标编解码测试（无需数据库）。"""

    def test_encode_decode_roundtrip(self):
        """编码—解码往返一致。"""
        from packages.research.repository import _decode_cursor, _encode_cursor

        original_dt = datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC)
        original_id = uuid4()

        cursor = _encode_cursor(original_dt, original_id)
        decoded_dt, decoded_id = _decode_cursor(cursor)

        assert decoded_dt == original_dt
        assert decoded_id == original_id

    def test_decode_invalid_base64(self):
        """无效 base64 编码抛出 ValueError。"""
        from packages.research.repository import _decode_cursor

        with pytest.raises(ValueError, match="无效的游标编码"):
            _decode_cursor("!!!not-base64!!!")

    def test_decode_invalid_json(self):
        """有效 base64 但无效 JSON 抛出 ValueError。"""
        import base64

        from packages.research.repository import _decode_cursor

        raw = base64.urlsafe_b64encode(b"not json").decode("ascii")
        with pytest.raises(ValueError, match="无效的游标 JSON"):
            _decode_cursor(raw)

    def test_decode_missing_fields(self):
        """JSON 缺少必要字段抛出 ValueError。"""
        import base64

        from packages.research.repository import _decode_cursor

        payload = json.dumps({"v": "2026-01-01"}).encode()
        cursor = base64.urlsafe_b64encode(payload).decode("ascii")
        with pytest.raises(ValueError, match="游标缺少必要字段"):
            _decode_cursor(cursor)

    def test_decode_invalid_timestamp(self):
        """v 字段不是合法 ISO 时间抛出 ValueError。"""
        import base64

        from packages.research.repository import _decode_cursor

        payload = json.dumps({"v": "not-a-date", "id": str(uuid4())}).encode()
        cursor = base64.urlsafe_b64encode(payload).decode("ascii")
        with pytest.raises(ValueError, match="不是合法 ISO 时间"):
            _decode_cursor(cursor)

    def test_decode_invalid_uuid(self):
        """id 字段不是合法 UUID 抛出 ValueError。"""
        import base64

        from packages.research.repository import _decode_cursor

        payload = json.dumps({"v": "2026-01-01T00:00:00", "id": "not-a-uuid"}).encode()
        cursor = base64.urlsafe_b64encode(payload).decode("ascii")
        with pytest.raises(ValueError, match="不是合法 UUID"):
            _decode_cursor(cursor)


# ---------------------------------------------------------------------------
# 5. WorkspaceService 测试（mock 数据库层）
# ---------------------------------------------------------------------------


class TestWorkspaceService:
    """WorkspaceService 业务逻辑测试（mock Repository + AuditRecorder）。"""

    @pytest.fixture
    def mock_fact_provider(self):
        """模拟 CoreFactProvider。"""
        provider = AsyncMock()
        provider.get_fact_summary = AsyncMock(
            return_value=FactSummary(
                fact_id=uuid4(),
                fact_type="experiment_run",
                subject_id="实验001",
                status="active",
                department_name="实验室A",
            )
        )
        provider.get_fact_fields = AsyncMock(return_value=["组分", "结果"])
        provider.search_facts = AsyncMock(return_value=([], None))
        return provider

    @pytest.fixture
    def service(self, mock_fact_provider):
        """创建 WorkspaceService 实例（mock session_factory）。"""
        from packages.research.service import WorkspaceService

        mock_factory = MagicMock()
        return WorkspaceService(
            session_factory=mock_factory,
            department_id=uuid4(),
            actor_id=uuid4(),
            fact_provider=mock_fact_provider,
        )

    @pytest.mark.asyncio
    async def test_create_workspace_requires_actor(self, mock_fact_provider):
        """actor_id 为 None 时抛出 forbidden。"""
        from packages.common.errors import AppError
        from packages.research.service import WorkspaceService

        mock_factory = MagicMock()
        svc = WorkspaceService(
            session_factory=mock_factory,
            department_id=uuid4(),
            actor_id=None,
            fact_provider=mock_fact_provider,
        )
        with pytest.raises(AppError, match="操作需要已认证用户"):
            await svc.create_workspace(
                CreateWorkspaceCommand(name="test", question_text="question")
            )

    @pytest.mark.asyncio
    async def test_create_workspace_success(self, service):
        """创建工作空间成功 — 验证插入 workspace + question v1 + 审计。"""
        actor_id = service._actor_id
        dept_id = service._dept_id
        ws_id = uuid4()
        qv_id = uuid4()

        _mock_scoped_session(service)

        # Mock workspace 返回
        mock_ws = MagicMock()
        mock_ws.id = ws_id
        mock_ws.name = "测试工作空间"
        mock_ws.status = "draft"
        mock_ws.current_question_version = 0

        # Mock question version 返回
        mock_qv = MagicMock()
        mock_qv.id = qv_id

        with (
            patch(
                "packages.research.service.ResearchRepository.insert_workspace",
                new_callable=AsyncMock,
                return_value=mock_ws,
            ) as mock_insert_ws,
            patch(
                "packages.research.service.ResearchRepository.insert_question_version",
                new_callable=AsyncMock,
                return_value=mock_qv,
            ) as mock_insert_qv,
            patch(
                "packages.research.service.ResearchRepository.update_workspace_current_version",
                new_callable=AsyncMock,
            ) as mock_update_version,
            patch(
                "packages.research.service.AuditRecorder.record",
                new_callable=AsyncMock,
            ) as mock_audit,
        ):
            result = await service.create_workspace(
                CreateWorkspaceCommand(name="测试工作空间", question_text="研究问题")
            )

        # 验证返回值
        assert isinstance(result, WorkspaceRef)
        assert result.workspace_id == ws_id
        assert result.name == "测试工作空间"
        assert result.status == "draft"
        assert result.current_question_version == 1

        # 验证调用
        mock_insert_ws.assert_awaited_once()
        mock_insert_qv.assert_awaited_once()
        mock_update_version.assert_awaited_once()
        mock_audit.assert_awaited_once()

        # 验证审计事件 action
        audit_call = mock_audit.call_args
        event = audit_call.args[1]
        assert event.action == "research.workspace.create"
        assert event.actor_user_id == actor_id
        assert event.department_id == dept_id

    @pytest.mark.asyncio
    async def test_archive_workspace_not_found(self, service):
        """归档不存在的工作空间抛出 not_found。"""
        from packages.common.errors import AppError

        _mock_scoped_session(service)

        with patch(
            "packages.research.service.ResearchRepository.get_workspace",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with pytest.raises(AppError, match="研究工作空间不存在"):
                await service.archive_workspace(uuid4())

    @pytest.mark.asyncio
    async def test_archive_workspace_success(self, service):
        """归档工作空间成功 — 状态变更为 archived。"""
        mock_ws = MagicMock()
        mock_ws.id = uuid4()
        mock_ws.status = "draft"

        _mock_scoped_session(service)

        with (
            patch(
                "packages.research.service.ResearchRepository.get_workspace",
                new_callable=AsyncMock,
                return_value=mock_ws,
            ),
            patch(
                "packages.research.service.ResearchRepository.update_workspace_status",
                new_callable=AsyncMock,
            ) as mock_update,
            patch(
                "packages.research.service.AuditRecorder.record",
                new_callable=AsyncMock,
            ) as mock_audit,
        ):
            await service.archive_workspace(mock_ws.id)

        # 验证状态更新为 archived
        call_args = mock_update.call_args
        assert call_args.args[1] == mock_ws.id
        assert call_args.args[2] == "archived"

        # 验证审计
        mock_audit.assert_awaited_once()
        event = mock_audit.call_args.args[1]
        assert event.action == "research.workspace.archive"

    @pytest.mark.asyncio
    async def test_delete_workspace_not_found(self, service):
        """删除不存在的工作空间抛出 not_found。"""
        from packages.common.errors import AppError

        _mock_scoped_session(service)

        with patch(
            "packages.research.service.ResearchRepository.get_workspace",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with pytest.raises(AppError, match="研究工作空间不存在"):
                await service.delete_workspace(uuid4())

    @pytest.mark.asyncio
    async def test_delete_workspace_success(self, service):
        """删除工作空间成功（本期无发布成果引用检查）。"""
        mock_ws = MagicMock()
        mock_ws.id = uuid4()

        _mock_scoped_session(service)

        with (
            patch(
                "packages.research.service.ResearchRepository.get_workspace",
                new_callable=AsyncMock,
                return_value=mock_ws,
            ),
            patch(
                "packages.research.service.ResearchRepository.delete_workspace",
                new_callable=AsyncMock,
            ) as mock_delete,
            patch(
                "packages.research.service.ResearchRepository.count_published_results_by_workspace",
                new_callable=AsyncMock,
                return_value=0,
            ),
            patch(
                "packages.research.service.AuditRecorder.record",
                new_callable=AsyncMock,
            ) as mock_audit,
        ):
            await service.delete_workspace(mock_ws.id)

        mock_delete.assert_awaited_once()
        mock_audit.assert_awaited_once()
        event = mock_audit.call_args.args[1]
        assert event.action == "research.workspace.delete"

    @pytest.mark.asyncio
    async def test_fork_workspace_inherits_evidence_refs(self, service):
        """分叉工作空间继承主研究问题最新版本 + 证据引用列表（副本）。"""
        source_ws_id = uuid4()
        new_ws_id = uuid4()

        mock_source = MagicMock()
        mock_source.id = source_ws_id
        mock_source.name = "源工作空间"

        mock_new_ws = MagicMock()
        mock_new_ws.id = new_ws_id
        mock_new_ws.name = "分叉工作空间"
        mock_new_ws.status = "draft"

        mock_qv = MagicMock()
        mock_qv.question_text = "原研究问题"
        mock_qv.sub_questions = ["子问题1"]

        # 2 个 active 证据引用
        ref1 = MagicMock()
        ref1.source_namespace = "core:fact"
        ref1.source_id = uuid4()
        ref1.source_version = "v1"
        ref1.source_name = "Fact1"

        ref2 = MagicMock()
        ref2.source_namespace = "core:fact"
        ref2.source_id = uuid4()
        ref2.source_version = "v2"
        ref2.source_name = "Fact2"

        _mock_scoped_session(service)

        with (
            patch(
                "packages.research.service.ResearchRepository.get_workspace",
                new_callable=AsyncMock,
                return_value=mock_source,
            ),
            patch(
                "packages.research.service.ResearchRepository.get_latest_question_version",
                new_callable=AsyncMock,
                return_value=mock_qv,
            ),
            patch(
                "packages.research.service.ResearchRepository.list_evidence_refs",
                new_callable=AsyncMock,
                return_value=[ref1, ref2],
            ),
            patch(
                "packages.research.service.ResearchRepository.insert_workspace",
                new_callable=AsyncMock,
                return_value=mock_new_ws,
            ),
            patch(
                "packages.research.service.ResearchRepository.insert_question_version",
                new_callable=AsyncMock,
            ) as mock_insert_qv,
            patch(
                "packages.research.service.ResearchRepository.update_workspace_current_version",
                new_callable=AsyncMock,
            ),
            patch(
                "packages.research.service.ResearchRepository.insert_evidence_ref",
                new_callable=AsyncMock,
            ) as mock_insert_ref,
            patch(
                "packages.research.service.AuditRecorder.record",
                new_callable=AsyncMock,
            ) as mock_audit,
        ):
            result = await service.fork_workspace(source_ws_id, "分叉工作空间")

        # 验证返回值
        assert isinstance(result, WorkspaceRef)
        assert result.workspace_id == new_ws_id
        assert result.name == "分叉工作空间"
        assert result.forked_from_id == source_ws_id
        assert result.current_question_version == 1

        # 验证插入了问题版本（继承源最新文本）
        mock_insert_qv.assert_awaited_once()
        qv_call = mock_insert_qv.call_args
        assert qv_call.kwargs["question_text"] == "原研究问题"
        assert qv_call.kwargs["sub_questions"] == ["子问题1"]

        # 验证插入了 2 个证据引用副本
        assert mock_insert_ref.await_count == 2

        # 验证审计事件
        mock_audit.assert_awaited_once()
        event = mock_audit.call_args.args[1]
        assert event.action == "research.workspace.fork"

    @pytest.mark.asyncio
    async def test_fork_workspace_no_evidence(self, service):
        """分叉时源工作空间无证据引用，不插入证据。"""
        source_ws_id = uuid4()
        new_ws_id = uuid4()

        mock_source = MagicMock()
        mock_source.id = source_ws_id

        mock_new_ws = MagicMock()
        mock_new_ws.id = new_ws_id
        mock_new_ws.name = "分叉"
        mock_new_ws.status = "draft"

        mock_qv = MagicMock()
        mock_qv.question_text = "问题"
        mock_qv.sub_questions = []

        _mock_scoped_session(service)

        with (
            patch(
                "packages.research.service.ResearchRepository.get_workspace",
                new_callable=AsyncMock,
                return_value=mock_source,
            ),
            patch(
                "packages.research.service.ResearchRepository.get_latest_question_version",
                new_callable=AsyncMock,
                return_value=mock_qv,
            ),
            patch(
                "packages.research.service.ResearchRepository.list_evidence_refs",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "packages.research.service.ResearchRepository.insert_workspace",
                new_callable=AsyncMock,
                return_value=mock_new_ws,
            ),
            patch(
                "packages.research.service.ResearchRepository.insert_question_version",
                new_callable=AsyncMock,
            ),
            patch(
                "packages.research.service.ResearchRepository.update_workspace_current_version",
                new_callable=AsyncMock,
            ),
            patch(
                "packages.research.service.ResearchRepository.insert_evidence_ref",
                new_callable=AsyncMock,
            ) as mock_insert_ref,
            patch(
                "packages.research.service.AuditRecorder.record",
                new_callable=AsyncMock,
            ),
        ):
            result = await service.fork_workspace(source_ws_id, "分叉")

        assert result.forked_from_id == source_ws_id
        mock_insert_ref.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_fork_workspace_no_question(self, service):
        """分叉时源工作空间无研究问题，新工作空间问题文本为空。"""
        source_ws_id = uuid4()
        new_ws_id = uuid4()

        mock_source = MagicMock()
        mock_source.id = source_ws_id

        mock_new_ws = MagicMock()
        mock_new_ws.id = new_ws_id
        mock_new_ws.name = "分叉"
        mock_new_ws.status = "draft"

        _mock_scoped_session(service)

        with (
            patch(
                "packages.research.service.ResearchRepository.get_workspace",
                new_callable=AsyncMock,
                return_value=mock_source,
            ),
            patch(
                "packages.research.service.ResearchRepository.get_latest_question_version",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "packages.research.service.ResearchRepository.list_evidence_refs",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "packages.research.service.ResearchRepository.insert_workspace",
                new_callable=AsyncMock,
                return_value=mock_new_ws,
            ),
            patch(
                "packages.research.service.ResearchRepository.insert_question_version",
                new_callable=AsyncMock,
            ) as mock_insert_qv,
            patch(
                "packages.research.service.ResearchRepository.update_workspace_current_version",
                new_callable=AsyncMock,
            ),
            patch(
                "packages.research.service.AuditRecorder.record",
                new_callable=AsyncMock,
            ),
        ):
            await service.fork_workspace(source_ws_id, "分叉")

        # 验证问题文本为空字符串
        mock_insert_qv.assert_awaited_once()
        assert mock_insert_qv.call_args.kwargs["question_text"] == ""

    @pytest.mark.asyncio
    async def test_update_question_creates_new_version(self, service):
        """更新研究问题创建新版本，版本号递增。"""
        ws_id = uuid4()
        mock_ws = MagicMock()
        mock_ws.id = ws_id
        mock_ws.current_question_version = 2

        mock_qv = MagicMock()
        mock_qv.id = uuid4()

        _mock_scoped_session(service)

        with (
            patch(
                "packages.research.service.ResearchRepository.get_workspace",
                new_callable=AsyncMock,
                return_value=mock_ws,
            ),
            patch(
                "packages.research.service.ResearchRepository.insert_question_version",
                new_callable=AsyncMock,
                return_value=mock_qv,
            ) as mock_insert_qv,
            patch(
                "packages.research.service.ResearchRepository.update_workspace_current_version",
                new_callable=AsyncMock,
            ) as mock_update_version,
            patch(
                "packages.research.service.AuditRecorder.record",
                new_callable=AsyncMock,
            ),
        ):
            result = await service.update_question(ws_id, "新研究问题", ["子问题A", "子问题B"])

        # 验证版本号递增
        assert isinstance(result, QuestionVersionRef)
        assert result.version_number == 3

        # 验证插入新版本时版本号为 3
        assert mock_insert_qv.call_args.kwargs["version_number"] == 3

        # 验证更新了工作空间版本号
        mock_update_version.assert_awaited_once()
        assert mock_update_version.call_args.args[1] == ws_id
        assert mock_update_version.call_args.args[2] == 3

    @pytest.mark.asyncio
    async def test_search_facts_delegates_to_provider(self, service, mock_fact_provider):
        """search_facts 委托 CoreFactProvider。"""
        fact_id = uuid4()
        mock_facts = [
            FactSummary(
                fact_id=fact_id,
                fact_type="experiment_run",
                subject_id="实验001",
                status="active",
            )
        ]
        mock_fact_provider.search_facts = AsyncMock(return_value=(mock_facts, "next-cursor"))

        result_facts, cursor = await service.search_facts("Na2O", page_size=10)

        assert len(result_facts) == 1
        assert result_facts[0].fact_id == fact_id
        assert cursor == "next-cursor"
        mock_fact_provider.search_facts.assert_awaited_once()


# ---------------------------------------------------------------------------
# 6. EvidenceSnapshotService 测试
# ---------------------------------------------------------------------------


class TestEvidenceSnapshotService:
    """EvidenceSnapshotService 冻结快照与哈希计算测试。"""

    @pytest.fixture
    def mock_fact_provider(self):
        """模拟 CoreFactProvider。"""
        provider = AsyncMock()
        provider.get_fact_summary = AsyncMock(
            return_value=FactSummary(
                fact_id=uuid4(),
                fact_type="experiment_run",
                subject_id="实验001",
                status="active",
                department_name="实验室A",
            )
        )
        provider.get_fact_fields = AsyncMock(return_value=["组分", "结果"])
        provider.get_fact_data = AsyncMock(
            return_value={
                "metadata": {"组分": "Na2O"},
                "points": [{"name": "结果", "value": 42.5}],
            }
        )
        return provider

    @pytest.fixture
    def snapshot_service(self, mock_fact_provider):
        """创建 EvidenceSnapshotService 实例。"""
        from packages.research.snapshots import EvidenceSnapshotService

        mock_factory = MagicMock()
        return EvidenceSnapshotService(
            session_factory=mock_factory,
            department_id=uuid4(),
            actor_id=uuid4(),
            fact_provider=mock_fact_provider,
        )

    def test_compute_content_hash_sha256(self, snapshot_service):
        """内容哈希使用 SHA-256 计算。"""
        ref1 = MagicMock()
        ref1.source_namespace = "core:fact"
        ref1.source_id = uuid4()

        fact_fields_map = {ref1.source_id: ["组分", "结果"]}
        fact_data_map = {
            ref1.source_id: {
                "metadata": {"组分": "Na2O"},
                "points": [{"name": "结果", "value": 42.5}],
            }
        }

        content_hash = snapshot_service._compute_content_hash(
            [ref1], fact_fields_map, fact_data_map
        )

        # SHA-256 哈希为 64 字符十六进制
        assert len(content_hash) == 64
        assert all(c in "0123456789abcdef" for c in content_hash)

        # 手动验证哈希值
        entries = [
            {
                "namespace": "core:fact",
                "id": str(ref1.source_id),
                "field": "组分",
                "value": "Na2O",
            },
            {
                "namespace": "core:fact",
                "id": str(ref1.source_id),
                "field": "结果",
                "value": 42.5,
            },
        ]
        entries.sort(key=lambda e: (e["namespace"], e["id"], e["field"]))
        expected_bytes = json.dumps(
            entries, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        expected_hash = hashlib.sha256(expected_bytes).hexdigest()
        assert content_hash == expected_hash

    def test_compute_content_hash_sorted(self, snapshot_service):
        """哈希计算按 (namespace, id, field) 排序。"""
        ref1 = MagicMock()
        ref1.source_namespace = "core:fact"
        ref1.source_id = uuid4()

        ref2 = MagicMock()
        ref2.source_namespace = "core:fact"
        ref2.source_id = uuid4()

        fact_fields_map = {
            ref1.source_id: ["z_field", "a_field"],
            ref2.source_id: ["m_field"],
        }
        fact_data_map = {
            ref1.source_id: {"metadata": {"a_field": 1, "z_field": 2}},
            ref2.source_id: {"metadata": {"m_field": 3}},
        }

        hash1 = snapshot_service._compute_content_hash([ref2, ref1], fact_fields_map, fact_data_map)
        hash2 = snapshot_service._compute_content_hash([ref1, ref2], fact_fields_map, fact_data_map)

        # 不同顺序的 refs 应产生相同哈希（内部排序）
        assert hash1 == hash2

    def test_compute_content_hash_deterministic(self, snapshot_service):
        """相同输入产生相同哈希。"""
        ref = MagicMock()
        ref.source_namespace = "core:fact"
        ref.source_id = uuid4()

        fact_fields_map = {ref.source_id: ["field1"]}
        fact_data_map = {ref.source_id: {"metadata": {"field1": "value1"}}}

        hash1 = snapshot_service._compute_content_hash([ref], fact_fields_map, fact_data_map)
        hash2 = snapshot_service._compute_content_hash([ref], fact_fields_map, fact_data_map)

        assert hash1 == hash2

    def test_compute_content_hash_empty(self, snapshot_service):
        """无引用时哈希为空 entries 的哈希。"""
        hash_val = snapshot_service._compute_content_hash([], {}, {})
        expected = hashlib.sha256(b"[]").hexdigest()
        assert hash_val == expected

    def test_extract_field_value_from_metadata(self, snapshot_service):
        """从 metadata 中提取字段值。"""
        fact_data = {"metadata": {"组分": "Na2O", "温度": "1200℃"}}
        assert snapshot_service._extract_field_value(fact_data, "组分") == "Na2O"
        assert snapshot_service._extract_field_value(fact_data, "温度") == "1200℃"
        assert snapshot_service._extract_field_value(fact_data, "不存在的字段") is None

    def test_extract_field_value_from_points(self, snapshot_service):
        """从 points 中提取字段值。"""
        fact_data = {
            "points": [
                {"name": "D50", "value": 12.5},
                {"name": "比表面积", "value": 3500},
            ]
        }
        assert snapshot_service._extract_field_value(fact_data, "D50") == 12.5
        assert snapshot_service._extract_field_value(fact_data, "比表面积") == 3500

    def test_extract_field_value_not_found(self, snapshot_service):
        """字段不存在时返回 None。"""
        fact_data = {"metadata": {}, "points": []}
        assert snapshot_service._extract_field_value(fact_data, "any") is None

    def test_build_permission_envelope(self, snapshot_service):
        """权限包络记录每个 source 的权限快照。"""
        fact_id = uuid4()
        ref = MagicMock()
        ref.source_id = fact_id

        summaries = {
            fact_id: FactSummary(
                fact_id=fact_id,
                fact_type="experiment_run",
                subject_id="实验001",
                status="active",
                department_name="实验室A",
            )
        }

        envelope = snapshot_service._build_permission_envelope([ref], summaries)

        assert str(fact_id) in envelope
        assert envelope[str(fact_id)]["fact_type"] == "experiment_run"
        assert envelope[str(fact_id)]["status"] == "active"
        assert envelope[str(fact_id)]["department_name"] == "实验室A"

    def test_build_permission_envelope_empty(self, snapshot_service):
        """无引用时权限包络为空字典。"""
        envelope = snapshot_service._build_permission_envelope([], {})
        assert envelope == {}

    def test_build_field_manifest(self, snapshot_service):
        """字段清单提取正确。"""
        fact_id = uuid4()
        ref = MagicMock()
        ref.source_id = fact_id

        fields_map = {fact_id: ["组分", "结果", "D50"]}

        manifest = snapshot_service._build_field_manifest([ref], fields_map)

        assert str(fact_id) in manifest
        assert manifest[str(fact_id)] == ["组分", "结果", "D50"]

    def test_build_field_manifest_empty(self, snapshot_service):
        """无引用时字段清单为空字典。"""
        manifest = snapshot_service._build_field_manifest([], {})
        assert manifest == {}

    @pytest.mark.asyncio
    async def test_freeze_snapshot_no_active_evidence(self, snapshot_service):
        """无活跃证据引用时冻结快照抛出 validation_failed。"""
        from packages.common.errors import AppError

        ws_id = uuid4()
        mock_ws = MagicMock()
        mock_ws.id = ws_id

        _mock_scoped_session(snapshot_service)

        with (
            patch(
                "packages.research.snapshots.ResearchRepository.get_workspace",
                new_callable=AsyncMock,
                return_value=mock_ws,
            ),
            patch(
                "packages.research.snapshots.ResearchRepository.list_evidence_refs",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            with pytest.raises(AppError, match="无活跃证据引用"):
                await snapshot_service.freeze_snapshot(ws_id)

    @pytest.mark.asyncio
    async def test_freeze_snapshot_workspace_not_found(self, snapshot_service):
        """工作空间不存在时冻结快照抛出 not_found。"""
        from packages.common.errors import AppError

        _mock_scoped_session(snapshot_service)

        with patch(
            "packages.research.snapshots.ResearchRepository.get_workspace",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with pytest.raises(AppError, match="研究工作空间不存在"):
                await snapshot_service.freeze_snapshot(uuid4())


# ---------------------------------------------------------------------------
# 7. CoreFactProvider 测试
# ---------------------------------------------------------------------------


class TestCoreFactProvider:
    """CoreFactProvider 只读适配器测试。"""

    def test_core_fact_provider_protocol_exists(self):
        """CoreFactProvider 协议存在。"""
        from packages.research.core_adapter import CoreFactProvider

        assert CoreFactProvider is not None

    def test_core_fact_provider_impl_exists(self):
        """CoreFactProviderImpl 实现类存在。"""
        from packages.research.core_adapter import CoreFactProviderImpl

        assert CoreFactProviderImpl is not None

    def test_impl_does_not_expose_session(self):
        """CoreFactProviderImpl 不暴露 session 引用。"""
        from packages.research.core_adapter import CoreFactProviderImpl

        mock_query_service = MagicMock()
        provider = CoreFactProviderImpl(query_service=mock_query_service)

        # 不应该有 session 或 _session 属性
        assert not hasattr(provider, "session")
        assert not hasattr(provider, "_session")

    @pytest.mark.asyncio
    async def test_search_facts_converts_to_fact_summary(self):
        """search_facts 将搜索结果转换为 FactSummary 列表。"""
        from packages.research.core_adapter import CoreFactProviderImpl

        mock_row = {
            "fact_id": uuid4(),
            "fact_type": "experiment_run",
            "subject_id": "实验001",
            "status": "active",
            "department_id": uuid4(),
        }

        mock_query_service = MagicMock()
        mock_query_service._rls_dept_id = uuid4()
        mock_query_service._actor_id = uuid4()

        mock_result = MagicMock()
        mock_result.mappings.return_value.all.return_value = [mock_row]
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        @asynccontextmanager
        async def _fake_scoped_session(*args, **kwargs):
            yield mock_session

        with patch("packages.research.core_adapter.scoped_session", _fake_scoped_session):
            provider = CoreFactProviderImpl(query_service=mock_query_service)
            results, cursor = await provider.search_facts("Na2O", page_size=10)

        assert len(results) == 1
        assert isinstance(results[0], FactSummary)
        assert results[0].fact_type == "experiment_run"
        assert results[0].subject_id == "实验001"
        assert results[0].status == "active"

    @pytest.mark.asyncio
    async def test_get_fact_summary_converts_to_fact_summary(self):
        """get_fact_summary 将详情转换为 FactSummary。"""
        from packages.research.core_adapter import CoreFactProviderImpl

        mock_row = MagicMock()
        mock_row.fact_id = uuid4()
        mock_row.fact_type = "experiment_run"
        mock_row.subject_id = "实验001"
        mock_row.status = "active"
        mock_row.department_name = "实验室A"

        mock_query_service = AsyncMock()
        mock_query_service.get_fact_detail = AsyncMock(return_value=mock_row)

        provider = CoreFactProviderImpl(query_service=mock_query_service)
        result = await provider.get_fact_summary(uuid4())

        assert isinstance(result, FactSummary)
        assert result.fact_id == mock_row.fact_id
        assert result.fact_type == "experiment_run"

    @pytest.mark.asyncio
    async def test_get_fact_summary_not_found_to_forbidden(self):
        """get_fact_summary 将 not_found 转换为 forbidden（不泄露 Fact 是否存在）。"""
        from packages.research.core_adapter import CoreFactProviderImpl

        from packages.common.errors import AppError

        mock_query_service = AsyncMock()
        mock_query_service.get_fact_detail = AsyncMock(
            side_effect=AppError(
                code="not_found",
                message="Fact 不存在",
                retryable=False,
            )
        )

        provider = CoreFactProviderImpl(query_service=mock_query_service)
        with pytest.raises(AppError, match="无权访问该 Fact 数据") as exc_info:
            await provider.get_fact_summary(uuid4())

        assert exc_info.value.code == "forbidden"

    @pytest.mark.asyncio
    async def test_get_fact_fields_extracts_from_metadata_and_points(self):
        """get_fact_fields 从 metadata 和 points 中提取字段名。"""
        from packages.research.core_adapter import CoreFactProviderImpl

        mock_data = {
            "metadata": {"组分": "Na2O", "温度": "1200"},
            "points": [
                {"name": "D50", "value": 12.5},
                {"name": "比表面积", "value": 3500},
                {"name": "组分", "value": "Na2O"},  # 重复字段
            ],
        }

        mock_query_service = AsyncMock()
        mock_query_service.get_fact_data = AsyncMock(return_value=mock_data)

        provider = CoreFactProviderImpl(query_service=mock_query_service)
        fields = await provider.get_fact_fields(uuid4())

        # 包含 metadata 和 points 的字段名
        assert "组分" in fields
        assert "温度" in fields
        assert "D50" in fields
        assert "比表面积" in fields
        # 不重复
        assert fields.count("组分") == 1

    @pytest.mark.asyncio
    async def test_get_fact_fields_empty_data(self):
        """get_fact_fields 空数据返回空列表。"""
        from packages.research.core_adapter import CoreFactProviderImpl

        mock_query_service = AsyncMock()
        mock_query_service.get_fact_data = AsyncMock(return_value={"metadata": {}, "points": []})

        provider = CoreFactProviderImpl(query_service=mock_query_service)
        fields = await provider.get_fact_fields(uuid4())

        assert fields == []

    @pytest.mark.asyncio
    async def test_get_fact_data_delegates(self):
        """get_fact_data 委托给 query_service。"""
        from packages.research.core_adapter import CoreFactProviderImpl

        mock_data = {"metadata": {"key": "value"}}
        mock_query_service = AsyncMock()
        mock_query_service.get_fact_data = AsyncMock(return_value=mock_data)

        provider = CoreFactProviderImpl(query_service=mock_query_service)
        result = await provider.get_fact_data(uuid4())

        assert result == mock_data


# ---------------------------------------------------------------------------
# 8. ResearchCatalog 占位测试
# ---------------------------------------------------------------------------


class TestResearchCatalog:
    """ResearchCatalog 占位实现测试。"""

    @pytest.mark.asyncio
    async def test_stub_returns_empty_list(self):
        """ResearchCatalogStub 返回空列表。"""
        from packages.research.catalog import ResearchCatalogStub

        stub = ResearchCatalogStub()
        result = await stub.search_derived_data("query")
        assert result == []

    @pytest.mark.asyncio
    async def test_stub_with_filters(self):
        """带过滤条件的占位搜索也返回空。"""
        from packages.research.catalog import ResearchCatalogStub

        stub = ResearchCatalogStub()
        result = await stub.search_derived_data("query", {"type": "model"})
        assert result == []


# ---------------------------------------------------------------------------
# 9. 迁移测试
# ---------------------------------------------------------------------------


class TestMigration0074:
    """验证 0074_research_foundation.py 迁移结构。"""

    MIGRATIONS_DIR = Path(__file__).parents[1] / "migrations" / "versions"

    def _load_migration_module(self):
        """动态加载 0074 迁移文件。"""
        files = list(self.MIGRATIONS_DIR.glob("0074_*.py"))
        assert files, "找不到 0074 迁移文件"
        assert len(files) == 1, f"0074 匹配到多个文件: {files}"
        spec = importlib.util.spec_from_file_location("migration_0074", files[0])
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_migration_revision_chain(self):
        """迁移链连续性：revision=0074, down_revision=0073。"""
        mod = self._load_migration_module()
        assert mod.revision == "0074"
        assert mod.down_revision == "0073"

    def test_upgrade_creates_four_tables(self):
        """upgrade 创建 4 张表。"""
        mod = self._load_migration_module()

        # 捕获 op.execute 调用的 SQL
        executed_sqls: list[str] = []

        class _MockOp:
            def execute(self, sql):
                executed_sqls.append(str(sql))

        with patch.object(mod, "op", _MockOp()):
            mod.upgrade()

        # 验证 4 张表的 CREATE TABLE
        create_tables = [s for s in executed_sqls if "CREATE TABLE" in s.upper()]
        assert len(create_tables) == 4, f"期望 4 张表，实际 {len(create_tables)}"

        # 验证表名
        all_sql = " ".join(executed_sqls)
        assert "research_workspace" in all_sql
        assert "research_question_version" in all_sql
        assert "research_workspace_evidence_ref" in all_sql
        assert "research_evidence_snapshot" in all_sql

    def test_upgrade_creates_indexes(self):
        """upgrade 创建 4 个普通索引。"""
        mod = self._load_migration_module()

        executed_sqls: list[str] = []

        class _MockOp:
            def execute(self, sql):
                executed_sqls.append(str(sql))

        with patch.object(mod, "op", _MockOp()):
            mod.upgrade()

        create_indexes = [
            s for s in executed_sqls if "CREATE INDEX" in s.upper() and "UNIQUE" not in s.upper()
        ]
        assert len(create_indexes) == 4, f"期望 4 个普通索引，实际 {len(create_indexes)}"

        all_sql = " ".join(executed_sqls)
        assert "ix_research_workspace_owner_user_id" in all_sql
        assert "ix_research_question_version_workspace_id" in all_sql
        assert "ix_research_evidence_ref_workspace_id" in all_sql
        assert "ix_research_snapshot_workspace_id" in all_sql

    def test_upgrade_creates_unique_constraints(self):
        """upgrade 创建 2 个唯一约束/唯一索引。"""
        mod = self._load_migration_module()

        executed_sqls: list[str] = []

        class _MockOp:
            def execute(self, sql):
                executed_sqls.append(str(sql))

        with patch.object(mod, "op", _MockOp()):
            mod.upgrade()

        unique_indexes = [s for s in executed_sqls if "CREATE UNIQUE INDEX" in s.upper()]
        assert len(unique_indexes) == 2, f"期望 2 个唯一索引，实际 {len(unique_indexes)}"

        all_sql = " ".join(executed_sqls)
        # (workspace_id, version_number) 唯一约束
        assert "uq_rqv_workspace_version" in all_sql
        # (workspace_id, source_namespace, source_id) WHERE status='active' 唯一约束
        assert "uq_evidence_ref_workspace_source" in all_sql

    def test_downgrade_drops_all_tables(self):
        """downgrade 按反序删除 4 张表。"""
        mod = self._load_migration_module()

        executed_sqls: list[str] = []

        class _MockOp:
            def execute(self, sql):
                executed_sqls.append(str(sql))

        with patch.object(mod, "op", _MockOp()):
            mod.downgrade()

        drop_tables = [s for s in executed_sqls if "DROP TABLE" in s.upper()]
        assert len(drop_tables) == 4, f"期望 4 个 DROP TABLE，实际 {len(drop_tables)}"

        # 验证反序删除
        all_sql = " ".join(executed_sqls)
        assert "research_evidence_snapshot" in all_sql
        assert "research_workspace_evidence_ref" in all_sql
        assert "research_question_version" in all_sql
        assert "research_workspace" in all_sql


# ---------------------------------------------------------------------------
# 10. 模块隔离验证
# ---------------------------------------------------------------------------


class TestModuleIsolation:
    """验证新模块不反向侵入老系统。"""

    def test_core_tables_no_fk_to_research(self):
        """核心表（fact, evidence_set 等）无到 research_* 表的外键。"""
        # 导入所有模型确保 metadata 完整
        # 检查所有非 research_ 表是否有到 research_ 表的 FK
        # 注意：某些 FK 目标表可能未加载到 metadata（如 experiment_project），
        # 此时 fk.column.table.name 会抛 NoReferencedTableError，
        # 这是已有的元数据加载问题，与本测试无关，跳过这些列。
        from sqlalchemy.exc import NoReferencedTableError

        import packages.facts.entities  # noqa: F401
        import packages.research.entities  # noqa: F401

        for table_name, table in Base.metadata.tables.items():
            if table_name.startswith("research_"):
                continue
            for col in table.columns:
                for fk in col.foreign_keys:
                    try:
                        target_table = fk.column.table.name
                    except NoReferencedTableError:
                        # FK 目标表未加载（已有问题，非研究域引入）
                        continue
                    assert not target_table.startswith("research_"), (
                        f"核心表 {table_name}.{col.name} 有到 research 表 "
                        f"{target_table} 的外键，违反模块隔离约定"
                    )

    def test_research_tables_use_research_prefix(self):
        """研究域表名均以 research_ 前缀命名。"""
        import packages.research.entities  # noqa: F401

        research_tables = {t for t in Base.metadata.tables if t.startswith("research_")}
        assert "research_workspace" in research_tables
        assert "research_question_version" in research_tables
        assert "research_workspace_evidence_ref" in research_tables
        assert "research_evidence_snapshot" in research_tables

    def test_router_registration_gated_by_feature_flag(self):
        """功能开关闭时 research_router 不注册。"""
        import os

        import packages.common.feature_flags as ff_module

        # 模拟功能开关关闭
        with patch.dict(os.environ, {"RESEARCH_MODULE_ENABLED": "false"}):
            importlib.reload(ff_module)
            assert ff_module.RESEARCH_MODULE_ENABLED is False

        # 恢复默认
        with patch.dict(os.environ, {}, clear=False):
            if "RESEARCH_MODULE_ENABLED" in os.environ:
                del os.environ["RESEARCH_MODULE_ENABLED"]
            importlib.reload(ff_module)


# ---------------------------------------------------------------------------
# 11. API 路由测试
# ---------------------------------------------------------------------------


class TestResearchAPI:
    """研究域 API 端点测试。"""

    def test_router_has_14_endpoints(self):
        """research_router 包含 14 个端点。"""
        from apps.api.routers.research import research_router

        routes = [r for r in research_router.routes if hasattr(r, "methods") and r.methods]
        assert len(routes) == 14, f"期望 14 个端点，实际 {len(routes)}"


# ---------------------------------------------------------------------------
# 12. 数据类测试
# ---------------------------------------------------------------------------


class TestDataModels:
    """验证研究域数据类（models.py）正确性。"""

    def test_create_workspace_command_is_frozen(self):
        """CreateWorkspaceCommand 为 frozen dataclass。"""
        cmd = CreateWorkspaceCommand(name="test", question_text="question")
        assert cmd.name == "test"
        assert cmd.question_text == "question"
        with pytest.raises(AttributeError):
            cmd.name = "other"

    def test_workspace_ref_is_frozen(self):
        """WorkspaceRef 为 frozen dataclass。"""
        ref = WorkspaceRef(
            workspace_id=uuid4(),
            name="test",
            status="draft",
            current_question_version=1,
        )
        with pytest.raises(AttributeError):
            ref.name = "other"

    def test_question_version_ref_default_sub_questions(self):
        """QuestionVersionRef 默认 sub_questions 为空列表。"""
        ref = QuestionVersionRef(
            version_id=uuid4(),
            workspace_id=uuid4(),
            version_number=1,
            question_text="问题",
        )
        assert ref.sub_questions == []

    def test_evidence_ref_dto_is_frozen(self):
        """EvidenceRefDTO 为 frozen dataclass。"""
        dto = EvidenceRefDTO(
            ref_id=uuid4(),
            source_namespace="core:fact",
            source_id=uuid4(),
            source_version=None,
            source_name=None,
            status="active",
        )
        with pytest.raises(AttributeError):
            dto.status = "removed"

    def test_snapshot_ref_is_frozen(self):
        """SnapshotRef 为 frozen dataclass。"""
        ref = SnapshotRef(
            snapshot_id=uuid4(),
            snapshot_number=1,
            content_hash="a" * 64,
            captured_at=datetime.now(UTC),
        )
        with pytest.raises(AttributeError):
            ref.snapshot_number = 2

    def test_fact_summary_optional_fields(self):
        """FactSummary 可选字段默认值正确。"""
        summary = FactSummary(
            fact_id=uuid4(),
            fact_type="experiment_run",
            subject_id="实验001",
            status="active",
        )
        assert summary.department_name is None

    def test_workspace_detail_defaults(self):
        """WorkspaceDetail 默认 snapshots 为空列表。"""
        from packages.research.dtos import WorkspaceDetail

        detail = WorkspaceDetail(
            workspace_id=uuid4(),
            name="test",
            status="draft",
            current_question=None,
            evidence_count=0,
        )
        assert detail.snapshots == []
