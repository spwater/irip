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


# ---- 响应模型（list / detail） ----


class CandidateListItemResponse(BaseModel):
    """候选产物列表项。"""

    candidate_type: str
    source_artifact_id: str | None = None
    candidate_id: str
    source_run_id: str
    source_step_id: str | None = None
    step_name: str | None = None
    step_status: str | None = None
    preview_data: Any = None
    status: str | None = None
    error_reason: str | None = None


class CandidateListResponse(BaseModel):
    """候选产物列表。"""

    items: list[CandidateListItemResponse]


class CandidateDetailResponse(BaseModel):
    """候选产物详情。"""

    candidate_type: str
    candidate_id: str
    source_run_id: str
    source_step_id: str | None = None
    preview_data: Any = None


class DatasetListItemResponse(BaseModel):
    """DerivedDataset 列表项。"""

    dataset_id: str
    name: str
    status: str
    current_version: int
    workspace_id: str


class DatasetListResponse(BaseModel):
    """DerivedDataset 列表。"""

    items: list[DatasetListItemResponse]


class DatasetDetailResponse(BaseModel):
    """DerivedDataset 详情。"""

    dataset_id: str
    workspace_id: str
    name: str
    summary: str | None = None
    tags: list[str] = Field(default_factory=list)
    status: str
    current_version: int
    source_run_id: str
    source_snapshot_id: str | None = None
    current_version_data: Any = None


class DatasetVersionListItemResponse(BaseModel):
    """DerivedDataset 版本列表项。"""

    version_id: str
    dataset_id: str
    version_number: int
    content_hash: str | None = None
    created_at: str | None = None


class DatasetVersionListResponse(BaseModel):
    """DerivedDataset 版本列表。"""

    items: list[DatasetVersionListItemResponse]


class DatasetVersionDetailResponse(BaseModel):
    """DerivedDataset 版本详情。"""

    version_id: str
    dataset_id: str
    version_number: int
    metadata_content: Any = None
    points_content: Any = None
    series_content: Any = None
    field_manifest: Any = None
    content_hash: str | None = None
    source_run_id: str
    source_step_id: str | None = None
    source_artifact_id: str | None = None
    created_at: str | None = None


class ViewListItemResponse(BaseModel):
    """ResearchView 列表项。"""

    view_id: str
    name: str
    status: str
    current_version: int
    caption: str | None = None
    display_order: int = 0


class ViewListResponse(BaseModel):
    """ResearchView 列表。"""

    items: list[ViewListItemResponse]


class ViewDetailResponse(BaseModel):
    """ResearchView 详情。"""

    view_id: str
    workspace_id: str
    name: str
    caption: str | None = None
    display_order: int = 0
    status: str
    current_version: int
    source_run_id: str
    current_version_info: Any = None


class ViewVersionListItemResponse(BaseModel):
    """ResearchView 版本列表项。"""

    version_id: str
    view_id: str
    version_number: int
    image_storage_path: str | None = None
    image_format: str | None = None
    created_at: str | None = None


class ViewVersionListResponse(BaseModel):
    """ResearchView 版本列表。"""

    items: list[ViewVersionListItemResponse]


class ViewVersionDetailResponse(BaseModel):
    """ResearchView 版本详情。"""

    version_id: str
    view_id: str
    version_number: int
    image_storage_path: str | None = None
    image_format: str | None = None
    image_width: int | None = None
    image_height: int | None = None
    image_content_hash: str | None = None
    chart_code_artifact_id: str | None = None
    image_digest: str | None = None
    source_run_id: str
    source_step_id: str | None = None
    source_artifact_id: str | None = None
    bound_dataset_version_id: str | None = None
    chart_description: str | None = None
    created_at: str | None = None


class InsightListItemResponse(BaseModel):
    """Insight 列表项。"""

    insight_id: str
    name: str
    status: str
    current_version: int


class InsightListResponse(BaseModel):
    """Insight 列表。"""

    items: list[InsightListItemResponse]


class InsightDetailResponse(BaseModel):
    """Insight 详情。"""

    insight_id: str
    workspace_id: str
    name: str
    status: str
    current_version: int
    source_run_id: str | None = None
    current_version_data: Any = None


class InsightVersionListItemResponse(BaseModel):
    """Insight 版本列表项。"""

    version_id: str
    insight_id: str
    version_number: int
    is_modified: bool | None = None
    created_at: str | None = None


class InsightVersionListResponse(BaseModel):
    """Insight 版本列表。"""

    items: list[InsightVersionListItemResponse]


class InsightCandidateListItemResponse(BaseModel):
    """Insight 候选列表项。"""

    candidate_id: str
    run_id: str
    step_id: str | None = None
    status: str
    conclusion: str | None = None
    evidence_source_label: str | None = None
    created_at: str | None = None


class InsightCandidateListResponse(BaseModel):
    """Insight 候选列表。"""

    items: list[InsightCandidateListItemResponse]


class ProductListItemResponse(BaseModel):
    """产物列表项。"""

    product_type: str
    product_id: str
    name: str
    status: str
    current_version: int


class ProductListResponse(BaseModel):
    """产物列表。"""

    items: list[ProductListItemResponse]
