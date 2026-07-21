"""审计记录器：仅 INSERT，不提供 UPDATE/DELETE。

AuditRecorder.record(session, event) 将 AuditEventData 转换为
AuditEvent ORM 对象并 INSERT 到 audit_event 表。

设计约束（docs/arch-v0.md §3.1 第 292 行）：
  "应用角色 REVOKE UPDATE, DELETE ON audit_event；仅 INSERT + SELECT。"
数据库层面通过 REVOKE 保证仅追加；应用层面 AuditRecorder 不暴露
任何修改/删除方法。
"""

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
            organization_id=event.organization_id,
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
