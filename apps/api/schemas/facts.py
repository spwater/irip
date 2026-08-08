"""Fact 相关共享响应模型。

从 apps/api/routers/facts.py 提取，供多个路由复用（P2-C6）。
"""

from pydantic import BaseModel, Field


class FactResponse(BaseModel):
    """事实响应。"""

    fact_id: str
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
    created_at: str | None = None


class FactListResponse(BaseModel):
    """事实分页列表响应。"""

    items: list[FactResponse]
    next_cursor: str | None
    group_counts: dict[str, int] = Field(
        default_factory=dict,
        description="每个 task_code 对应的事实总数（不受分页限制）",
    )
