"""研究域请求/响应数据类。

全部为 frozen dataclass，用于 Service 层与 API 路由之间的数据传递。
不包含 ORM 实体引用，确保层间解耦。

参照 packages/facts/observations.py 的 FactRef / FactDetailRow 模式。
"""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class CreateWorkspaceCommand:
    """创建工作空间命令。

    Attributes:
        name: 工作空间名称。
        question_text: 主研究问题文本。
    """

    name: str
    question_text: str


@dataclass(frozen=True)
class WorkspaceRef:
    """工作空间引用（列表/创建/分叉响应）。

    Attributes:
        workspace_id: 工作空间 UUID。
        name: 工作空间名称。
        status: 状态（draft / archived）。
        current_question_version: 当前问题版本号。
        forked_from_id: 分叉来源 ID（可选）。
    """

    workspace_id: UUID
    name: str
    status: str
    current_question_version: int
    forked_from_id: UUID | None = None


@dataclass(frozen=True)
class QuestionVersionRef:
    """研究问题版本引用。

    Attributes:
        version_id: 版本 UUID。
        workspace_id: 工作空间 UUID。
        version_number: 版本号。
        question_text: 主研究问题文本。
        sub_questions: 子问题列表。
    """

    version_id: UUID
    workspace_id: UUID
    version_number: int
    question_text: str
    sub_questions: list[str] = field(default_factory=list)


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
    """工作空间详情（含当前问题版本 + 证据数 + 快照数）。

    Attributes:
        workspace_id: 工作空间 UUID。
        name: 工作空间名称。
        status: 状态。
        current_question: 当前问题版本引用。
        evidence_count: 活跃证据引用数。
        snapshots: 快照引用列表。
    """

    workspace_id: UUID
    name: str
    status: str
    current_question: QuestionVersionRef | None
    evidence_count: int
    snapshots: list[SnapshotRef] = field(default_factory=list)


# ============================================================
# 阶段 3：研究产物数据类
# ============================================================

from enum import Enum


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

    metadata: dict = field(default_factory=dict)
    points: list = field(default_factory=list)
    series: list = field(default_factory=list)


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
    field_manifest: list[dict] = field(default_factory=list)


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
    current_version_data: dict | None = None


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
    metadata_content: dict
    points_content: list
    series_content: list
    field_manifest: list
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
    current_version_info: dict | None = None


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
    current_version_data: dict | None = None


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
    evidence_refs: list
    method_refs: list
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
    preview_data: dict
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
    preview_data: dict


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
