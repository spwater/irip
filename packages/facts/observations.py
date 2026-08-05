"""L2 事实值对象。

定义服务返回的事实引用值对象：
- FactRef: 事实引用（服务返回值），替代原 FactRevisionRef。
- FactDetailRow: 读投影（list/search/search-data/get-detail 返回）。
- FactMeta: 写侧元数据（delete 前置查询）。
- FactSnapshotRow: 仓储内部行（fetch_snapshots 返回元素）。

所有值对象均为 frozen dataclass（或 NamedTuple），符合不可变值对象设计约定。
"""

from dataclasses import dataclass
from datetime import datetime
from typing import NamedTuple
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


@dataclass(frozen=True)
class FactDetailRow:
    """读投影——list / search / search-data / get-detail 返回。

    Attributes:
        fact_id: 事实 UUID。
        fact_type: 事实类型。
        subject_id: 主体标识。
        status: 事实状态。
        task_code: 任务编码快照。
        task_name: 任务名称（coalesce 后的 display_name）。
        project_name: 项目名称（仅 list_facts_detail 填充）。
        department_name: 部门名称快照。
        operator: 操作人快照。
        run_operator: 运行操作人快照。
        equipment_name: 设备名快照。
        data_summary: 数据摘要（list / search-data 填充）。
        created_at: 创建时间。
    """

    fact_id: UUID
    fact_type: str
    subject_id: str
    status: str
    task_code: str | None = None
    task_name: str | None = None
    project_name: str | None = None
    department_name: str | None = None
    operator: str | None = None
    run_operator: str | None = None
    equipment_name: str | None = None
    data_summary: str | None = None
    created_at: datetime | None = None


@dataclass(frozen=True)
class FactMeta:
    """写侧元数据——delete 前置查询。

    Attributes:
        fact_id: 事实 UUID。
        source_artifact_id: 源工件 ID（可选，用于 MinIO 删除）。
        department_id: 所属部门 ID（用于归属校验，get_facts_meta_by_task 时为 None）。
        owner_user_id: 所有者用户 ID（用于归属校验，get_facts_meta_by_task 时为 None）。
        flow_run_id: 流程运行 ID（可选，用于删除关联 FlowRun）。
    """

    fact_id: UUID
    source_artifact_id: UUID | None
    department_id: UUID | None
    owner_user_id: UUID | None
    flow_run_id: UUID | None


class FactSnapshotRow(NamedTuple):
    """仓储内部行——fetch_snapshots 返回元素。

    统一的快照 JOIN 结果，包含基础字段（fact_type / subject_id / status，
    仅 include_base=True 时填充）和富化字段（task_code / task_name /
    department_name / operator / run_operator / equipment_name / created_at）。
    project_name 仅 include_project=True 时填充。

    Attributes:
        fact_id: 事实 UUID。
        fact_type: 事实类型（include_base=True 时填充，否则 None）。
        subject_id: 主体标识（include_base=True 时填充，否则 None）。
        status: 事实状态（include_base=True 时填充，否则 None）。
        task_code: 任务编码快照。
        task_name: 任务名称（coalesce 后的 display_name）。
        project_name: 项目名称（include_project=True 时填充，否则 None）。
        department_name: 部门名称快照。
        operator: 操作人快照。
        run_operator: 运行操作人快照。
        equipment_name: 设备名快照。
        created_at: 创建时间。
    """

    fact_id: UUID
    fact_type: str | None
    subject_id: str | None
    status: str | None
    task_code: str | None
    task_name: str | None
    project_name: str | None
    department_name: str | None
    operator: str | None
    run_operator: str | None
    equipment_name: str | None
    created_at: datetime | None
