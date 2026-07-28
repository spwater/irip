"""实验对象类型字典 ORM。"""

from datetime import datetime

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from packages.common.database import Base
from packages.common.db_types import GUID, UTCDateTime
from packages.common.ids import new_id


class ObjectTypeDict(Base):
    """实验对象类型字典（对应 object_type_dict 表）。

    code 不可变唯一键，display_name 可改中文名。
    industrial_object.object_type 存的是 code，改名不影响关联。
    """

    __tablename__ = "object_type_dict"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(sa.Text, nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"ObjectTypeDict(code={self.code!r}, display_name={self.display_name!r})"
