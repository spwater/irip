"""审计记录器：仅 INSERT，不提供 UPDATE/DELETE。

AuditRecorder.record(session, event) 将 AuditEventData 转换为
AuditEvent ORM 对象并 INSERT 到 audit_event 表。

设计约束（docs/arch-v0.md §3.1 第 292 行）：
  "应用角色 REVOKE UPDATE, DELETE ON audit_event；仅 INSERT + SELECT。"
数据库层面通过 REVOKE 保证仅追加；应用层面 AuditRecorder 不暴露
任何修改/删除方法。
"""

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from packages.audit.events import AuditEvent, AuditEventData


class AuditRecorder:
    """审计记录器（仅 INSERT）。

    线程安全：无实例状态，所有方法为静态方法。
    事务由调用方管理（session_scope 或显式 begin/commit）。
    """

    @staticmethod
    async def record(session: AsyncSession, event: AuditEventData) -> AuditEvent:
        """将审计事件 INSERT 到 audit_event 表。

        Args:
            session: 数据库异步会话（由调用方管理事务）。
            event: 审计事件数据（payload 应已脱敏）。

        Returns:
            AuditEvent: 已插入的 ORM 对象（含生成的 id 和 occurred_at）。
        """
        audit_event = AuditEvent(
            actor_user_id=event.actor_user_id,
            department_id=event.department_id,
            action=event.action,
            resource_type=event.resource_type,
            resource_id=event.resource_id,
            payload=event.payload,
            ip=event.ip,
            user_agent=event.user_agent,
        )
        session.add(audit_event)
        await session.flush()
        return audit_event


class AuditQueryRepository:
    """审计事件查询仓库（仅 SELECT）。

    提供按多条件过滤的游标分页查询，供 router 层调用（ORM 查询已从 router 下沉）。
    线程安全：无实例状态，所有方法为静态方法。
    """

    @staticmethod
    async def list_events(
        session: AsyncSession,
        *,
        object_id: UUID | None = None,
        object_type: str | None = None,
        user_id: UUID | None = None,
        action: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        cursor_dt: datetime | None = None,
        limit: int = 50,
    ) -> list[AuditEvent]:
        """按多条件过滤查询审计事件（游标分页，时间倒序）。

        Args:
            session: 数据库异步会话。
            object_id: 资源 ID 过滤。
            object_type: 资源类型过滤。
            user_id: 操作者 ID 过滤。
            action: 动作过滤。
            start_date: 起始日期。
            end_date: 截止日期。
            cursor_dt: 分页游标（上一页最后一条的 occurred_at）。
            limit: 每页数量。

        Returns:
            list[AuditEvent]: 审计事件列表（多查 1 条用于判断 has_more）。
        """
        conditions: list[sa.ColumnExpressionArgument[bool]] = []

        if object_id is not None:
            conditions.append(AuditEvent.resource_id == object_id)
        if object_type is not None:
            conditions.append(AuditEvent.resource_type == object_type)
        if user_id is not None:
            conditions.append(AuditEvent.actor_user_id == user_id)
        if action is not None:
            conditions.append(AuditEvent.action == action)
        if start_date is not None:
            conditions.append(AuditEvent.occurred_at >= start_date)
        if end_date is not None:
            conditions.append(AuditEvent.occurred_at <= end_date)
        if cursor_dt is not None:
            conditions.append(AuditEvent.occurred_at < cursor_dt)

        stmt = (
            sa.select(AuditEvent)
            .where(*conditions)
            .order_by(AuditEvent.occurred_at.desc())
            .limit(limit + 1)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())
