"""可信执行请求/响应数据类与枚举（阶段 2 新增）。

全部为 frozen dataclass 或 Enum，用于 Service 层与 API 路由之间的数据传递。
不包含 ORM 实体引用，确保层间解耦。

参照 packages/research/models.py 的 frozen dataclass 模式。
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

# ============================================================
# 枚举定义
# ============================================================


class RunStatus(Enum):
    """Run 状态枚举。"""

    QUEUED = "queued"
    PLANNING = "planning"
    RUNNING = "running"
    PARTIALLY_SUCCEEDED = "partially_succeeded"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(Enum):
    """步骤状态枚举。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class PlanStatus(Enum):
    """计划状态枚举。"""

    DRAFT = "draft"
    CONFIRMED = "confirmed"
    SUPERSEDED = "superseded"


class AnalysisMode(Enum):
    """分析模式枚举。"""

    FULL_COMPUTE = "full_compute"
    CHUNKED_FULL_SCAN = "chunked_full_scan"
    DIRECT_FULL_CONTEXT = "direct_full_context"
    RETRIEVAL = "retrieval"
    MIXED = "mixed"


class TaskType(Enum):
    """模型任务类型枚举。"""

    PLANNING = "planning"
    CODE_GEN = "code_gen"
    LONG_CONTEXT = "long_context"
    INSIGHT = "insight"
    CONVERSATION = "conversation"


class ChunkStrategy(Enum):
    """分块策略枚举。"""

    TOKEN_BUDGET = "token_budget"
    RECORD_COUNT = "record_count"
    BUSINESS_LOGIC = "business_logic"


class ErrorClassification(Enum):
    """错误分类枚举。"""

    SYNTAX_ERROR = "syntax_error"
    DEPENDENCY_ERROR = "dependency_error"
    TIMEOUT = "timeout"
    RESOURCE_EXCEEDED = "resource_exceeded"
    PERMISSION_DENIED = "permission_denied"
    MODEL_ERROR = "model_error"
    WORKER_CRASHED = "worker_crashed"
    UNKNOWN = "unknown"


# ============================================================
# DAG 步骤与计划
# ============================================================


@dataclass(frozen=True)
class PlanStep:
    """DAG 步骤定义（计划中的单步骤）。

    Attributes:
        step_key: 步骤唯一键。
        question: 步骤要回答的问题。
        evidence_refs: 证据引用 ID 列表。
        method: 执行方式（python / llm / knowledge / mixed）。
        strategy: 策略（full / chunked / sampled）。
        expected_output: 预期输出描述。
        risks: 风险列表。
        dependencies: 依赖步骤 key 列表。
        requires_full: 是否要求全量。
        per_record_semantic: 是否需逐条语义阅读。
        cross_record_reasoning: 是否存在跨记录推理。
        allows_sampling: 是否允许抽样。
        estimated_tokens: 预估 token 数。
        resource_tier: 资源档位（standard / heavy）。
    """

    step_key: str
    question: str
    evidence_refs: list[str] = field(default_factory=list)
    method: str = "python"
    strategy: str = "full"
    expected_output: str = ""
    risks: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    requires_full: bool = True
    per_record_semantic: bool = False
    cross_record_reasoning: bool = False
    allows_sampling: bool = False
    estimated_tokens: int = 0
    resource_tier: str = "standard"


@dataclass(frozen=True)
class DagStructure:
    """DAG 结构（包含步骤列表）。

    Attributes:
        steps: 步骤列表。
    """

    steps: list[PlanStep] = field(default_factory=list)


@dataclass(frozen=True)
class PlanVersionRef:
    """计划版本引用。

    Attributes:
        plan_id: 计划版本 UUID。
        workspace_id: 工作空间 UUID。
        version_number: 版本号。
        status: 状态（draft / confirmed / superseded）。
        step_count: 步骤数。
    """

    plan_id: UUID
    workspace_id: UUID
    version_number: int
    status: str
    step_count: int = 0


@dataclass(frozen=True)
class PlanDetail:
    """计划详情（含完整 DAG 步骤 + 覆盖声明）。

    Attributes:
        plan_id: 计划版本 UUID。
        workspace_id: 工作空间 UUID。
        version_number: 版本号。
        status: 状态。
        dag_structure: DAG 结构（JSON dict）。
        coverage_declaration: 覆盖声明（JSON dict）。
        created_at: 创建时间。
        confirmed_at: 确认时间（可选）。
    """

    plan_id: UUID
    workspace_id: UUID
    version_number: int
    status: str
    dag_structure: dict[str, Any]
    coverage_declaration: dict[str, Any] | None = None
    created_at: datetime | None = None
    confirmed_at: datetime | None = None


# ============================================================
# Run 与步骤
# ============================================================


@dataclass(frozen=True)
class RunRef:
    """Run 引用。

    Attributes:
        run_id: Run UUID。
        workspace_id: 工作空间 UUID。
        run_number: Run 编号。
        status: 状态。
        queue_position: 排队位置（排队中时有值）。
    """

    run_id: UUID
    workspace_id: UUID
    run_number: int
    status: str
    queue_position: int | None = None


@dataclass(frozen=True)
class RunProgress:
    """Run 进度（含步骤状态列表 + 覆盖声明）。

    Attributes:
        run_id: Run UUID。
        status: Run 状态。
        total_steps: 总步骤数。
        completed_steps: 已完成步骤数。
        steps: 步骤进度列表。
        coverage_declaration: 覆盖声明。
        started_at: 开始时间。
        completed_at: 完成时间。
    """

    run_id: UUID
    status: str
    total_steps: int
    completed_steps: int
    steps: list["StepProgress"] = field(default_factory=list)
    coverage_declaration: "CoverageDeclaration | None" = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


@dataclass(frozen=True)
class StepProgress:
    """步骤进度。

    Attributes:
        step_id: 步骤 UUID。
        step_key: 步骤键。
        step_index: 步骤序号。
        status: 状态。
        method: 执行方式。
        analysis_mode: 分析模式。
        coverage_rate: 数据覆盖率。
        llm_read_rate: LLM 阅读率。
        is_sampled: 是否抽样。
        attempt_count: 尝试次数。
        error_message: 错误消息。
    """

    step_id: UUID
    step_key: str
    step_index: int
    status: str
    method: str
    analysis_mode: str | None = None
    coverage_rate: float | None = None
    llm_read_rate: float | None = None
    is_sampled: bool = False
    attempt_count: int = 0
    error_message: str | None = None


# ============================================================
# 覆盖声明
# ============================================================


@dataclass(frozen=True)
class CoverageDeclaration:
    """覆盖声明。

    数据覆盖率与 LLM 阅读率独立计算，不混为一谈。
    数据覆盖率 = Python 全量计算的数据覆盖率。
    LLM 阅读率 = LLM 逐条语义阅读的记录比例。

    Attributes:
        analysis_mode: 分析模式。
        data_coverage_rate: 数据覆盖率（0.0 ~ 1.0）。
        llm_read_rate: LLM 阅读率（0.0 ~ 1.0）。
        is_sampled: 是否抽样。
        batch_count: 分块总数（None = 非分块）。
        batch_progress: 当前批次（None = 非分块）。
        mode_reason: 模式选择原因。
    """

    analysis_mode: str
    data_coverage_rate: float
    llm_read_rate: float
    is_sampled: bool = False
    batch_count: int | None = None
    batch_progress: int | None = None
    mode_reason: str = ""

    def to_display_string(self) -> str:
        """生成用户可读的覆盖声明字符串。

        Returns:
            str: 如 "自动模式: 混合分析 | 数据覆盖率 100% | LLM 阅读率 75% | 是否抽样: 否"
        """
        mode_labels = {
            "full_compute": "全量计算",
            "chunked_full_scan": "分块全量扫描",
            "direct_full_context": "直接全量上下文",
            "retrieval": "检索探索",
            "mixed": "混合分析",
        }
        mode_label = mode_labels.get(self.analysis_mode, self.analysis_mode)
        sampling_label = "是" if self.is_sampled else "否"
        return (
            f"自动模式: {mode_label} | "
            f"数据覆盖率 {int(self.data_coverage_rate * 100)}% | "
            f"LLM 阅读率 {int(self.llm_read_rate * 100)}% | "
            f"是否抽样: {sampling_label}"
        )

    def to_dict(self) -> dict[str, Any]:
        """转为 JSONB 可存储的字典。"""
        return {
            "analysis_mode": self.analysis_mode,
            "data_coverage_rate": self.data_coverage_rate,
            "llm_read_rate": self.llm_read_rate,
            "is_sampled": self.is_sampled,
            "batch_count": self.batch_count,
            "batch_progress": self.batch_progress,
            "mode_reason": self.mode_reason,
        }


# ============================================================
# 工件
# ============================================================


@dataclass(frozen=True)
class ArtifactRef:
    """工件引用。

    Attributes:
        artifact_id: 工件 UUID。
        run_id: Run UUID。
        step_id: 步骤 UUID（可选）。
        artifact_type: 工件类型。
        artifact_key: 工件键名。
        storage_path: MinIO 存储路径。
        content_hash: 内容哈希。
        size_bytes: 文件大小。
        is_publishable: 是否可发布。
        created_at: 创建时间。
    """

    artifact_id: UUID
    run_id: UUID
    step_id: UUID | None
    artifact_type: str
    artifact_key: str
    storage_path: str
    content_hash: str | None = None
    size_bytes: int | None = None
    is_publishable: bool = False
    created_at: datetime | None = None


@dataclass(frozen=True)
class ArtifactContent:
    """工件内容。

    Attributes:
        artifact_id: 工件 UUID。
        artifact_type: 工件类型。
        artifact_key: 工件键名。
        content: 文件内容（bytes）。
        content_hash: 内容哈希。
    """

    artifact_id: UUID
    artifact_type: str
    artifact_key: str
    content: bytes
    content_hash: str | None = None


# ============================================================
# 对话
# ============================================================


@dataclass(frozen=True)
class ConversationMessage:
    """对话消息。

    Attributes:
        message_id: 消息 UUID。
        workspace_id: 工作空间 UUID。
        role: 角色（user / assistant / system）。
        content: 消息内容（dict，含 text / code_blocks / plan_ref / artifact_refs）。
        run_id: 关联的 Run UUID（可选）。
        created_at: 创建时间。
    """

    message_id: UUID
    workspace_id: UUID
    role: str
    content: dict[str, Any]
    run_id: UUID | None = None
    created_at: datetime | None = None


# ============================================================
# 排队
# ============================================================


@dataclass(frozen=True)
class QueuePosition:
    """排队位置。

    Attributes:
        position: 队列位置（从 1 开始）。
        ahead_count: 前方用户数。
        estimated_wait_seconds: 预计等待秒数。
    """

    position: int
    ahead_count: int
    estimated_wait_seconds: int


# ============================================================
# 沙箱值对象
# ============================================================


@dataclass(frozen=True)
class ResourceLimits:
    """沙箱资源限制。

    Attributes:
        cpu_count: CPU 核心数。
        memory_mb: 内存限制（MB）。
        timeout_seconds: 超时秒数。
        disk_gb: 临时磁盘限制（GB）。
        output_size_mb: 输出大小限制（MB）。
    """

    cpu_count: float = 2.0
    memory_mb: int = 4096
    timeout_seconds: int = 1200
    disk_gb: int = 10
    output_size_mb: int = 100


@dataclass(frozen=True)
class ExecutionResult:
    """沙箱执行结果。

    Attributes:
        exit_code: 退出码（0 = 成功）。
        stdout: 标准输出。
        stderr: 标准错误。
        timed_out: 是否超时。
        duration_seconds: 执行时长（秒）。
    """

    exit_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    duration_seconds: int = 0


@dataclass(frozen=True)
class OutputFile:
    """沙箱输出文件。

    Attributes:
        filename: 文件名。
        content: 文件内容（bytes）。
        content_hash: 内容哈希（SHA-256）。
        size_bytes: 文件大小（字节）。
    """

    filename: str
    content: bytes
    content_hash: str = ""
    size_bytes: int = 0


# ============================================================
# 计划范围边界
# ============================================================


@dataclass(frozen=True)
class ScopeBoundary:
    """计划范围边界（确认后的计划记录）。

    用于检测越界行为：新增数据/改变目标/首次知识库/扩大资源需重新确认。

    Attributes:
        snapshot_id: 快照 ID。
        question_version: 研究问题版本号。
        methods_allowed: 允许的方法集合。
        resource_tier: 资源档位。
        knowledge_base_used: 是否已使用知识库。
    """

    snapshot_id: UUID
    question_version: int
    methods_allowed: set[str] = field(default_factory=lambda: {"python", "llm", "mixed"})
    resource_tier: str = "standard"
    knowledge_base_used: bool = False


@dataclass(frozen=True)
class ScopeCheckResult:
    """范围检查结果。

    Attributes:
        is_within_scope: 是否在范围内。
        violation_type: 越界类型（如 snapshot_changed / question_changed
        / knowledge_first_use / resource_upgraded）。
        message: 描述消息。
    """

    is_within_scope: bool
    violation_type: str = ""
    message: str = ""


# ============================================================
# 模型网关值对象
# ============================================================


@dataclass(frozen=True)
class ModelConfig:
    """模型配置。

    Attributes:
        provider: 供应商（如 openai）。
        model: 模型名称（如 gpt-4o）。
        version: 模型版本（如 2024-08）。
        context_limit: 上下文窗口限制（token 数）。
    """

    provider: str
    model: str
    version: str
    context_limit: int = 128000


@dataclass(frozen=True)
class ModelResponse:
    """模型调用响应。

    Attributes:
        answer: 回答文本。
        provider: 供应商。
        model: 模型名称。
        model_version: 模型版本。
        prompt_version: 提示词版本。
        tool_version: 工具版本。
        tokens_used: 使用的 token 数。
        tool_calls: 工具调用列表。
        uncertainty: 不确定性说明。
        failover_used: 是否使用了故障切换。
    """

    answer: str
    provider: str = ""
    model: str = ""
    model_version: str = ""
    prompt_version: str = ""
    tool_version: str = ""
    tokens_used: int = 0
    tool_calls: list[dict[str, Any]] = field(default_factory=list[Any])
    uncertainty: str | None = None
    failover_used: bool = False


# ============================================================
# 数据 Profile（PlanService 使用）
# ============================================================


@dataclass(frozen=True)
class DataProfile:
    """证据快照数据 Profile（AI 检查数据结构与质量）。

    Attributes:
        snapshot_id: 快照 ID。
        total_records: 总记录数。
        total_tokens_estimate: 预估总 token 数。
        field_manifest: 字段清单（{fact_id: [field_names]}）。
        source_count: 数据源数量。
        data_summary: 数据摘要文本（传给 AI 的数据描述）。
    """

    snapshot_id: UUID
    total_records: int = 0
    total_tokens_estimate: int = 0
    field_manifest: dict[str, Any] = field(default_factory=dict[str, Any])
    source_count: int = 0
    data_summary: str = ""


# ============================================================
# Chunk（ContextRouter 分块结果）
# ============================================================


@dataclass(frozen=True)
class Chunk:
    """数据分块。

    Attributes:
        index: 分块索引。
        content: 分块内容。
        token_count: token 数。
        record_range: 记录范围（start, end）。
    """

    index: int
    content: str
    token_count: int = 0
    record_range: tuple[int, int] = (0, 0)


# ============================================================
# 摘要
# ============================================================


@dataclass(frozen=True)
class RunSummary:
    """Run 摘要（列表展示用）。

    Attributes:
        run_id: Run UUID。
        run_number: Run 编号。
        status: 状态。
        submitted_at: 提交时间。
        completed_at: 完成时间。
    """

    run_id: UUID
    run_number: int
    status: str
    submitted_at: datetime | None = None
    completed_at: datetime | None = None


@dataclass(frozen=True)
class StepSummary:
    """步骤摘要。

    Attributes:
        step_key: 步骤键。
        step_index: 步骤序号。
        status: 状态。
        method: 执行方式。
    """

    step_key: str
    step_index: int
    status: str
    method: str


@dataclass(frozen=True)
class PlanSummary:
    """计划摘要。

    Attributes:
        plan_id: 计划 UUID。
        version_number: 版本号。
        status: 状态。
        step_count: 步骤数。
    """

    plan_id: UUID
    version_number: int
    status: str
    step_count: int = 0


@dataclass(frozen=True)
class ArtifactSummary:
    """工件摘要。

    Attributes:
        artifact_id: 工件 UUID。
        artifact_type: 工件类型。
        artifact_key: 工件键名。
        is_publishable: 是否可发布。
    """

    artifact_id: UUID
    artifact_type: str
    artifact_key: str
    is_publishable: bool = False


@dataclass(frozen=True)
class EligibilityResult:
    """发布资格校验结果。

    Attributes:
        is_eligible: 是否可发布。
        failed_step_keys: 依赖闭包中失败的步骤 key 列表。
        source_run_partial: 源 Run 是否部分成功。
        message: 描述消息。
    """

    is_eligible: bool
    failed_step_keys: list[str] = field(default_factory=list)
    source_run_partial: bool = False
    message: str = ""
