"""IRIP 审计包。

Phase V0 T05: 仅追加审计日志（append-only audit）。

提供：
- AuditEventData: 审计事件数据载体（frozen dataclass）；
- AuditEvent: 审计事件 ORM 模型（对应 audit_event 表）；
- redact: 敏感字段脱敏函数；
- AuditRecorder: 审计记录器（仅 INSERT，无 UPDATE/DELETE）。

安全约束（docs/arch-v0.md §3.1 第 280-292 行）：
  应用角色 irip_app 对 audit_event 仅拥有 INSERT + SELECT 权限，
  UPDATE/DELETE 在迁移中通过 REVOKE 撤销，实现数据库级仅追加保证。
"""

from packages.audit.events import AuditEvent, AuditEventData
from packages.audit.redaction import redact
from packages.audit.repository import AuditRecorder

__all__ = [
    "AuditEvent",
    "AuditEventData",
    "AuditRecorder",
    "redact",
]
