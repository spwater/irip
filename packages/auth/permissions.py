"""RBAC 权限定义：5 个内置角色与权限矩阵。

定义平台级角色：
- RoleCode 枚举：5 个角色代码常量；
- Permission 常量：所有权限字符串；
- BUILTIN_ROLES：code → {display_name, permissions} 映射；
- Role ORM 模型：对应 role 表（id UUID PK, code TEXT UNIQUE, display_name, permissions JSONB）。

权限矩阵设计原则：
  platform_administrator 拥有全部权限（含 user:manage, role:assign）；
  platform_auditor 拥有平台级只读权限；
  lab_director 拥有实验室管理 + 全部实验操作权限；
  lab_member 拥有实验操作权限（不含管理）；
  lab_viewer 拥有实验资源只读权限。
"""

from enum import StrEnum
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from packages.common.database import Base
from packages.common.db_types import GUID
from packages.common.ids import new_id


class RoleCode(StrEnum):
    """内置角色代码枚举（5 个）。"""

    PLATFORM_ADMINISTRATOR = "platform_administrator"
    PLATFORM_AUDITOR = "platform_auditor"
    LAB_DIRECTOR = "lab_director"
    LAB_MEMBER = "lab_member"
    LAB_VIEWER = "lab_viewer"


class Permission:
    """权限字符串常量。

    命名约定：``<resource>:<operation>``，如 ``fact:read``、``artifact:download``。
    """

    # 用户与角色管理
    USER_MANAGE: str = "user:manage"
    ROLE_ASSIGN: str = "role:assign"

    # 标准
    STANDARD_READ: str = "standard:read"
    STANDARD_WRITE: str = "standard:write"
    STANDARD_PUBLISH: str = "standard:publish"

    # 事实数据
    FACT_READ: str = "fact:read"
    FACT_WRITE: str = "fact:write"

    # 制品
    ARTIFACT_READ: str = "artifact:read"
    ARTIFACT_UPLOAD: str = "artifact:upload"
    ARTIFACT_DOWNLOAD: str = "artifact:download"

    # 作业
    JOB_READ: str = "job:read"
    JOB_SUBMIT: str = "job:submit"
    JOB_CANCEL: str = "job:cancel"

    # 模型
    MODEL_READ: str = "model:read"
    MODEL_MANAGE: str = "model:manage"
    MODEL_WRITE: str = "model:write"
    MODEL_PUBLISH: str = "model:publish"
    MODEL_PREDICT: str = "model:predict"

    # 参数审核与发布
    PARAMETER_READ: str = "parameter:read"
    PARAMETER_WRITE: str = "parameter:write"
    PARAMETER_REVIEW: str = "parameter:review"
    PARAMETER_APPROVE: str = "parameter:approve"
    PARAMETER_PUBLISH: str = "parameter:publish"

    # 机构/实验室管理（新增）
    DEPARTMENT_MANAGE: str = "department:manage"
    DEPARTMENT_READ: str = "department:read"

    # 设备仪器管理
    EQUIPMENT_MANAGE: str = "equipment:manage"
    EQUIPMENT_READ: str = "equipment:read"

    # 数据导入与映射（IRIP Task 13）
    INGESTION_READ: str = "ingestion:read"
    INGESTION_WRITE: str = "ingestion:write"
    INGESTION_PUBLISH: str = "ingestion:publish"

    # 溯源与推导（IRIP Task 17）
    PROVENANCE_READ: str = "provenance:read"
    PROVENANCE_WRITE: str = "provenance:write"
    PROVENANCE_PUBLISH: str = "provenance:publish"

    # 组件管理（IRIP V2-T01）
    COMPONENT_MANAGE: str = "component:manage"
    COMPONENT_READ: str = "component:read"

    # 流程引擎（IRIP V2-T03）
    FLOW_MANAGE: str = "flow:manage"
    FLOW_EXECUTE: str = "flow:execute"
    FLOW_READ: str = "flow:read"

    # AI 助手（IRIP V3-T01）
    ASSISTANT_USE: str = "assistant:use"

    # 审计（IRIP V3-T02）
    AUDIT_READ: str = "audit:read"

    # 系统健康监控（IRIP V3-T02）
    SYSTEM_HEALTH: str = "system:health"

    # 系统管理：备份/恢复等系统级运维操作（IRIP V3-T03）
    SYSTEM_MANAGE: str = "system:manage"

    @classmethod
    def all(cls) -> list[str]:
        """返回所有权限常量列表。"""
        return [
            cls.USER_MANAGE,
            cls.ROLE_ASSIGN,
            cls.STANDARD_READ,
            cls.STANDARD_WRITE,
            cls.STANDARD_PUBLISH,
            cls.FACT_READ,
            cls.FACT_WRITE,
            cls.ARTIFACT_READ,
            cls.ARTIFACT_UPLOAD,
            cls.ARTIFACT_DOWNLOAD,
            cls.JOB_READ,
            cls.JOB_SUBMIT,
            cls.JOB_CANCEL,
            cls.MODEL_READ,
            cls.MODEL_MANAGE,
            cls.MODEL_WRITE,
            cls.MODEL_PUBLISH,
            cls.MODEL_PREDICT,
            cls.PARAMETER_READ,
            cls.PARAMETER_WRITE,
            cls.PARAMETER_REVIEW,
            cls.PARAMETER_APPROVE,
            cls.PARAMETER_PUBLISH,
            cls.DEPARTMENT_MANAGE,
            cls.DEPARTMENT_READ,
            cls.EQUIPMENT_MANAGE,
            cls.EQUIPMENT_READ,
            cls.INGESTION_READ,
            cls.INGESTION_WRITE,
            cls.INGESTION_PUBLISH,
            cls.PROVENANCE_READ,
            cls.PROVENANCE_WRITE,
            cls.PROVENANCE_PUBLISH,
            cls.COMPONENT_MANAGE,
            cls.COMPONENT_READ,
            cls.FLOW_MANAGE,
            cls.FLOW_EXECUTE,
            cls.FLOW_READ,
            cls.ASSISTANT_USE,
            cls.AUDIT_READ,
            cls.SYSTEM_HEALTH,
            cls.SYSTEM_MANAGE,
        ]


#: 全部权限列表（便于 platform_administrator 引用）。
_ALL_PERMISSIONS: list[str] = Permission.all()


#: 内置角色定义：code → {display_name, permissions}。
BUILTIN_ROLES: dict[str, dict[str, object]] = {
    RoleCode.PLATFORM_ADMINISTRATOR.value: {
        "display_name": "平台管理员",
        "permissions": list(_ALL_PERMISSIONS),
    },
    RoleCode.PLATFORM_AUDITOR.value: {
        "display_name": "平台监督员",
        "permissions": [
            Permission.STANDARD_READ,
            Permission.FACT_READ,
            Permission.ARTIFACT_READ,
            Permission.JOB_READ,
            Permission.MODEL_READ,
            Permission.PARAMETER_READ,
            Permission.DEPARTMENT_READ,
            Permission.EQUIPMENT_READ,
            Permission.INGESTION_READ,
            Permission.PROVENANCE_READ,
            Permission.COMPONENT_READ,
            Permission.FLOW_READ,
            Permission.AUDIT_READ,
            Permission.SYSTEM_HEALTH,
        ],
    },
    RoleCode.LAB_DIRECTOR.value: {
        "display_name": "实验室负责人",
        "permissions": [
            Permission.STANDARD_READ,
            Permission.STANDARD_WRITE,
            Permission.STANDARD_PUBLISH,
            Permission.FACT_READ,
            Permission.FACT_WRITE,
            Permission.ARTIFACT_READ,
            Permission.ARTIFACT_UPLOAD,
            Permission.ARTIFACT_DOWNLOAD,
            Permission.JOB_READ,
            Permission.JOB_SUBMIT,
            Permission.JOB_CANCEL,
            Permission.MODEL_READ,
            Permission.MODEL_MANAGE,
            Permission.MODEL_WRITE,
            Permission.MODEL_PUBLISH,
            Permission.MODEL_PREDICT,
            Permission.PARAMETER_READ,
            Permission.PARAMETER_WRITE,
            Permission.PARAMETER_REVIEW,
            Permission.PARAMETER_APPROVE,
            Permission.PARAMETER_PUBLISH,
            Permission.DEPARTMENT_MANAGE,
            Permission.DEPARTMENT_READ,
            Permission.EQUIPMENT_MANAGE,
            Permission.EQUIPMENT_READ,
            Permission.INGESTION_READ,
            Permission.INGESTION_WRITE,
            Permission.INGESTION_PUBLISH,
            Permission.PROVENANCE_READ,
            Permission.PROVENANCE_WRITE,
            Permission.PROVENANCE_PUBLISH,
            Permission.COMPONENT_MANAGE,
            Permission.COMPONENT_READ,
            Permission.FLOW_MANAGE,
            Permission.FLOW_EXECUTE,
            Permission.FLOW_READ,
            Permission.ASSISTANT_USE,
        ],
    },
    RoleCode.LAB_MEMBER.value: {
        "display_name": "实验室成员",
        "permissions": [
            Permission.FACT_READ,
            Permission.FACT_WRITE,
            Permission.ARTIFACT_READ,
            Permission.ARTIFACT_UPLOAD,
            Permission.ARTIFACT_DOWNLOAD,
            Permission.JOB_READ,
            Permission.JOB_SUBMIT,
            Permission.JOB_CANCEL,
            Permission.MODEL_READ,
            Permission.MODEL_PREDICT,
            Permission.PARAMETER_READ,
            Permission.PARAMETER_WRITE,
            Permission.DEPARTMENT_READ,
            Permission.EQUIPMENT_READ,
            Permission.INGESTION_READ,
            Permission.INGESTION_WRITE,
            Permission.PROVENANCE_READ,
            Permission.PROVENANCE_WRITE,
            Permission.COMPONENT_READ,
            Permission.FLOW_EXECUTE,
            Permission.FLOW_READ,
            Permission.ASSISTANT_USE,
        ],
    },
    RoleCode.LAB_VIEWER.value: {
        "display_name": "实验室成员（只读）",
        "permissions": [
            Permission.STANDARD_READ,
            Permission.FACT_READ,
            Permission.ARTIFACT_READ,
            Permission.JOB_READ,
            Permission.MODEL_READ,
            Permission.PARAMETER_READ,
            Permission.DEPARTMENT_READ,
            Permission.EQUIPMENT_READ,
            Permission.INGESTION_READ,
            Permission.PROVENANCE_READ,
            Permission.COMPONENT_READ,
            Permission.FLOW_READ,
            Permission.ASSISTANT_USE,
        ],
    },
}


class Role(Base):
    """角色实体（对应 role 表）。

    内置 5 个角色，由迁移种子数据插入。permissions 字段为 JSONB 数组，
    存储该角色拥有的权限字符串列表（如 ``["fact:read", "fact:write"]``）。

    Attributes:
        id: 角色 UUID。
        code: 角色代码（UNIQUE），如 ``platform_administrator``。
        display_name: 中文显示名。
        permissions: 权限列表（JSONB 数组）。
    """

    __tablename__ = "role"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(sa.Text, unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    permissions: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=sa.text("'[]'::jsonb")
    )

    def __repr__(self) -> str:
        return f"Role(code={self.code!r}, display_name={self.display_name!r})"


def get_role_permissions(role_code: str) -> list[str]:
    """获取内置角色的权限列表。

    Args:
        role_code: 角色代码（如 ``"lab_member"``）。

    Returns:
        list[str]: 权限字符串列表。未知角色返回空列表。
    """
    role_def = BUILTIN_ROLES.get(role_code)
    if role_def is None:
        return []
    permissions = role_def["permissions"]
    if isinstance(permissions, list):
        return [str(p) for p in permissions]
    return []


def has_role_permission(role_code: str, action: str) -> bool:
    """检查内置角色是否拥有指定权限。

    Args:
        role_code: 角色代码。
        action: 权限字符串（如 ``"fact:read"``）。

    Returns:
        bool: 角色拥有该权限返回 True。
    """
    return action in get_role_permissions(role_code)
