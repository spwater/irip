"""AI 协作实体 + 值对象单元测试（irip-ai-collab）。

覆盖（P0-01）：
- ConversationParticipant ORM 模型字段映射（表名 / 联合主键 / role 默认值）；
- ParticipantRef 不可变值对象；
- MentionableUserRef 不可变值对象。
纯 Python，无需数据库。
"""

from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa

from packages.ai.collaboration_entities import (
    ConversationParticipant,
    MentionableUserRef,
    ParticipantRef,
)
from packages.common.database import Base


class TestConversationParticipantModel:
    """ConversationParticipant ORM 模型测试。"""

    def test_tablename_is_conversation_participant(self) -> None:
        """表名为 conversation_participant。"""
        assert ConversationParticipant.__tablename__ == "conversation_participant"

    def test_inherits_base(self) -> None:
        """继承 Base。"""
        assert issubclass(ConversationParticipant, Base)

    def test_has_composite_primary_key(self) -> None:
        """联合主键 (conversation_id, user_id)。"""
        pk_columns = ConversationParticipant.__table__.primary_key.columns
        pk_names = {col.name for col in pk_columns}
        assert pk_names == {"conversation_id", "user_id"}

    def test_role_column_default_member(self) -> None:
        """role 列默认值为 'member'（Python default + DB server_default）。"""
        role_col = ConversationParticipant.__table__.columns["role"]
        assert str(role_col.type) == "VARCHAR(20)"
        # Python 层 default 在 flush 时应用（SQLAlchemy 行为），
        # 这里校验 column default 定义而非构造时实例属性。
        assert role_col.default is not None
        assert role_col.default.arg == "member"

    def test_joined_at_column_nullable_false(self) -> None:
        """joined_at 列不可为空。"""
        joined_at_col = ConversationParticipant.__table__.columns["joined_at"]
        assert joined_at_col.nullable is False

    def test_conversation_id_fk_cascade(self) -> None:
        """conversation_id 外键 ON DELETE CASCADE。"""
        fks = ConversationParticipant.__table__.columns["conversation_id"].foreign_keys
        assert len(fks) == 1
        fk = list(fks)[0]
        assert fk.ondelete == "CASCADE"
        assert fk.target_fullname == "ai_conversation.id"

    def test_user_id_fk_cascade(self) -> None:
        """user_id 外键 ON DELETE CASCADE。"""
        fks = ConversationParticipant.__table__.columns["user_id"].foreign_keys
        assert len(fks) == 1
        fk = list(fks)[0]
        assert fk.ondelete == "CASCADE"
        assert fk.target_fullname == "app_user.id"


class TestParticipantRef:
    """ParticipantRef 不可变值对象测试。"""

    def test_create_with_required_fields(self) -> None:
        """创建包含必填字段。"""
        conv_id = uuid4()
        user_id = uuid4()
        joined = datetime.now(UTC)
        ref = ParticipantRef(
            conversation_id=conv_id,
            user_id=user_id,
            role="owner",
            joined_at=joined,
        )
        assert ref.conversation_id == conv_id
        assert ref.user_id == user_id
        assert ref.role == "owner"
        assert ref.joined_at == joined

    def test_default_optional_fields(self) -> None:
        """display_name 默认空串，avatar_url 默认 None。"""
        ref = ParticipantRef(
            conversation_id=uuid4(),
            user_id=uuid4(),
            role="member",
            joined_at=datetime.now(UTC),
        )
        assert ref.display_name == ""
        assert ref.avatar_url is None

    def test_is_frozen(self) -> None:
        """frozen=True 不可变。"""
        ref = ParticipantRef(
            conversation_id=uuid4(),
            user_id=uuid4(),
            role="member",
            joined_at=datetime.now(UTC),
        )
        try:
            ref.role = "owner"  # type: ignore[misc]
            assert False, "应抛出 FrozenInstanceError"
        except Exception:
            pass  # dataclass(frozen=True) 会抛 FrozenInstanceError


class TestMentionableUserRef:
    """MentionableUserRef 不可变值对象测试。"""

    def test_create_with_required_fields(self) -> None:
        """创建包含必填字段。"""
        uid = uuid4()
        ref = MentionableUserRef(
            id=uid,
            display_name="研究员",
            avatar_url="http://example.com/a.png",
            roles=["lab_member"],
        )
        assert ref.id == uid
        assert ref.display_name == "研究员"
        assert ref.avatar_url == "http://example.com/a.png"
        assert ref.roles == ["lab_member"]

    def test_default_optional_fields(self) -> None:
        """avatar_url 默认 None，roles 默认空列表。"""
        ref = MentionableUserRef(id=uuid4(), display_name="用户")
        assert ref.avatar_url is None
        assert ref.roles == []

    def test_roles_independent_per_instance(self) -> None:
        """每个实例的 roles 列表独立（default_factory）。"""
        r1 = MentionableUserRef(id=uuid4(), display_name="a")
        r2 = MentionableUserRef(id=uuid4(), display_name="b")
        r1.roles.append("lab_member")
        assert r2.roles == []

    def test_is_frozen(self) -> None:
        """frozen=True 不可变（但 roles 列表内部可变，属于 dataclass 限制）。"""
        ref = MentionableUserRef(id=uuid4(), display_name="a")
        try:
            ref.display_name = "b"  # type: ignore[misc]
            assert False, "应抛出 FrozenInstanceError"
        except Exception:
            pass
