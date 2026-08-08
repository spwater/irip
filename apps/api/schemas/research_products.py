"""研究产物 API 的 Pydantic 请求/响应模型。

从 apps/api/routers/research_products.py 提取（P2-C5）。
"""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

# ---- DerivedDataset ----


class CreateDatasetRequest(BaseModel):
    """创建 DerivedDataset 请求。"""

    artifact_id: UUID
    name: str
    summary: str | None = None
    tags: list[str] = Field(default_factory=list)


class UpdateDatasetMetadataRequest(BaseModel):
    """编辑 DerivedDataset 元数据请求。"""

    name: str | None = None
    summary: str | None = None
    tags: list[str] | None = None


class DatasetResponse(BaseModel):
    dataset_id: UUID
    name: str
    status: str
    current_version: int
    workspace_id: UUID


# ---- ResearchView ----


class CreateViewRequest(BaseModel):
    """创建 ResearchView 请求。"""

    artifact_id: UUID
    name: str
    caption: str | None = None
    display_order: int = 0


class UpdateViewMetadataRequest(BaseModel):
    """编辑 ResearchView 元数据请求。"""

    name: str | None = None
    caption: str | None = None
    display_order: int | None = None


class ViewResponse(BaseModel):
    view_id: UUID
    name: str
    status: str
    current_version: int
    caption: str | None = None
    display_order: int = 0


# ---- Insight ----


class UpdateInsightMetadataRequest(BaseModel):
    """编辑 Insight 元数据请求。"""

    name: str


class InsightResponse(BaseModel):
    insight_id: UUID
    name: str
    status: str
    current_version: int


# ---- 候选产物 ----


class ModifyCandidateRequest(BaseModel):
    """修改 Insight 候选请求。"""

    conclusion: str | None = None
    scope: str | None = None
    evidence_refs: list[dict[str, Any]] | None = None
    method_refs: list[dict[str, Any]] | None = None
    confidence_level: str | None = None
    limitations: str | None = None
    evidence_source_label: str | None = None
    modification_note: str


class RejectCandidateRequest(BaseModel):
    """拒绝 Insight 候选请求。"""

    reason: str | None = None
