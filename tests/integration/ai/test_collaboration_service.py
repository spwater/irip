"""AI 协作服务方法集成测试（irip-ai-collab）。

覆盖 P0 功能需求：
- P0-01 对话参与者管理（创建自动 owner / 邀请 / 移除 / 退出 / 跨部门 拒绝）；
- P0-02/09 对话三栏查询（private / same_dept / cross_dept 返回空）；
- P0-03 @人提及（list_mentionable_users 同 org active）。

依赖测试数据库（IRIP_TEST_DATABASE_URL），
通过根 conftest 的 async_session_factory + test_user fixture。
"""

from uuid import uuid4

import pytest
import sqlalchemy as sa

from packages.ai.offline_provider import OfflineProvider
from packages.ai.service import AIService
from packages.ai.tools import ToolRegistry
from packages.auth.passwords import hash_password
from packages.common.errors import AppError


@pytest.fixture
def ai_service(async_session_factory):  # type: ignore[no-untyped-def]
    """构造 AIService 实例（离线 Provider + 空 ToolRegistry）。"""
    return AIService(
        provider=OfflineProvider(),
        tool_registry=ToolRegistry(tools=()),
        session_factory=async_session_factory,
    )


def _insert_user(
    sync_engine, email: str, org_id=None, display_name="用户", roles=None, status="active"
):
    """插入测试用户并返回 (user_id, org_id)。

    当 org_id 为 None 时自动创建 department 记录（满足 app_user.department_id FK 约束）。
    """
    from packages.common.ids import new_id as _new_id

    user_id = _new_id()
    final_org = org_id if org_id is not None else _new_id()
    with sync_engine.connect() as conn:
        # 当创建新 org 时，先插入 department 记录（满足 app_user.department_id FK 约束）
        if org_id is None:
            conn.execute(
                sa.text(
                    "INSERT INTO department "
                    "(id, code, display_name, status, lock_version) "
                    "VALUES (:id, :code, :name, 'active', 0)"
                ),
                {
                    "id": final_org,
                    "code": f"test-dept-{final_org.hex[:8]}",
                    "name": "Test Department",
                },
            )
        conn.execute(
            sa.text(
                "INSERT INTO app_user "
                "(id, department_id, email, display_name, "
                "password_hash, status, roles, lock_version, token_version) "
                "VALUES (:id, :org, :email, :name, :hash, :status, :roles, 0, 0)"
            ),
            {
                "id": user_id,
                "org": final_org,
                "email": email,
                "name": display_name,
                "hash": hash_password("Test-Password-2026!"),
                "status": status,
                "roles": __import__("json").dumps(roles or ["lab_member"]),
            },
        )
        conn.commit()
    return user_id, final_org


def _cleanup_user(sync_engine, user_id):
    """清理测试用户及其对话/参与者记录和关联 department。"""
    with sync_engine.connect() as conn:
        # 先查出 department_id
        result = conn.execute(
            sa.text("SELECT department_id FROM app_user WHERE id = :uid"),
            {"uid": user_id},
        )
        row = result.fetchone()
        dept_id = row[0] if row else None
        conn.execute(
            sa.text("DELETE FROM conversation_participant WHERE user_id = :uid"),
            {"uid": user_id},
        )
        conn.execute(
            sa.text(
                "DELETE FROM ai_message WHERE conversation_id IN ("
                "SELECT id FROM ai_conversation WHERE user_id = :uid)"
            ),
            {"uid": user_id},
        )
        conn.execute(
            sa.text("DELETE FROM ai_conversation WHERE user_id = :uid"),
            {"uid": user_id},
        )
        conn.execute(sa.text("DELETE FROM app_user WHERE id = :uid"), {"uid": user_id})
        # 删除 department（仅当没有其他用户引用时）
        if dept_id is not None:
            conn.execute(
                sa.text(
                    "DELETE FROM department WHERE id = :did "
                    "AND NOT EXISTS (SELECT 1 FROM app_user WHERE department_id = :did)"
                ),
                {"did": dept_id},
            )
        conn.commit()


class TestCreateConversationAutoOwner:
    """P0-01: 创建对话时自动插入 owner 记录。"""

    async def test_create_conversation_inserts_owner_participant(self, ai_service, sync_engine):
        user_id, org_id = _insert_user(sync_engine, f"owner-{uuid4().hex[:8]}@irip.local")
        try:
            ref = await ai_service.create_conversation(
                user_id=user_id,
                department_id=org_id,
                title="协作测试对话",
            )
            # 校验 conversation_participant 表有 owner 记录
            with sync_engine.connect() as conn:
                row = conn.execute(
                    sa.text(
                        "SELECT role FROM conversation_participant "
                        "WHERE conversation_id = :cid AND user_id = :uid"
                    ),
                    {"cid": ref.id, "uid": user_id},
                ).fetchone()
            assert row is not None
            assert row[0] == "owner"
        finally:
            _cleanup_user(sync_engine, user_id)


class TestAddParticipant:
    """P0-01: 邀请同 org 成员 / 跨部门 拒绝 / 重复邀请冲突。"""

    async def test_invite_same_dept_member(self, ai_service, sync_engine):
        owner_id, org_id = _insert_user(
            sync_engine, f"inviter-{uuid4().hex[:8]}@irip.local", display_name="邀请者"
        )
        target_id, _ = _insert_user(
            sync_engine,
            f"target-{uuid4().hex[:8]}@irip.local",
            org_id=org_id,
            display_name="被邀请者",
        )
        try:
            conv = await ai_service.create_conversation(
                user_id=owner_id, department_id=org_id, title="邀请测试"
            )
            ref = await ai_service.add_participant(
                conversation_id=conv.id,
                inviter_user_id=owner_id,
                target_user_id=target_id,
            )
            assert ref.role == "member"
            assert ref.user_id == target_id
            assert ref.display_name == "被邀请者"
        finally:
            _cleanup_user(sync_engine, owner_id)
            _cleanup_user(sync_engine, target_id)

    async def test_invite_cross_dept_rejected(self, ai_service, sync_engine):
        owner_id, org_a = _insert_user(sync_engine, f"cross-owner-{uuid4().hex[:8]}@irip.local")
        target_id, org_b = _insert_user(sync_engine, f"cross-target-{uuid4().hex[:8]}@irip.local")
        assert org_a != org_b
        try:
            conv = await ai_service.create_conversation(
                user_id=owner_id, department_id=org_a, title="跨org测试"
            )
            with pytest.raises(AppError) as exc_info:
                await ai_service.add_participant(
                    conversation_id=conv.id,
                    inviter_user_id=owner_id,
                    target_user_id=target_id,
                )
            assert exc_info.value.code == "validation_failed"
        finally:
            _cleanup_user(sync_engine, owner_id)
            _cleanup_user(sync_engine, target_id)

    async def test_invite_duplicate_conflict(self, ai_service, sync_engine):
        owner_id, org_id = _insert_user(sync_engine, f"dup-owner-{uuid4().hex[:8]}@irip.local")
        target_id, _ = _insert_user(
            sync_engine,
            f"dup-target-{uuid4().hex[:8]}@irip.local",
            org_id=org_id,
        )
        try:
            conv = await ai_service.create_conversation(
                user_id=owner_id, department_id=org_id, title="重复邀请测试"
            )
            await ai_service.add_participant(
                conversation_id=conv.id,
                inviter_user_id=owner_id,
                target_user_id=target_id,
            )
            with pytest.raises(AppError) as exc_info:
                await ai_service.add_participant(
                    conversation_id=conv.id,
                    inviter_user_id=owner_id,
                    target_user_id=target_id,
                )
            assert exc_info.value.code == "conflict"
        finally:
            _cleanup_user(sync_engine, owner_id)
            _cleanup_user(sync_engine, target_id)

    async def test_invite_by_non_owner_forbidden(self, ai_service, sync_engine):
        owner_id, org_id = _insert_user(sync_engine, f"real-owner-{uuid4().hex[:8]}@irip.local")
        member_id, _ = _insert_user(
            sync_engine, f"member-{uuid4().hex[:8]}@irip.local", org_id=org_id
        )
        target_id, _ = _insert_user(
            sync_engine, f"target2-{uuid4().hex[:8]}@irip.local", org_id=org_id
        )
        try:
            conv = await ai_service.create_conversation(
                user_id=owner_id, department_id=org_id, title="非owner邀请测试"
            )
            # member 先被邀请加入
            await ai_service.add_participant(
                conversation_id=conv.id,
                inviter_user_id=owner_id,
                target_user_id=member_id,
            )
            # member 尝试邀请 target → forbidden
            with pytest.raises(AppError) as exc_info:
                await ai_service.add_participant(
                    conversation_id=conv.id,
                    inviter_user_id=member_id,
                    target_user_id=target_id,
                )
            assert exc_info.value.code == "forbidden"
        finally:
            _cleanup_user(sync_engine, owner_id)
            _cleanup_user(sync_engine, member_id)
            _cleanup_user(sync_engine, target_id)

    async def test_invite_nonexistent_conversation(self, ai_service, sync_engine):
        owner_id, org_id = _insert_user(sync_engine, f"ghost-{uuid4().hex[:8]}@irip.local")
        try:
            with pytest.raises(AppError) as exc_info:
                await ai_service.add_participant(
                    conversation_id=uuid4(),
                    inviter_user_id=owner_id,
                    target_user_id=uuid4(),
                )
            assert exc_info.value.code == "not_found"
        finally:
            _cleanup_user(sync_engine, owner_id)


class TestRemoveParticipant:
    """P0-01: owner 可移除成员。"""

    async def test_remove_member_by_owner(self, ai_service, sync_engine):
        owner_id, org_id = _insert_user(
            sync_engine, f"rm-owner-{uuid4().hex[:8]}@irip.local", display_name="房主"
        )
        member_id, _ = _insert_user(
            sync_engine, f"rm-member-{uuid4().hex[:8]}@irip.local", org_id=org_id
        )
        try:
            conv = await ai_service.create_conversation(
                user_id=owner_id, department_id=org_id, title="移除测试"
            )
            await ai_service.add_participant(
                conversation_id=conv.id,
                inviter_user_id=owner_id,
                target_user_id=member_id,
            )
            await ai_service.remove_participant(
                conversation_id=conv.id,
                owner_user_id=owner_id,
                target_user_id=member_id,
            )
            # 校验已删除
            with sync_engine.connect() as conn:
                row = conn.execute(
                    sa.text(
                        "SELECT 1 FROM conversation_participant "
                        "WHERE conversation_id = :cid AND user_id = :uid"
                    ),
                    {"cid": conv.id, "uid": member_id},
                ).fetchone()
            assert row is None
        finally:
            _cleanup_user(sync_engine, owner_id)
            _cleanup_user(sync_engine, member_id)

    async def test_remove_by_non_owner_forbidden(self, ai_service, sync_engine):
        owner_id, org_id = _insert_user(sync_engine, f"rm2-owner-{uuid4().hex[:8]}@irip.local")
        member_id, _ = _insert_user(
            sync_engine, f"rm2-member-{uuid4().hex[:8]}@irip.local", org_id=org_id
        )
        try:
            conv = await ai_service.create_conversation(
                user_id=owner_id, department_id=org_id, title="非owner移除测试"
            )
            await ai_service.add_participant(
                conversation_id=conv.id,
                inviter_user_id=owner_id,
                target_user_id=member_id,
            )
            with pytest.raises(AppError) as exc_info:
                await ai_service.remove_participant(
                    conversation_id=conv.id,
                    owner_user_id=member_id,
                    target_user_id=owner_id,
                )
            assert exc_info.value.code == "forbidden"
        finally:
            _cleanup_user(sync_engine, owner_id)
            _cleanup_user(sync_engine, member_id)


class TestLeaveConversation:
    """P0-01: 成员可退出，owner 不能退出。"""

    async def test_member_can_leave(self, ai_service, sync_engine):
        owner_id, org_id = _insert_user(sync_engine, f"lv-owner-{uuid4().hex[:8]}@irip.local")
        member_id, _ = _insert_user(
            sync_engine, f"lv-member-{uuid4().hex[:8]}@irip.local", org_id=org_id
        )
        try:
            conv = await ai_service.create_conversation(
                user_id=owner_id, department_id=org_id, title="退出测试"
            )
            await ai_service.add_participant(
                conversation_id=conv.id,
                inviter_user_id=owner_id,
                target_user_id=member_id,
            )
            await ai_service.leave_conversation(conversation_id=conv.id, user_id=member_id)
            with sync_engine.connect() as conn:
                row = conn.execute(
                    sa.text(
                        "SELECT 1 FROM conversation_participant "
                        "WHERE conversation_id = :cid AND user_id = :uid"
                    ),
                    {"cid": conv.id, "uid": member_id},
                ).fetchone()
            assert row is None
        finally:
            _cleanup_user(sync_engine, owner_id)
            _cleanup_user(sync_engine, member_id)

    async def test_owner_cannot_leave(self, ai_service, sync_engine):
        owner_id, org_id = _insert_user(sync_engine, f"noleave-{uuid4().hex[:8]}@irip.local")
        try:
            conv = await ai_service.create_conversation(
                user_id=owner_id, department_id=org_id, title="owner不能退出"
            )
            with pytest.raises(AppError) as exc_info:
                await ai_service.leave_conversation(conversation_id=conv.id, user_id=owner_id)
            assert exc_info.value.code == "forbidden"
        finally:
            _cleanup_user(sync_engine, owner_id)

    async def test_non_participant_leave_not_found(self, ai_service, sync_engine):
        owner_id, org_id = _insert_user(sync_engine, f"noleave2-{uuid4().hex[:8]}@irip.local")
        outsider_id, _ = _insert_user(
            sync_engine, f"outsider-{uuid4().hex[:8]}@irip.local", org_id=org_id
        )
        try:
            conv = await ai_service.create_conversation(
                user_id=owner_id, department_id=org_id, title="非参与者退出"
            )
            with pytest.raises(AppError) as exc_info:
                await ai_service.leave_conversation(conversation_id=conv.id, user_id=outsider_id)
            assert exc_info.value.code == "not_found"
        finally:
            _cleanup_user(sync_engine, owner_id)
            _cleanup_user(sync_engine, outsider_id)


class TestListParticipants:
    """P0-01: 列出对话参与者。"""

    async def test_list_participants_returns_owner_and_members(self, ai_service, sync_engine):
        owner_id, org_id = _insert_user(
            sync_engine, f"lp-owner-{uuid4().hex[:8]}@irip.local", display_name="创建人"
        )
        member_id, _ = _insert_user(
            sync_engine,
            f"lp-member-{uuid4().hex[:8]}@irip.local",
            org_id=org_id,
            display_name="成员甲",
        )
        try:
            conv = await ai_service.create_conversation(
                user_id=owner_id, department_id=org_id, title="列表测试"
            )
            await ai_service.add_participant(
                conversation_id=conv.id,
                inviter_user_id=owner_id,
                target_user_id=member_id,
            )
            refs = await ai_service.list_participants(conversation_id=conv.id, user_id=owner_id)
            roles_map = {r.user_id: r.role for r in refs}
            assert roles_map[owner_id] == "owner"
            assert roles_map[member_id] == "member"
            # display_name 正确填充
            names = {r.user_id: r.display_name for r in refs}
            assert names[owner_id] == "创建人"
            assert names[member_id] == "成员甲"
        finally:
            _cleanup_user(sync_engine, owner_id)
            _cleanup_user(sync_engine, member_id)

    async def test_list_participants_forbidden_for_outsider(self, ai_service, sync_engine):
        owner_id, org_id = _insert_user(sync_engine, f"lp2-owner-{uuid4().hex[:8]}@irip.local")
        outsider_id, _ = _insert_user(
            sync_engine, f"lp2-outsider-{uuid4().hex[:8]}@irip.local", org_id=org_id
        )
        try:
            conv = await ai_service.create_conversation(
                user_id=owner_id, department_id=org_id, title="外部访问测试"
            )
            with pytest.raises(AppError) as exc_info:
                await ai_service.list_participants(conversation_id=conv.id, user_id=outsider_id)
            assert exc_info.value.code == "forbidden"
        finally:
            _cleanup_user(sync_engine, owner_id)
            _cleanup_user(sync_engine, outsider_id)


class TestListConversationsWithTab:
    """P0-02/09: 对话三栏查询。"""

    async def test_cross_dept_returns_empty(self, ai_service, sync_engine):
        user_id, org_id = _insert_user(sync_engine, f"tab-cross-{uuid4().hex[:8]}@irip.local")
        try:
            result = await ai_service.list_conversations_with_tab(
                user_id=user_id, department_id=org_id, tab="cross_dept"
            )
            assert result == []
        finally:
            _cleanup_user(sync_engine, user_id)

    async def test_private_tab_returns_solo_conversations(self, ai_service, sync_engine):
        owner_id, org_id = _insert_user(sync_engine, f"tab-priv-{uuid4().hex[:8]}@irip.local")
        member_id, _ = _insert_user(
            sync_engine, f"tab-priv-m-{uuid4().hex[:8]}@irip.local", org_id=org_id
        )
        try:
            solo = await ai_service.create_conversation(
                user_id=owner_id, department_id=org_id, title="私有对话"
            )
            shared = await ai_service.create_conversation(
                user_id=owner_id, department_id=org_id, title="协作对话"
            )
            await ai_service.add_participant(
                conversation_id=shared.id,
                inviter_user_id=owner_id,
                target_user_id=member_id,
            )
            result = await ai_service.list_conversations_with_tab(
                user_id=owner_id, department_id=org_id, tab="private"
            )
            ids = [r.id for r in result]
            assert solo.id in ids
            assert shared.id not in ids
        finally:
            _cleanup_user(sync_engine, owner_id)
            _cleanup_user(sync_engine, member_id)

    async def test_same_dept_tab_returns_owned_and_participated(self, ai_service, sync_engine):
        owner_id, org_id = _insert_user(sync_engine, f"tab-org-{uuid4().hex[:8]}@irip.local")
        member_id, _ = _insert_user(
            sync_engine, f"tab-org-m-{uuid4().hex[:8]}@irip.local", org_id=org_id
        )
        try:
            solo = await ai_service.create_conversation(
                user_id=owner_id, department_id=org_id, title="我的私有"
            )
            shared = await ai_service.create_conversation(
                user_id=owner_id, department_id=org_id, title="协作"
            )
            await ai_service.add_participant(
                conversation_id=shared.id,
                inviter_user_id=owner_id,
                target_user_id=member_id,
            )
            # owner 视角：same_dept 包含 solo 和 shared
            result = await ai_service.list_conversations_with_tab(
                user_id=owner_id, department_id=org_id, tab="same_dept"
            )
            ids = {r.id for r in result}
            assert solo.id in ids
            assert shared.id in ids
            # member 视角：same_dept 仅包含 shared（参与者）
            result_member = await ai_service.list_conversations_with_tab(
                user_id=member_id, department_id=org_id, tab="same_dept"
            )
            member_ids = {r.id for r in result_member}
            assert shared.id in member_ids
            assert solo.id not in member_ids
            # 参与者信息附带
            shared_conv = next(r for r in result_member if r.id == shared.id)
            assert len(shared_conv.participants) >= 1
        finally:
            _cleanup_user(sync_engine, owner_id)
            _cleanup_user(sync_engine, member_id)


class TestListMentionableUsers:
    """P0-03: list_mentionable_users 返回同 org active 用户（排除自己）。"""

    async def test_returns_same_dept_active_excluding_self(self, ai_service, sync_engine):
        me_id, org_id = _insert_user(
            sync_engine, f"me-{uuid4().hex[:8]}@irip.local", display_name="我", roles=["lab_member"]
        )
        colleague_id, _ = _insert_user(
            sync_engine,
            f"col-{uuid4().hex[:8]}@irip.local",
            org_id=org_id,
            display_name="同事",
            roles=["lab_director"],
        )
        # 跨部门 用户不应出现
        other_dept_id, _ = _insert_user(
            sync_engine,
            f"other-{uuid4().hex[:8]}@irip.local",
            display_name="外人",
            roles=["lab_member"],
        )
        # 禁用用户不应出现
        disabled_id, _ = _insert_user(
            sync_engine,
            f"dis-{uuid4().hex[:8]}@irip.local",
            org_id=org_id,
            display_name="禁用者",
            status="disabled",
        )
        try:
            refs = await ai_service.list_mentionable_users(user_id=me_id, department_id=org_id)
            ids = {r.id for r in refs}
            assert colleague_id in ids
            assert me_id not in ids  # 排除自己
            assert other_dept_id not in ids  # 跨部门 不出现
            assert disabled_id not in ids  # 禁用不出现
            # 校验 colleague 信息
            colleague = next(r for r in refs if r.id == colleague_id)
            assert colleague.display_name == "同事"
            assert "lab_director" in colleague.roles
        finally:
            _cleanup_user(sync_engine, me_id)
            _cleanup_user(sync_engine, colleague_id)
            _cleanup_user(sync_engine, other_dept_id)
            _cleanup_user(sync_engine, disabled_id)
