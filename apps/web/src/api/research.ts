/**
 * 研究域 API 客户端
 *
 * 端点（阶段 1 基础 + 阶段 2 可信执行）：
 *   POST   /api/v1/research/workspaces                    — 创建工作空间
 *   GET    /api/v1/research/workspaces                     — 列表
 *   GET    /api/v1/research/workspaces/{id}                — 详情
 *   PATCH  /api/v1/research/workspaces/{id}                — 更新名称
 *   DELETE /api/v1/research/workspaces/{id}                — 删除
 *   POST   /api/v1/research/workspaces/{id}/archive        — 归档
 *   POST   /api/v1/research/workspaces/{id}/fork           — 分叉
 *   PUT    /api/v1/research/workspaces/{id}/question        — 更新研究问题
 *   POST   /api/v1/research/workspaces/{id}/evidence       — 加入证据
 *   DELETE /api/v1/research/workspaces/{id}/evidence/{refId} — 移除证据
 *   GET    /api/v1/research/workspaces/{id}/evidence        — 证据列表
 *   POST   /api/v1/research/workspaces/{id}/snapshot        — 冻结快照
 *   GET    /api/v1/research/workspaces/{id}/snapshots       — 快照列表
 *   GET    /api/v1/research/facts/search                    — 搜索 Fact
 *
 * 阶段 2 新增端点：
 *   POST   /api/v1/research/workspaces/{id}/plans                        — 生成分析计划
 *   GET    /api/v1/research/workspaces/{id}/plans                         — 列出计划
 *   GET    /api/v1/research/workspaces/{id}/plans/{planId}                — 获取计划详情
 *   POST   /api/v1/research/workspaces/{id}/plans/{planId}/confirm        — 确认计划
 *   POST   /api/v1/research/workspaces/{id}/runs                         — 提交 Run
 *   GET    /api/v1/research/workspaces/{id}/runs                          — 列出 Run
 *   GET    /api/v1/research/workspaces/{id}/runs/{runId}                  — 获取 Run 详情
 *   POST   /api/v1/research/workspaces/{id}/runs/{runId}/cancel           — 取消 Run
 *   GET    /api/v1/research/workspaces/{id}/runs/{runId}/steps            — 获取步骤状态
 *   GET    /api/v1/research/workspaces/{id}/runs/{runId}/artifacts        — 列出工件
 *   GET    /api/v1/research/workspaces/{id}/runs/{runId}/artifacts/{aid}  — 获取工件
 *   GET    /api/v1/research/workspaces/{id}/runs/{runId}/queue-status     — 排队状态
 *   GET    /api/v1/research/workspaces/{id}/runs/{runId}/events           — SSE 端点
 *   POST   /api/v1/research/workspaces/{id}/conversation                  — 发送 AI 消息
 *   GET    /api/v1/research/workspaces/{id}/conversation                   — 获取对话历史
 *
 * 风格参考 apps/web/src/api/experiment-projects.ts：纯 async 函数 + http 实例。
 */
import { http } from './client';

// ============================================================
// 类型定义 — 阶段 1
// ============================================================

export type Workspace = {
  workspace_id: string;
  name: string;
  status: string;
  latest_snapshot_number: number | null;
  turn_count: number;
  active_run_status: string | null;
};

export type WorkspaceListResponse = {
  items: Workspace[];
  next_cursor: string | null;
};

export type WorkspaceDetail = {
  workspace_id: string;
  name: string;
  status: string;
  evidence_count: number;
  snapshots: Snapshot[];
  latest_snapshot_number: number | null;
  turn_count: number;
  active_run_status: string | null;
};

export type EvidenceRef = {
  ref_id: string;
  source_namespace: string;
  source_id: string;
  source_version: string | null;
  source_name: string | null;
  status: string;
};

export type EvidenceListResponse = {
  items: EvidenceRef[];
};

/** Insight 候选产物（AI 抽取的结论候选） */
export type InsightCandidate = {
  confidence_level?: string;
  conclusion?: string;
  scope?: string;
  limitations?: string;
  evidence_source_label?: string;
  [key: string]: unknown;
};

export type Snapshot = {
  snapshot_id: string;
  snapshot_number: number;
  content_hash: string;
  captured_at: string;
};

export type SnapshotListResponse = {
  items: Snapshot[];
};

export type FactSearchItem = {
  fact_id: string;
  fact_type: string;
  subject_id: string;
  status: string;
  department_name?: string | null;
};

export type FactSearchResponse = {
  items: FactSearchItem[];
  next_cursor: string | null;
};

// ============================================================
// 类型定义 — 阶段 2 可信执行
// ============================================================

export type Plan = {
  plan_id: string;
  workspace_id: string;
  version_number: number;
  status: string;
  step_count: number;
};

export type PlanDetail = {
  plan_id: string;
  workspace_id: string;
  version_number: number;
  status: string;
  dag_structure: { steps: PlanStep[] };
  coverage_declaration: CoverageDeclaration | null;
  created_at: string | null;
  confirmed_at: string | null;
};

export type PlanStep = {
  step_key: string;
  question: string;
  evidence_refs: string[];
  method: string;
  strategy: string;
  expected_output: string;
  risks: string[];
  dependencies: string[];
  requires_full: boolean;
  per_record_semantic: boolean;
  cross_record_reasoning: boolean;
  allows_sampling: boolean;
  estimated_tokens: number;
  resource_tier: string;
  analysis_mode?: string;
  mode_reason?: string;
  data_budget_tokens?: number;
  analysis_result?: string;
  data_context?: string;
  insight_candidate?: InsightCandidate | null;
  insight_candidate_id?: string;
  insight_run_id?: string;
};

export type PlanListResponse = {
  items: Plan[];
};

export type CoverageDeclaration = {
  analysis_mode: string;
  data_coverage_rate: number;
  llm_read_rate: number;
  is_sampled: boolean;
  batch_count: number | null;
  batch_progress: number | null;
  mode_reason: string;
  /** 阶段 5 新增：知识库检索状态 */
  knowledge_search_status?: 'searched' | 'degraded' | 'not_applicable';
  /** 阶段 5 新增：已检索到的知识库文献数 */
  knowledge_reference_count?: number;
  /** 阶段 5 新增：使用的知识库 Provider 名称 */
  knowledge_provider_name?: string;
  /** 阶段 5 新增：降级原因（knowledge_search_status=degraded 时） */
  knowledge_degrade_reason?: string;
};

export type Run = {
  run_id: string;
  workspace_id: string;
  run_number: number;
  status: string;
  queue_position: number | null;
};

export type RunListResponse = {
  items: Run[];
};

export type StepProgress = {
  step_id: string;
  step_key: string;
  step_index: number;
  status: string;
  method: string;
  analysis_mode: string | null;
  coverage_rate: number | null;
  llm_read_rate: number | null;
  is_sampled: boolean;
  attempt_count: number;
  error_message: string | null;
};

export type RunProgress = {
  run_id: string;
  status: string;
  total_steps: number;
  completed_steps: number;
  steps: StepProgress[];
  coverage_declaration: CoverageDeclaration | null;
  started_at: string | null;
  completed_at: string | null;
};

export type Artifact = {
  artifact_id: string;
  run_id: string;
  step_id: string | null;
  artifact_type: string;
  artifact_key: string;
  storage_path: string;
  content_hash: string | null;
  size_bytes: number | null;
  is_publishable: boolean;
  created_at: string | null;
};

export type ArtifactListResponse = {
  items: Artifact[];
};

export type QueueStatus = {
  position: number;
  ahead_count: number;
  estimated_wait_seconds: number;
};

export type ConversationMessage = {
  message_id: string;
  workspace_id: string;
  role: string;
  content: { text: string; code_blocks?: string[]; plan_ref?: string };
  run_id: string | null;
  created_at: string | null;
};

export type ConversationListResponse = {
  items: ConversationMessage[];
};

// ============================================================
// API 函数 — 阶段 1
// ============================================================

export async function apiCreateWorkspace(body: {
  name: string;
  question_text?: string;
}): Promise<Workspace> {
  const res = await http.post<Workspace>('/research/workspaces', body);
  return res.data;
}

export async function apiListWorkspaces(params?: {
  status?: string;
  cursor?: string;
  page_size?: number;
}): Promise<WorkspaceListResponse> {
  const res = await http.get<WorkspaceListResponse>('/research/workspaces', { params });
  return res.data;
}

export async function apiGetWorkspace(id: string): Promise<WorkspaceDetail> {
  const res = await http.get<WorkspaceDetail>(`/research/workspaces/${id}`);
  return res.data;
}

export async function apiUpdateWorkspace(
  id: string,
  body: { name: string },
): Promise<Workspace> {
  const res = await http.patch<Workspace>(`/research/workspaces/${id}`, body);
  return res.data;
}

export async function apiDeleteWorkspace(id: string): Promise<void> {
  await http.delete(`/research/workspaces/${id}`);
}

export async function apiArchiveWorkspace(id: string): Promise<void> {
  await http.post(`/research/workspaces/${id}/archive`);
}

// Timeline refactoring: fork and updateQuestion APIs removed (routes deleted from backend)

export async function apiAddEvidence(
  id: string,
  body: { source_namespace: string; source_id: string },
): Promise<EvidenceRef> {
  const res = await http.post<EvidenceRef>(`/research/workspaces/${id}/evidence`, body);
  return res.data;
}

/**
 * 加入衍生数据集为证据（阶段 3 新增）
 * source_namespace = "research:derived"
 */
export async function apiAddDerivedEvidence(
  id: string,
  datasetId: string,
): Promise<EvidenceRef> {
  return apiAddEvidence(id, {
    source_namespace: 'research:derived',
    source_id: datasetId,
  });
}

/**
 * 加入已发布成果包中的 DerivedDataset 为证据（阶段 4 新增）
 * source_namespace = "research:published_derived"
 * 后端通过 ResearchCatalog 校验成果包 ACL 和版本，
 * 快照冻结时从 ResearchResultVersion 的 dataset_version_refs
 * 解析获取 DerivedDatasetVersion 的 content_hash 纳入哈希计算。
 */
export async function apiAddPublishedDerivedEvidence(
  id: string,
  datasetId: string,
): Promise<EvidenceRef> {
  return apiAddEvidence(id, {
    source_namespace: 'research:published_derived',
    source_id: datasetId,
  });
}

/** 阶段 4 新增：EvidenceRef 支持的 source_namespace 枚举 */
export type EvidenceNamespace =
  | 'core:fact'
  | 'research:derived'
  | 'research:published_derived';

export async function apiRemoveEvidence(
  id: string,
  refId: string,
): Promise<void> {
  await http.delete(`/research/workspaces/${id}/evidence/${refId}`);
}

export async function apiListEvidence(id: string): Promise<EvidenceListResponse> {
  const res = await http.get<EvidenceListResponse>(`/research/workspaces/${id}/evidence`);
  return res.data;
}

export async function apiFreezeSnapshot(id: string): Promise<Snapshot> {
  const res = await http.post<Snapshot>(`/research/workspaces/${id}/snapshot`);
  return res.data;
}

export async function apiListSnapshots(id: string): Promise<SnapshotListResponse> {
  const res = await http.get<SnapshotListResponse>(`/research/workspaces/${id}/snapshots`);
  return res.data;
}

export async function apiSearchFacts(params: {
  q: string;
  cursor?: string;
  page_size?: number;
}): Promise<FactSearchResponse> {
  const res = await http.get<FactSearchResponse>('/research/facts/search', { params });
  return res.data;
}

// ============================================================
// API 函数 — 阶段 2 可信执行
// ============================================================

// --- 计划 ---

export async function apiGeneratePlan(
  workspaceId: string,
  snapshotId: string,
): Promise<Plan> {
  const res = await http.post<Plan>(`/research/workspaces/${workspaceId}/plans`, {
    snapshot_id: snapshotId,
  });
  return res.data;
}

export async function apiListPlans(workspaceId: string): Promise<PlanListResponse> {
  const res = await http.get<PlanListResponse>(`/research/workspaces/${workspaceId}/plans`);
  return res.data;
}

export async function apiGetPlan(
  workspaceId: string,
  planId: string,
): Promise<PlanDetail> {
  const res = await http.get<PlanDetail>(`/research/workspaces/${workspaceId}/plans/${planId}`);
  return res.data;
}

export async function apiConfirmPlan(
  workspaceId: string,
  planId: string,
): Promise<Plan> {
  const res = await http.post<Plan>(`/research/workspaces/${workspaceId}/plans/${planId}/confirm`);
  return res.data;
}

export async function apiRevisePlan(
  workspaceId: string,
  planId: string,
  steps: PlanStep[],
): Promise<Plan> {
  const res = await http.put<Plan>(`/research/workspaces/${workspaceId}/plans/${planId}`, {
    steps,
  });
  return res.data;
}

export async function apiAnalyzeData(
  workspaceId: string,
  planId: string,
  snapshotId: string,
  editedAdvice?: string,
): Promise<{ analysis_result: string; data_context?: string }> {
  const res = await http.post(`/research/workspaces/${workspaceId}/analyze-data`, {
    plan_id: planId,
    snapshot_id: snapshotId,
    edited_advice: editedAdvice || null,
  });
  return res.data;
}

export async function apiExtractInsight(
  workspaceId: string,
  planId: string,
  snapshotId: string,
): Promise<{ insight_candidate: InsightCandidate | null; insight_candidate_id: string | null; run_id: string | null }> {
  const res = await http.post(`/research/workspaces/${workspaceId}/extract-insight`, {
    plan_id: planId,
    snapshot_id: snapshotId,
  });
  return res.data;
}

// --- Run ---

export async function apiSubmitRun(
  workspaceId: string,
  planVersionId: string,
  snapshotId: string,
): Promise<Run> {
  const res = await http.post<Run>(`/research/workspaces/${workspaceId}/runs`, {
    plan_version_id: planVersionId,
    snapshot_id: snapshotId,
  });
  return res.data;
}

export async function apiListRuns(workspaceId: string): Promise<RunListResponse> {
  const res = await http.get<RunListResponse>(`/research/workspaces/${workspaceId}/runs`);
  return res.data;
}

export async function apiGetRun(
  workspaceId: string,
  runId: string,
): Promise<RunProgress> {
  const res = await http.get<RunProgress>(`/research/workspaces/${workspaceId}/runs/${runId}`);
  return res.data;
}

export async function apiCancelRun(
  workspaceId: string,
  runId: string,
): Promise<void> {
  await http.post(`/research/workspaces/${workspaceId}/runs/${runId}/cancel`);
}

export async function apiGetRunSteps(
  workspaceId: string,
  runId: string,
): Promise<RunProgress> {
  const res = await http.get<RunProgress>(`/research/workspaces/${workspaceId}/runs/${runId}/steps`);
  return res.data;
}

// --- 工件 ---

export async function apiListRunArtifacts(
  workspaceId: string,
  runId: string,
  artifactType?: string,
): Promise<ArtifactListResponse> {
  const params = artifactType ? { artifact_type: artifactType } : undefined;
  const res = await http.get<ArtifactListResponse>(
    `/research/workspaces/${workspaceId}/runs/${runId}/artifacts`,
    { params },
  );
  return res.data;
}

export async function apiGetRunArtifact(
  workspaceId: string,
  runId: string,
  artifactId: string,
): Promise<Record<string, unknown>> {
  const res = await http.get<Record<string, unknown>>(
    `/research/workspaces/${workspaceId}/runs/${runId}/artifacts/${artifactId}`,
  );
  return res.data;
}

// --- 排队 ---

export async function apiGetQueueStatus(
  workspaceId: string,
  runId: string,
): Promise<QueueStatus> {
  const res = await http.get<QueueStatus>(
    `/research/workspaces/${workspaceId}/runs/${runId}/queue-status`,
  );
  return res.data;
}

// --- SSE 端点 URL ---

export function getRunSSEUrl(workspaceId: string, runId: string): string {
  const baseURL = (import.meta as unknown as { env?: { VITE_API_BASE_URL?: string } }).env?.VITE_API_BASE_URL ?? '/api/v1';
  return `${baseURL}/research/workspaces/${workspaceId}/runs/${runId}/events`;
}

// --- 对话 ---

export async function apiSendMessage(
  workspaceId: string,
  message: string,
  runId?: string,
): Promise<ConversationMessage> {
  const res = await http.post<ConversationMessage>(
    `/research/workspaces/${workspaceId}/conversation`,
    { message, run_id: runId ?? null },
  );
  return res.data;
}

export async function apiListMessages(
  workspaceId: string,
  runId?: string,
  limit?: number,
): Promise<ConversationListResponse> {
  const params: Record<string, unknown> = {};
  if (runId) params.run_id = runId;
  if (limit) params.limit = limit;
  const res = await http.get<ConversationListResponse>(
    `/research/workspaces/${workspaceId}/conversation`,
    { params },
  );
  return res.data;
}

// --- Run 状态获取（轮询 fallback 用） ---

export async function apiGetRunStatus(
  workspaceId: string,
  runId: string,
): Promise<Run> {
  const res = await http.get<Run>(`/research/workspaces/${workspaceId}/runs/${runId}`);
  // RunProgress 包含 Run 的基本字段
  return {
    run_id: res.data.run_id,
    workspace_id: workspaceId,
    run_number: 0,
    status: res.data.status,
    queue_position: null,
  };
}
