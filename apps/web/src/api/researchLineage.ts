/**
 * 研究溯源与知识库 API 客户端（阶段 5 新增）
 *
 * 端点列表（前缀 /api/v1/research）：
 *   GET    /provenance/graph                          — 查询联邦溯源图
 *   GET    /provenance/graph/result/{result_id}/version/{version_number} — 成果版本溯源图
 *   GET    /provenance/graph/dataset/{dataset_id}/version/{version_number} — 数据集溯源图
 *   GET    /provenance/graph/view/{view_id}/version/{version_number}     — 图表溯源图
 *   GET    /provenance/graph/insight/{insight_id}/version/{version_number} — Insight 溯源图
 *   GET    /provenance/node/{namespace}/{node_id}      — 单节点详情
 *   GET    /knowledge/search                          — 检索知识库
 *   GET    /knowledge/references/{insight_id}         — Insight 关联知识引用列表
 *   GET    /knowledge/references/{reference_id}/detail — 单条知识引用详情
 *   POST   /provenance/graph/export                    — 导出溯源图
 *
 * 风格参考 apps/web/src/api/researchPublish.ts：纯 async 函数 + http 实例。
 */
import { http } from './client';

// ============================================================
// 类型定义 — 溯源图
// ============================================================

export type ProvenanceNodeLabel = {
  display_label: string;
  node_type_label: string;
  version_summary: string;
  namespace: string;
  icon: string;
  jump_target: string | null;
};

export type ProvenanceNode = {
  namespace: string;
  node_id: string;
  version: number | null;
  node_type: string;
  display_label: ProvenanceNodeLabel | null;
  attributes: Record<string, unknown>;
  is_restricted: boolean;
};

export type ProvenanceEdge = {
  source_namespace: string;
  source_id: string;
  source_version: number | null;
  target_namespace: string;
  target_id: string;
  target_version: number | null;
  edge_type: string;
  edge_type_label: string;
};

export type ProvenanceGraphStats = {
  total_nodes: number;
  nodes_by_type: Record<string, number>;
  restricted_nodes_count: number;
  truncated_count: number;
};

export type ProvenanceGraph = {
  nodes: ProvenanceNode[];
  edges: ProvenanceEdge[];
  stats: ProvenanceGraphStats;
};

// ============================================================
// 类型定义 — 知识库
// ============================================================

export type KnowledgeSearchResult = {
  document_id: string;
  document_version: string;
  title: string;
  section: string;
  page: number;
  chunk_id: string;
  relevance_score: number;
  source_uri: string;
  content_hash: string;
  snippet: string;
};

export type KnowledgeReferenceRef = {
  reference_id: string;
  workspace_id: string;
  run_id: string;
  step_id: string | null;
  insight_id: string | null;
  document_id: string;
  document_version: string;
  title: string;
  content_hash: string;
  source_uri: string;
  retrieval_time: string;
  provider_name: string;
};

export type KnowledgeReferenceDetail = {
  ref: KnowledgeReferenceRef;
  snippet_text: string;
  section: string;
  page: number;
  chunk_id: string;
  research_question_context: string;
};

export type ExportResponse = {
  format: string;
  content: string;
};

// ============================================================
// API 函数 — 联邦溯源查询
// ============================================================

/**
 * 查询联邦溯源图（通用端点）。
 * BFS 从 target 向上游追溯，跨核心域和研究域拼接完整溯源 DAG。
 */
export async function apiQueryProvenanceGraph(params: {
  target_namespace: string;
  target_id: string;
  max_depth?: number;
  truncate_branch?: boolean;
}): Promise<ProvenanceGraph> {
  const res = await http.get<ProvenanceGraph>('/research/provenance/graph', { params });
  return res.data;
}

/**
 * 查询成果版本的溯源图（便捷端点）。
 */
export async function apiQueryResultProvenance(
  resultId: string,
  versionNumber: number,
  maxDepth?: number,
): Promise<ProvenanceGraph> {
  const params: Record<string, number> = {};
  if (maxDepth !== undefined) params.max_depth = maxDepth;
  const res = await http.get<ProvenanceGraph>(
    `/research/provenance/graph/result/${resultId}/version/${versionNumber}`,
    { params },
  );
  return res.data;
}

/**
 * 查询数据集版本的溯源图。
 */
export async function apiQueryDatasetProvenance(
  datasetId: string,
  versionNumber: number,
  maxDepth?: number,
): Promise<ProvenanceGraph> {
  const params: Record<string, number> = {};
  if (maxDepth !== undefined) params.max_depth = maxDepth;
  const res = await http.get<ProvenanceGraph>(
    `/research/provenance/graph/dataset/${datasetId}/version/${versionNumber}`,
    { params },
  );
  return res.data;
}

/**
 * 查询图表版本的溯源图。
 */
export async function apiQueryViewProvenance(
  viewId: string,
  versionNumber: number,
  maxDepth?: number,
): Promise<ProvenanceGraph> {
  const params: Record<string, number> = {};
  if (maxDepth !== undefined) params.max_depth = maxDepth;
  const res = await http.get<ProvenanceGraph>(
    `/research/provenance/graph/view/${viewId}/version/${versionNumber}`,
    { params },
  );
  return res.data;
}

/**
 * 查询 Insight 版本的溯源图。
 */
export async function apiQueryInsightProvenance(
  insightId: string,
  versionNumber: number,
  maxDepth?: number,
): Promise<ProvenanceGraph> {
  const params: Record<string, number> = {};
  if (maxDepth !== undefined) params.max_depth = maxDepth;
  const res = await http.get<ProvenanceGraph>(
    `/research/provenance/graph/insight/${insightId}/version/${versionNumber}`,
    { params },
  );
  return res.data;
}

/**
 * 查询单个溯源节点详情（校验权限）。
 */
export async function apiGetProvenanceNode(
  namespace: string,
  nodeId: string,
): Promise<ProvenanceNode> {
  const res = await http.get<ProvenanceNode>(
    `/research/provenance/node/${namespace}/${nodeId}`,
  );
  return res.data;
}

// ============================================================
// API 函数 — 知识库检索
// ============================================================

/**
 * 检索知识库（支持指定 Provider 或全部 Provider 并行检索）。
 */
export async function apiSearchKnowledge(params: {
  search_query: string;
  provider_name?: string;
  max_results?: number;
}): Promise<KnowledgeSearchResult[]> {
  const res = await http.get<KnowledgeSearchResult[]>('/research/knowledge/search', {
    params,
  });
  return res.data;
}

/**
 * 查看 Insight 关联的知识引用快照列表。
 * full_content=true 需 research:manage 权限。
 */
export async function apiListKnowledgeReferencesByInsight(
  insightId: string,
  fullContent?: boolean,
): Promise<KnowledgeReferenceDetail[]> {
  const params: Record<string, boolean> = {};
  if (fullContent !== undefined) params.full_content = fullContent;
  const res = await http.get<KnowledgeReferenceDetail[]>(
    `/research/knowledge/references/${insightId}`,
    { params },
  );
  return res.data;
}

/**
 * 查看单个知识引用快照详情。
 * full_content=true 需 research:manage 权限。
 */
export async function apiGetKnowledgeReference(
  referenceId: string,
  fullContent?: boolean,
): Promise<KnowledgeReferenceDetail> {
  const params: Record<string, boolean> = {};
  if (fullContent !== undefined) params.full_content = fullContent;
  const res = await http.get<KnowledgeReferenceDetail>(
    `/research/knowledge/references/${referenceId}/detail`,
    { params },
  );
  return res.data;
}

// ============================================================
// API 函数 — 溯源导出
// ============================================================

/**
 * 导出溯源图（JSON 格式）。
 */
export async function apiExportProvenanceGraph(params: {
  target_namespace: string;
  target_id: string;
  format?: string;
  max_depth?: number;
}): Promise<ExportResponse> {
  const res = await http.post<ExportResponse>('/research/provenance/graph/export', {
    target_namespace: params.target_namespace,
    target_id: params.target_id,
    format: params.format ?? 'json',
    max_depth: params.max_depth ?? 20,
  });
  return res.data;
}
