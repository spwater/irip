"""L2 事实值对象。

定义服务返回的事实引用值对象：
- FactRef: 事实引用（服务返回值），替代原 FactRevisionRef。

所有值对象均为 frozen dataclass，符合不可变值对象设计约定。
"""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class FactRef:
    """事实引用（服务返回值），替代原 FactRevisionRef。

    Attributes:
        fact_id: 事实 UUID。
        fact_type: 事实类型。
        subject_id: 主体标识。
        status: 事实状态（active / superseded / withdrawn / archived）。
    """

    fact_id: UUID
    fact_type: str
    subject_id: str
    status: str
