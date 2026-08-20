"""研究域请求/响应数据类。

全部为 frozen dataclass，用于 Service 层与 API 路由之间的数据传递。
不包含 ORM 实体引用，确保层间解耦。

参照 packages/facts/observations.py 的 FactRef / FactDetailRow 模式。
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class CreateWorkspaceCommand:
    """创建工作空间命令。

    Timeline refactoring: 只需要名称，不再需要 question_text。
    进入工作空间后先加数据、确认快照，然后 AI 推荐问题。

    Attributes:
        name: 工作空间名称。
    """

    name: str


@dataclass(frozen=True)
class WorkspaceRef:
    """工作空间引用（列表/创建响应）。

    Timeline refactoring: 移除 current_question_version 和 forked_from_id，
    新增 latest_snapshot_number、turn_count 和 active_run_status。

    Attributes:
        workspace_id: 工作空间 UUID。
        name: 工作空间名称。
        status: 状态（draft / archived）。
        latest_snapshot_number: 最新快照编号（None 表示无快照）。
        turn_count: 研究轮次数。
        active_run_status: 活跃 Run 状态（None 表示无运行任务）。
    """

    workspace_id: UUID
    name: str
    status: str
    latest_snapshot_number: int | None = None
    turn_count: int = 0
    active_run_status: str | None = None


@dataclass(frozen=True)
class EvidenceRefDTO:
    """证据引用 DTO。

    Attributes:
        ref_id: 引用 UUID。
        source_namespace: 源命名空间（如 "core:fact"）。
        source_id: 源对象 UUID。
        source_version: 源版本快照（可选）。
        source_name: 源名称快照（可选）。
        status: 状态（active / removed）。
    """

    ref_id: UUID
    source_namespace: str
    source_id: UUID
    source_version: str | None
    source_name: str | None
    status: str


@dataclass(frozen=True)
class SnapshotRef:
    """证据快照引用。

    Attributes:
        snapshot_id: 快照 UUID。
        snapshot_number: 快照编号。
        content_hash: 内容哈希（SHA-256）。
        captured_at: 冻结时间。
    """

    snapshot_id: UUID
    snapshot_number: int
    content_hash: str
    captured_at: datetime


@dataclass(frozen=True)
class FactSummary:
    """Fact 摘要（CoreFactProvider 返回）。

    研究域通过 CoreFactProvider 获取 Fact 摘要，不暴露完整数据内容。
    用于权限校验和证据引用展示。

    Attributes:
        fact_id: Fact UUID。
        fact_type: 事实类型（experiment_run / simulation_run 等）。
        subject_id: 主体标识。
        status: 状态（active / archived 等）。
        department_name: 部门名称（可选）。
    """

    fact_id: UUID
    fact_type: str
    subject_id: str
    status: str
    department_name: str | None = None


@dataclass(frozen=True)
class WorkspaceDetail:
    """工作空间详情。

    Timeline refactoring: 移除 current_question，新增 turn_count 和 active_run_status。

    Attributes:
        workspace_id: 工作空间 UUID。
        name: 工作空间名称。
        status: 状态。
        evidence_count: 活跃证据引用数。
        snapshots: 快照引用列表。
        latest_snapshot_number: 最新快照编号（None 表示无快照）。
        turn_count: 研究轮次数。
        active_run_status: 活跃 Run 状态（None 表示无运行任务）。
    """

    workspace_id: UUID
    name: str
    status: str
    evidence_count: int
    snapshots: list[SnapshotRef] = field(default_factory=list)
    latest_snapshot_number: int | None = None
    turn_count: int = 0
    active_run_status: str | None = None


# ============================================================
# 阶段 3：研究产物数据类
# ============================================================


class EvidenceSourceLabel(Enum):
    """Insight 证据来源标签枚举。"""

    EXPERIMENTAL_DATA = "experimental_data"
    KNOWLEDGE_BASE = "knowledge_base"
    MODEL_INFERENCE = "model_inference"


class CandidateStatus(Enum):
    """Insight 候选状态枚举。"""

    PENDING = "pending"
    ACCEPTED = "accepted"
    MODIFIED = "modified"
    REJECTED = "rejected"


class ProductType(Enum):
    """产物类型枚举。"""

    DERIVED_DATASET = "derived_dataset"
    VIEW = "view"
    INSIGHT = "insight"


@dataclass(frozen=True)
class ThreeSegmentData:
    """三段式数据（metadata / points / series）。

    Attributes:
        metadata: 报告级描述（dict）。
        points: 独立单值指标列表（list of {name, value, unit}）。
        series: 普通表格/时间序列列表（list of {name, columns, rows}）。
    """

    metadata: dict[str, Any] = field(default_factory=dict[str, Any])
    points: list[Any] = field(default_factory=list[Any])
    series: list[Any] = field(default_factory=list[Any])


@dataclass(frozen=True)
class FieldManifestEntry:
    """字段清单条目。

    Attributes:
        field_name: 字段名。
        inferred_type: 推断类型（int/float/str/bool/null）。
        unit: 单位（可选）。
        description: 一句话说明（首期为空）。
        source_step: 来源步骤（可选）。
        column_order: 列顺序。
        shape: 基本形状（如 "12x4"）。
    """

    field_name: str
    inferred_type: str = "null"
    unit: str = ""
    description: str = ""
    source_step: str = ""
    column_order: int = 0
    shape: str = ""


@dataclass(frozen=True)
class ValidationResult:
    """三段式校验结果。

    Attributes:
        valid: 是否通过校验。
        errors: 错误信息列表。
        data: 解析后的三段式数据。
        field_manifest: 推断的字段清单。
    """

    valid: bool
    errors: list[str] = field(default_factory=list)
    data: ThreeSegmentData | None = None
    field_manifest: list[dict[str, Any]] = field(default_factory=list[Any])


@dataclass(frozen=True)
class DerivedDatasetRef:
    """衍生数据集引用。

    Attributes:
        dataset_id: 数据集 UUID。
        name: 名称。
        status: 状态。
        current_version: 当前版本号。
        workspace_id: 工作空间 ID。
    """

    dataset_id: UUID
    name: str
    status: str
    current_version: int
    workspace_id: UUID


@dataclass(frozen=True)
class DatasetVersionRef:
    """衍生数据集版本引用。

    Attributes:
        version_id: 版本 UUID。
        dataset_id: 数据集 UUID。
        version_number: 版本号。
        content_hash: 内容哈希。
        created_at: 创建时间。
    """

    version_id: UUID
    dataset_id: UUID
    version_number: int
    content_hash: str
    created_at: datetime


@dataclass(frozen=True)
class DatasetDetail:
    """衍生数据集详情。

    Attributes:
        dataset_id: 数据集 UUID。
        workspace_id: 工作空间 ID。
        name: 名称。
        summary: 摘要。
        tags: 标签列表。
        status: 状态。
        current_version: 当前版本号。
        source_run_id: 来源 Run ID。
        source_snapshot_id: 来源快照 ID。
        current_version_data: 当前版本的三段式数据 + field_manifest（可空）。
    """

    dataset_id: UUID
    workspace_id: UUID
    name: str
    summary: str | None
    tags: list[str]
    status: str
    current_version: int
    source_run_id: UUID
    source_snapshot_id: UUID | None
    current_version_data: dict[str, Any] | None = None


@dataclass(frozen=True)
class DatasetVersionDetail:
    """衍生数据集版本详情。

    Attributes:
        version_id: 版本 UUID。
        dataset_id: 数据集 UUID。
        version_number: 版本号。
        metadata_content: 报告级描述。
        points_content: 独立单值指标列表。
        series_content: 普通表格/时间序列列表。
        field_manifest: 字段清单。
        content_hash: 内容哈希。
        source_run_id / source_step_id / source_artifact_id: 来源引用。
        created_at: 创建时间。
    """

    version_id: UUID
    dataset_id: UUID
    version_number: int
    metadata_content: dict[str, Any]
    points_content: list[Any]
    series_content: list[Any]
    field_manifest: list[Any]
    content_hash: str
    source_run_id: UUID
    source_step_id: UUID | None
    source_artifact_id: UUID | None
    created_at: datetime


@dataclass(frozen=True)
class ViewRef:
    """研究视图引用。

    Attributes:
        view_id: 视图 UUID。
        name: 名称。
        status: 状态。
        current_version: 当前版本号。
        caption: 图注。
        display_order: 展示顺序。
    """

    view_id: UUID
    name: str
    status: str
    current_version: int
    caption: str | None = None
    display_order: int = 0


@dataclass(frozen=True)
class ViewVersionRef:
    """研究视图版本引用。

    Attributes:
        version_id: 版本 UUID。
        view_id: 视图 UUID。
        version_number: 版本号。
        image_storage_path: 图片存储路径。
        image_format: 图片格式。
        created_at: 创建时间。
    """

    version_id: UUID
    view_id: UUID
    version_number: int
    image_storage_path: str
    image_format: str
    created_at: datetime


@dataclass(frozen=True)
class ViewDetail:
    """研究视图详情。

    Attributes:
        view_id: 视图 UUID。
        workspace_id: 工作空间 ID。
        name: 名称。
        caption: 图注。
        display_order: 展示顺序。
        status: 状态。
        current_version: 当前版本号。
        source_run_id: 来源 Run ID。
        current_version_info: 当前版本详情（可空）。
    """

    view_id: UUID
    workspace_id: UUID
    name: str
    caption: str | None
    display_order: int
    status: str
    current_version: int
    source_run_id: UUID
    current_version_info: dict[str, Any] | None = None


@dataclass(frozen=True)
class ViewVersionDetail:
    """研究视图版本详情。

    Attributes:
        version_id: 版本 UUID。
        view_id: 视图 UUID。
        version_number: 版本号。
        image_storage_path: 图片存储路径。
        image_format: 图片格式。
        image_width / image_height: 图片尺寸。
        image_content_hash: 图片内容哈希。
        chart_code_artifact_id: 绘图代码工件 ID。
        image_digest: 沙箱镜像 digest。
        source_run_id / source_step_id / source_artifact_id: 来源引用。
        bound_dataset_version_id: 绑定数据集版本 ID。
        chart_description: 图表说明。
        created_at: 创建时间。
    """

    version_id: UUID
    view_id: UUID
    version_number: int
    image_storage_path: str
    image_format: str
    image_width: int | None
    image_height: int | None
    image_content_hash: str
    chart_code_artifact_id: UUID | None
    image_digest: str | None
    source_run_id: UUID
    source_step_id: UUID | None
    source_artifact_id: UUID | None
    bound_dataset_version_id: UUID | None
    chart_description: str | None
    created_at: datetime


@dataclass(frozen=True)
class InsightRef:
    """Insight 引用。

    Attributes:
        insight_id: Insight UUID。
        name: 名称。
        status: 状态。
        current_version: 当前版本号。
    """

    insight_id: UUID
    name: str
    status: str
    current_version: int


@dataclass(frozen=True)
class InsightVersionRef:
    """Insight 版本引用。

    Attributes:
        version_id: 版本 UUID。
        insight_id: Insight UUID。
        version_number: 版本号。
        is_modified: 是否被修改。
        created_at: 创建时间。
    """

    version_id: UUID
    insight_id: UUID
    version_number: int
    is_modified: bool
    created_at: datetime


@dataclass(frozen=True)
class InsightDetail:
    """Insight 详情。

    Attributes:
        insight_id: Insight UUID。
        workspace_id: 工作空间 ID。
        name: 名称。
        status: 状态。
        current_version: 当前版本号。
        source_run_id: 来源 Run ID。
        current_version_data: 当前版本的结构化字段（可空）。
    """

    insight_id: UUID
    workspace_id: UUID
    name: str
    status: str
    current_version: int
    source_run_id: UUID | None
    current_version_data: dict[str, Any] | None = None


@dataclass(frozen=True)
class InsightCandidateRef:
    """Insight 候选引用。

    Attributes:
        candidate_id: 候选 UUID。
        run_id: Run UUID。
        step_id: 步骤 UUID（可空）。
        status: 状态。
        conclusion: 结论。
        evidence_source_label: 证据来源标签。
        created_at: 创建时间。
    """

    candidate_id: UUID
    run_id: UUID
    step_id: UUID | None
    status: str
    conclusion: str
    evidence_source_label: str
    created_at: datetime


@dataclass(frozen=True)
class InsightCandidateData:
    """Insight 候选提取结果（从 LLM 响应解析）。

    Attributes:
        conclusion: 结论。
        scope: 适用范围。
        evidence_refs: 证据引用。
        method_refs: 方法引用。
        confidence_level: 置信说明。
        limitations: 限制条件。
        evidence_source_label: 证据来源标签。
        ai_raw_text: AI 原始回答文本。
        extraction_failed: 提取是否失败（解析失败时为 true，ai_raw_text 保留原文）。
    """

    conclusion: str
    scope: str
    evidence_refs: list[Any]
    method_refs: list[Any]
    confidence_level: str
    limitations: str
    evidence_source_label: str
    ai_raw_text: str
    extraction_failed: bool = False


@dataclass(frozen=True)
class CandidateProductSummary:
    """候选产物摘要。

    Attributes:
        candidate_type: 候选类型（derived_dataset / view / insight）。
        source_artifact_id: 来源工件 ID（可空，insight 候选无工件）。
        candidate_id: 候选 ID（insight 候选的 ID，data/chart 候选使用 artifact_id）。
        source_run_id: 来源 Run ID。
        source_step_id: 来源步骤 ID。
        step_name: 步骤名称。
        step_status: 步骤状态。
        preview_data: 预览数据（三段式摘要 / 图表元数据 / Insight 字段）。
        status: 候选状态（available / unavailable / pending）。
        error_reason: 不可用原因（校验失败时附带）。
    """

    candidate_type: str
    source_artifact_id: UUID | None
    candidate_id: UUID
    source_run_id: UUID
    source_step_id: UUID | None
    step_name: str
    step_status: str
    preview_data: dict[str, Any]
    status: str
    error_reason: str = ""


@dataclass(frozen=True)
class CandidateDetail:
    """候选产物详情。

    Attributes:
        candidate_type: 候选类型。
        candidate_id: 候选 ID。
        source_run_id: 来源 Run ID。
        source_step_id: 来源步骤 ID。
        preview_data: 完整预览数据。
    """

    candidate_type: str
    candidate_id: UUID
    source_run_id: UUID
    source_step_id: UUID | None
    preview_data: dict[str, Any]


@dataclass(frozen=True)
class ProductSummary:
    """产物列表条目。

    Attributes:
        product_type: 产物类型（derived_dataset / view / insight）。
        product_id: 产物 UUID。
        name: 名称。
        status: 状态。
        current_version: 当前版本号。
    """

    product_type: str
    product_id: UUID
    name: str
    status: str
    current_version: int


# ============================================================
# 阶段 4：发布与复用数据类
# ============================================================


class AclType(Enum):
    """成果包 ACL 类型枚举。

    ACL 严格度排序（rank 越高越宽松）：
    private(0) < explicit(1) < tree(2) < all(3)
    """

    PRIVATE = "private"
    EXPLICIT = "explicit"
    TREE = "tree"
    ALL = "all"


class ResultVersionStatus(Enum):
    """成果包版本状态枚举。"""

    ACTIVE = "active"
    SUPERSEDED = "superseded"
    WITHDRAWN = "withdrawn"


class LineageEdgeType(Enum):
    """溯源边类型枚举。"""

    WORKSPACE_TO_RESULT = "workspace_to_result"
    DATASET_TO_RESULT = "dataset_to_result"
    VIEW_TO_RESULT = "view_to_result"
    INSIGHT_TO_RESULT = "insight_to_result"


class ViewMode(Enum):
    """发布成果页视图模式枚举。"""

    ALL = "all"
    MINE = "mine"
    FAVORITES = "favorites"


@dataclass(frozen=True)
class PublishRequest:
    """发布请求。

    Attributes:
        title: 成果包标题。
        summary: 摘要（可选）。
        tags: 标签列表。
        release_notes: 发布说明（可选）。
        dataset_ids: 要发布的 DerivedDataset ID 列表。
        view_ids: 要发布的 ResearchView ID 列表。
        insight_ids: 要发布的 Insight ID 列表。
        requested_acl: 请求的 ACL 类型。
        explicit_user_ids: explicit 模式下指定用户列表。
        is_declassify: 是否为 declassify 操作。
        declassify_reason: declassify 理由。
    """

    title: str
    summary: str = ""
    tags: list[str] = field(default_factory=list)
    release_notes: str = ""
    dataset_ids: list[UUID] = field(default_factory=list)
    view_ids: list[UUID] = field(default_factory=list)
    insight_ids: list[UUID] = field(default_factory=list)
    requested_acl: str = "private"
    explicit_user_ids: list[UUID] = field(default_factory=list)
    is_declassify: bool = False
    declassify_reason: str = ""


@dataclass(frozen=True)
class PermissionEnvelope:
    """权限包络。

    Attributes:
        acl_type: 交集 ACL 类型（最严格的 ACL）。
        explicit_user_ids: explicit 模式下有效用户列表。
        source_details: 源数据权限详情列表。
    """

    acl_type: str
    explicit_user_ids: list[UUID] = field(default_factory=list)
    source_details: list[dict[str, Any]] = field(default_factory=list[Any])


@dataclass(frozen=True)
class EnvelopeValidationResult:
    """权限包络校验结果。

    Attributes:
        valid: 是否通过校验。
        effective_acl: 有效 ACL 类型。
        reason: 校验失败原因。
        limiting_sources: 限制源的权限详情。
    """

    valid: bool
    effective_acl: str = "private"
    reason: str = ""
    limiting_sources: list[dict[str, Any]] = field(default_factory=list[Any])


@dataclass(frozen=True)
class ProductRefCollection:
    """产物引用集合（发布时收集）。

    Attributes:
        dataset_version_refs: DerivedDataset 版本引用列表。
        view_version_refs: ResearchView 版本引用列表。
        insight_version_refs: Insight 版本引用列表。
        evidence_snapshot_ids: Evidence Snapshot ID 列表（去重）。
        analysis_run_ids: Analysis Run ID 列表（去重）。
        source_run_statuses: Run 状态映射 {run_id: status}。
    """

    dataset_version_refs: list[dict[str, Any]] = field(default_factory=list[Any])
    view_version_refs: list[dict[str, Any]] = field(default_factory=list[Any])
    insight_version_refs: list[dict[str, Any]] = field(default_factory=list[Any])
    evidence_snapshot_ids: list[str] = field(default_factory=list)
    analysis_run_ids: list[str] = field(default_factory=list)
    source_run_statuses: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ResultRef:
    """成果包引用。

    Attributes:
        result_id: 成果包 UUID。
        name: 成果包名称。
        status: 状态。
        current_version: 当前版本号。
        current_acl_type: 当前 ACL 类型。
    """

    result_id: UUID
    name: str
    status: str
    current_version: int
    current_acl_type: str
    workspace_id: UUID | None = None


@dataclass(frozen=True)
class ResultVersionRef:
    """成果包版本引用。

    Attributes:
        result_id: 成果包 UUID。
        version_number: 版本号。
        title: 标题。
        status: 版本状态。
        published_at: 发布时间。
    """

    result_id: UUID
    version_number: int
    title: str
    status: str
    published_at: datetime


@dataclass(frozen=True)
class ResultVersionDetail:
    """成果包版本详情。

    Attributes:
        result_id: 成果包 UUID。
        version_number: 版本号。
        title: 标题。
        summary: 摘要。
        tags: 标签列表。
        release_notes: 发布说明。
        dataset_version_refs: DerivedDataset 版本引用列表。
        view_version_refs: ResearchView 版本引用列表。
        insight_version_refs: Insight 版本引用列表。
        evidence_snapshot_ids: Evidence Snapshot ID 列表。
        analysis_run_ids: Analysis Run ID 列表。
        source_run_statuses: Run 状态映射。
        publisher: 发布者 ID。
        published_at: 发布时间。
        content_hash: 内容哈希。
        published_permission_envelope: 发布时权限包络快照。
        status: 版本状态。
    """

    result_id: UUID
    version_number: int
    title: str
    summary: str
    tags: list[str]
    release_notes: str
    dataset_version_refs: list[dict[str, Any]]
    view_version_refs: list[dict[str, Any]]
    insight_version_refs: list[dict[str, Any]]
    evidence_snapshot_ids: list[str]
    analysis_run_ids: list[str]
    source_run_statuses: dict[str, str]
    publisher: UUID
    published_at: datetime
    content_hash: str
    published_permission_envelope: dict[str, Any]
    status: str


@dataclass(frozen=True)
class ResultDetail:
    """成果包详情。

    Attributes:
        result_ref: 成果包引用。
        current_version: 当前版本详情。
        version_history: 版本历史列表。
        acl_revisions: ACL 变更记录列表。
        is_favorited: 当前用户是否已收藏。
    """

    result_ref: ResultRef
    current_version: ResultVersionDetail | None
    version_history: list[ResultVersionRef]
    acl_revisions: list["AclRevisionRef"]
    is_favorited: bool = False


@dataclass(frozen=True)
class AclRevisionRef:
    """ACL 修订记录引用。

    Attributes:
        revision_number: 修订号。
        acl_type: ACL 类型。
        explicit_user_ids: 指定用户列表。
        previous_acl_type: 变更前 ACL 类型。
        previous_explicit_user_ids: 变更前指定用户列表。
        changed_by: 变更者 ID。
        changed_at: 变更时间。
        change_reason: 变更原因。
        is_declassify: 是否为 declassify 操作。
        declassify_reason: declassify 理由。
    """

    revision_number: int
    acl_type: str
    explicit_user_ids: list[str]
    previous_acl_type: str | None
    previous_explicit_user_ids: list[str] | None
    changed_by: UUID
    changed_at: datetime
    change_reason: str
    is_declassify: bool
    declassify_reason: str | None


@dataclass(frozen=True)
class SearchResultItem:
    """搜索结果条目。

    Attributes:
        result_id: 成果包 UUID。
        name: 成果包名称。
        title: 当前版本标题。
        summary: 摘要。
        tags: 标签列表。
        publisher: 发布者 ID。
        published_at: 发布时间。
        current_version: 当前版本号。
        current_acl_type: 当前 ACL 类型。
        dataset_count: 数据集数量。
        view_count: 图表数量。
        insight_count: Insight 数量。
        workspace_id: 来源 Workspace ID。
    """

    result_id: UUID
    name: str
    title: str
    summary: str
    tags: list[str]
    publisher: UUID
    published_at: datetime
    current_version: int
    current_acl_type: str
    dataset_count: int
    view_count: int
    insight_count: int
    workspace_id: UUID


@dataclass(frozen=True)
class SearchResultPage:
    """搜索结果分页。

    Attributes:
        items: 搜索结果列表。
        total: 总数。
        page: 当前页码。
        page_size: 每页数量。
    """

    items: list[SearchResultItem]
    total: int
    page: int
    page_size: int


@dataclass(frozen=True)
class LineageEdgeRef:
    """溯源边引用。

    Attributes:
        source_namespace: 源命名空间。
        source_id: 源对象 UUID。
        source_version: 源版本号。
        target_namespace: 目标命名空间。
        target_id: 目标对象 UUID。
        target_version: 目标版本号。
        edge_type: 边类型。
    """

    source_namespace: str
    source_id: UUID
    source_version: int | None
    target_namespace: str
    target_id: UUID
    target_version: int | None
    edge_type: str


@dataclass(frozen=True)
class PublishPreviewResult:
    """发布预览结果。

    Attributes:
        product_refs: 产物引用集合。
        envelope: 权限包络。
        validation: 权限校验结果。
    """

    product_refs: ProductRefCollection
    envelope: PermissionEnvelope
    validation: EnvelopeValidationResult


# ============================================================
# 阶段 5：统一溯源与知识接口数据类
# ============================================================


class ProvenanceNamespace(Enum):
    """溯源节点命名空间枚举。

    命名空间格式: ``{domain}:{node_type}``，用于跨域唯一标识节点。
    """

    CORE_FACT = "core:fact"
    CORE_DERIVATION_RUN = "core:derivation_run"
    CORE_EVIDENCE_SET = "core:evidence_set"
    RESEARCH_EVIDENCE_SNAPSHOT = "research:evidence_snapshot"
    RESEARCH_ANALYSIS_RUN = "research:analysis_run"
    RESEARCH_ANALYSIS_STEP = "research:analysis_step"
    RESEARCH_DERIVED_DATASET = "research:derived_dataset"
    RESEARCH_DERIVED_DATASET_VERSION = "research:derived_dataset_version"
    RESEARCH_VIEW = "research:view"
    RESEARCH_VIEW_VERSION = "research:view_version"
    RESEARCH_INSIGHT = "research:insight"
    RESEARCH_INSIGHT_VERSION = "research:insight_version"
    RESEARCH_RESULT_VERSION = "research:result_version"
    RESEARCH_WORKSPACE = "research:workspace"
    RESEARCH_KNOWLEDGE_REFERENCE = "research:knowledge_reference"
    RESEARCH_DATASET_VERSION = "research:dataset_version"
    RESTRICTED = "restricted"


class EdgeType(Enum):
    """溯源边类型枚举（含阶段 4 已有 + 阶段 5 新增）。"""

    WORKSPACE_TO_RESULT = "workspace_to_result"
    DATASET_TO_RESULT = "dataset_to_result"
    VIEW_TO_RESULT = "view_to_result"
    INSIGHT_TO_RESULT = "insight_to_result"
    FACT_TO_SNAPSHOT = "fact_to_snapshot"
    PUBLISHED_DERIVED_TO_SNAPSHOT = "published_derived_to_snapshot"
    SNAPSHOT_TO_RUN = "snapshot_to_run"
    RUN_TO_STEP = "run_to_step"
    RUN_TO_DATASET = "run_to_dataset"
    RUN_TO_VIEW = "run_to_view"
    RUN_TO_INSIGHT = "run_to_insight"
    KNOWLEDGE_REF_TO_INSIGHT = "knowledge_ref_to_insight"


@dataclass(frozen=True)
class NodeDisplayLabel:
    """节点展示标签。

    Attributes:
        display_label: 展示名称（如 "拉曼样品#12 的实验事实"）。
        node_type_label: 类型标签（如 "实验事实"）。
        version_summary: 版本摘要（如 "v3" 或 "快照 #2"）。
        namespace: 命名空间（如 "core:fact"）。
        icon: 图标 emoji。
        jump_target: 跳转目标 URL（受限节点为 None）。
    """

    display_label: str
    node_type_label: str
    version_summary: str
    namespace: str
    icon: str
    jump_target: str | None


@dataclass(frozen=True)
class ProvenanceNode:
    """溯源节点。

    Attributes:
        namespace: 命名空间（如 "core:fact" / "research:insight"）。
        node_id: 节点 UUID。
        version: 版本号（可空）。
        node_type: 节点类型（如 "fact" / "insight" / "restricted"）。
        display_label: 展示标签。
        attributes: 展示属性字典（不含敏感数据）。
        is_restricted: 是否为受限占位节点。
    """

    namespace: str
    node_id: UUID
    version: int | None
    node_type: str
    display_label: NodeDisplayLabel | None
    attributes: dict[str, Any]
    is_restricted: bool = False


@dataclass(frozen=True)
class ProvenanceEdge:
    """溯源边。

    Attributes:
        source_namespace: 源命名空间。
        source_id: 源节点 UUID。
        source_version: 源版本号（可空）。
        target_namespace: 目标命名空间。
        target_id: 目标节点 UUID。
        target_version: 目标版本号（可空）。
        edge_type: 边类型。
        edge_type_label: 边类型展示标签。
    """

    source_namespace: str
    source_id: UUID
    source_version: int | None
    target_namespace: str
    target_id: UUID
    target_version: int | None
    edge_type: str
    edge_type_label: str


@dataclass(frozen=True)
class RestrictedNode:
    """受限占位节点。

    无权访问的节点替换为受限占位，不含名称/ID/属性/内容。
    临时 ID 每次查询重新生成（不可枚举）。

    Attributes:
        node_type: 固定为 "restricted"。
        display_label: 固定为 "受限来源"。
        attributes: 空字典。
        temp_id: 临时 ID（如 "restricted_0"）。
    """

    node_type: str
    display_label: str
    attributes: dict[str, Any]
    temp_id: str


@dataclass(frozen=True)
class ProvenanceGraphStats:
    """溯源图统计信息。

    Attributes:
        total_nodes: 总节点数。
        nodes_by_type: 按类型分组的节点数。
        restricted_nodes_count: 受限节点数。
        truncated_count: 被截断的分支数。
    """

    total_nodes: int
    nodes_by_type: dict[str, Any]
    restricted_nodes_count: int
    truncated_count: int


@dataclass(frozen=True)
class ProvenanceGraph:
    """溯源图。

    Attributes:
        nodes: 节点列表。
        edges: 边列表。
        stats: 统计信息。
    """

    nodes: list[ProvenanceNode]
    edges: list[ProvenanceEdge]
    stats: ProvenanceGraphStats


@dataclass(frozen=True)
class ProvenanceQueryOptions:
    """溯源查询选项。

    Attributes:
        max_depth: 最大追溯深度（默认 20）。
        truncate_branch: 无权节点是否截断整个上游分支（默认 False）。
        layout: 前端布局类型（如 "dagre" / "force"，默认 "dagre"）。
    """

    max_depth: int = 20
    truncate_branch: bool = False
    layout: str = "dagre"


@dataclass(frozen=True)
class KnowledgeSearchResult:
    """知识库检索结果。

    Attributes:
        document_id: 文档 ID。
        document_version: 文档版本。
        title: 文档标题。
        section: 段落/章节（可空）。
        page: 页码（可空）。
        chunk_id: 分块 ID（可空）。
        relevance_score: 相关性评分（0.0 ~ 1.0）。
        source_uri: 来源 URI。
        content_hash: 内容哈希（SHA-256）。
        snippet: 引用段落文本。
    """

    document_id: str
    document_version: str
    title: str
    section: str = ""
    page: int = 0
    chunk_id: str = ""
    relevance_score: float = 0.0
    source_uri: str = ""
    content_hash: str = ""
    snippet: str = ""


@dataclass(frozen=True)
class KnowledgeDocument:
    """文档元数据。

    Attributes:
        document_id: 文档 ID。
        document_version: 文档版本。
        title: 文档标题。
        source_uri: 来源 URI。
    """

    document_id: str
    document_version: str
    title: str
    source_uri: str


@dataclass(frozen=True)
class KnowledgeSearchOptions:
    """知识库检索选项。

    Attributes:
        max_results: 最大返回结果数。
        filter_tags: 过滤标签列表。
        timeout: 超时时间（秒）。
    """

    max_results: int = 10
    filter_tags: list[str] = field(default_factory=list)
    timeout: int = 30


@dataclass(frozen=True)
class KnowledgeReferenceRef:
    """知识引用快照引用。

    Attributes:
        reference_id: 引用快照 UUID。
        workspace_id: 工作空间 ID。
        run_id: Run ID。
        step_id: 步骤 ID（可空）。
        insight_id: Insight ID（可空）。
        document_id: 文档 ID。
        document_version: 文档版本。
        title: 文档标题。
        content_hash: 内容哈希。
        source_uri: 来源 URI。
        retrieval_time: 检索时间。
        provider_name: Provider 名称。
    """

    reference_id: UUID
    workspace_id: UUID
    run_id: UUID
    step_id: UUID | None
    insight_id: UUID | None
    document_id: str
    document_version: str
    title: str
    content_hash: str
    source_uri: str
    retrieval_time: datetime
    provider_name: str


@dataclass(frozen=True)
class KnowledgeReferenceDetail:
    """知识引用快照详情。

    Attributes:
        ref: 引用快照引用（基础信息）。
        snippet_text: 引用段落文本（需 research:manage 权限，普通用户为空）。
        section: 段落/章节。
        page: 页码。
        chunk_id: 分块 ID。
        research_question_context: 检索时的研究问题上下文。
    """

    ref: KnowledgeReferenceRef
    snippet_text: str
    section: str
    page: int
    chunk_id: str
    research_question_context: str
