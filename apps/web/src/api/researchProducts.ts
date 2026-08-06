/**
 * 研究产物 API 客户端（阶段 3 新增）
 *
 * 端点列表：
 *   GET    /research/workspaces/{id}/runs/{runId}/candidates                    — 列出候选产物
 *   GET    /research/workspaces/{id}/runs/{runId}/candidates/{candidateId}      — 候选详情
 *   POST   /research/workspaces/{id}/derived-datasets                           — 创建数据集
 *   GET    /research/workspaces/{id}/derived-datasets                           — 列出数据集
 *   GET    /research/workspaces/{id}/derived-datasets/{datasetId}              — 数据集详情
 *   PATCH  /research/workspaces/{id}/derived-datasets/{datasetId}              — 编辑元数据
 *   GET    /research/workspaces/{id}/derived-datasets/{datasetId}/versions     — 版本历史
 *   GET    /research/workspaces/{id}/derived-datasets/{datasetId}/versions/{vn} — 版本详情
 *   POST   /research/workspaces/{id}/views                                      — 创建视图
 *   GET    /research/workspaces/{id}/views                                      — 列出视图
 *   GET    /research/workspaces/{id}/views/{viewId}                            — 视图详情
 *   PATCH  /research/workspaces/{id}/views/{viewId}                            — 编辑元数据
 *   GET    /research/workspaces/{id}/views/{viewId}/versions                   — 版本历史
 *   GET    /research/workspaces/{id}/views/{viewId}/versions/{vn}              — 版本详情
 *   GET    /research/workspaces/{id}/views/{viewId}/versions/{vn}/image        — 下载图片
 *   GET    /research/workspaces/{id}/insights                                   — 列出 Insight
 *   GET    /research/workspaces/{id}/insights/{insightId}                     — Insight 详情
 *   PATCH  /research/workspaces/{id}/insights/{insightId}                     — 编辑元数据
 *   GET    /research/workspaces/{id}/insights/{insightId}/versions           — 版本历史
 *   GET    /research/workspaces/{id}/runs/{runId}/insight-candidates          — 列出候选
 *   GET    /research/workspaces/{id}/runs/{runId}/insight-candidates/{cid}   — 候选详情
 *   POST   /research/workspaces/{id}/runs/{runId}/insight-candidates/{cid}/accept — 接受
 *   POST   /research/workspaces/{id}/runs/{runId}/insight-candidates/{cid}/modify — 修改
 *   POST   /research/workspaces/{id}/runs/{runId}/insight-candidates/{cid}/reject — 拒绝
 *   GET    /research/workspaces/{id}/products                                  — 产物列表
 *   GET    /research/catalog/search                                            — 搜索衍生数据
 *
 * 风格参考 apps/web/src/api/research.ts：纯 async 函数 + http 实例。
 */
import { http } from './client';

// ============================================================
// 类型定义
// ============================================================

export type CandidateProduct = {
  candidate_type: string;
  source_artifact_id: string | null;
  candidate_id: string;
  source_run_id: string;
  source_step_id: string | null;
  step_name: string;
  step_status: string;
  preview_data: Record<string, unknown>;
  status: string;
  error_reason: string;
};

export type CandidateListResponse = {
  items: CandidateProduct[];
};

export type CandidateDetailResponse = {
  candidate_type: string;
  candidate_id: string;
  source_run_id: string;
  source_step_id: string | null;
  preview_data: Record<string, unknown>;
};

export type DerivedDataset = {
  dataset_id: string;
  name: string;
  status: string;
  current_version: number;
  workspace_id: string;
};

export type DatasetListResponse = {
  items: DerivedDataset[];
};

export type DatasetDetail = {
  dataset_id: string;
  workspace_id: string;
  name: string;
  summary: string | null;
  tags: string[];
  status: string;
  current_version: number;
  source_run_id: string;
  source_snapshot_id: string | null;
  current_version_data: Record<string, unknown> | null;
};

export type DatasetVersion = {
  version_id: string;
  dataset_id: string;
  version_number: number;
  content_hash: string;
  created_at: string;
};

export type DatasetVersionListResponse = {
  items: DatasetVersion[];
};

export type DatasetVersionDetail = {
  version_id: string;
  dataset_id: string;
  version_number: number;
  metadata_content: Record<string, unknown>;
  points_content: Array<Record<string, unknown>>;
  series_content: Array<Record<string, unknown>>;
  field_manifest: Array<Record<string, unknown>>;
  content_hash: string;
  source_run_id: string;
  source_step_id: string | null;
  source_artifact_id: string | null;
  created_at: string;
};

export type View = {
  view_id: string;
  name: string;
  status: string;
  current_version: number;
  caption: string | null;
  display_order: number;
};

export type ViewListResponse = {
  items: View[];
};

export type ViewDetail = {
  view_id: string;
  workspace_id: string;
  name: string;
  caption: string | null;
  display_order: number;
  status: string;
  current_version: number;
  source_run_id: string;
  current_version_info: Record<string, unknown> | null;
};

export type ViewVersion = {
  version_id: string;
  view_id: string;
  version_number: number;
  image_storage_path: string;
  image_format: string;
  created_at: string;
};

export type ViewVersionListResponse = {
  items: ViewVersion[];
};

export type ViewVersionDetail = {
  version_id: string;
  view_id: string;
  version_number: number;
  image_storage_path: string;
  image_format: string;
  image_width: number | null;
  image_height: number | null;
  image_content_hash: string;
  chart_code_artifact_id: string | null;
  image_digest: string | null;
  source_run_id: string;
  source_step_id: string | null;
  source_artifact_id: string | null;
  bound_dataset_version_id: string | null;
  chart_description: string | null;
  created_at: string;
};

export type Insight = {
  insight_id: string;
  name: string;
  status: string;
  current_version: number;
};

export type InsightListResponse = {
  items: Insight[];
};

export type InsightDetail = {
  insight_id: string;
  workspace_id: string;
  name: string;
  status: string;
  current_version: number;
  source_run_id: string | null;
  current_version_data: Record<string, unknown> | null;
};

export type InsightVersion = {
  version_id: string;
  insight_id: string;
  version_number: number;
  is_modified: boolean;
  created_at: string;
};

export type InsightVersionListResponse = {
  items: InsightVersion[];
};

export type InsightCandidate = {
  candidate_id: string;
  run_id: string;
  step_id: string | null;
  status: string;
  conclusion: string;
  evidence_source_label: string;
  created_at: string;
};

export type InsightCandidateListResponse = {
  items: InsightCandidate[];
};

export type ProductSummary = {
  product_type: string;
  product_id: string;
  name: string;
  status: string;
  current_version: number;
};

export type ProductListResponse = {
  items: ProductSummary[];
};

export type CatalogSearchResult = {
  id: string;
  name: string;
  current_version: number;
  workspace_id: string;
  owner_user_id: string;
  summary: string;
  tags: string[];
};

export type CatalogSearchResponse = {
  items: CatalogSearchResult[];
};

// ============================================================
// API 函数 — 候选产物
// ============================================================

export async function apiGetCandidates(
  workspaceId: string,
  runId: string,
): Promise<CandidateListResponse> {
  const res = await http.get<CandidateListResponse>(
    `/research/workspaces/${workspaceId}/runs/${runId}/candidates`,
  );
  return res.data;
}

export async function apiGetCandidateDetail(
  workspaceId: string,
  runId: string,
  candidateId: string,
): Promise<CandidateDetailResponse> {
  const res = await http.get<CandidateDetailResponse>(
    `/research/workspaces/${workspaceId}/runs/${runId}/candidates/${candidateId}`,
  );
  return res.data;
}

// ============================================================
// API 函数 — DerivedDataset
// ============================================================

export async function apiCreateDataset(
  workspaceId: string,
  body: { artifact_id: string; name: string; summary?: string; tags?: string[] },
): Promise<DerivedDataset> {
  const res = await http.post<DerivedDataset>(
    `/research/workspaces/${workspaceId}/derived-datasets`,
    body,
  );
  return res.data;
}

export async function apiListDatasets(
  workspaceId: string,
): Promise<DatasetListResponse> {
  const res = await http.get<DatasetListResponse>(
    `/research/workspaces/${workspaceId}/derived-datasets`,
  );
  return res.data;
}

export async function apiGetDataset(
  workspaceId: string,
  datasetId: string,
): Promise<DatasetDetail> {
  const res = await http.get<DatasetDetail>(
    `/research/workspaces/${workspaceId}/derived-datasets/${datasetId}`,
  );
  return res.data;
}

export async function apiUpdateDatasetMetadata(
  workspaceId: string,
  datasetId: string,
  body: { name?: string; summary?: string; tags?: string[] },
): Promise<DerivedDataset> {
  const res = await http.patch<DerivedDataset>(
    `/research/workspaces/${workspaceId}/derived-datasets/${datasetId}`,
    body,
  );
  return res.data;
}

export async function apiListDatasetVersions(
  workspaceId: string,
  datasetId: string,
): Promise<DatasetVersionListResponse> {
  const res = await http.get<DatasetVersionListResponse>(
    `/research/workspaces/${workspaceId}/derived-datasets/${datasetId}/versions`,
  );
  return res.data;
}

export async function apiGetDatasetVersion(
  workspaceId: string,
  datasetId: string,
  versionNumber: number,
): Promise<DatasetVersionDetail> {
  const res = await http.get<DatasetVersionDetail>(
    `/research/workspaces/${workspaceId}/derived-datasets/${datasetId}/versions/${versionNumber}`,
  );
  return res.data;
}

// ============================================================
// API 函数 — ResearchView
// ============================================================

export async function apiCreateView(
  workspaceId: string,
  body: { artifact_id: string; name: string; caption?: string; display_order?: number },
): Promise<View> {
  const res = await http.post<View>(
    `/research/workspaces/${workspaceId}/views`,
    body,
  );
  return res.data;
}

export async function apiListViews(
  workspaceId: string,
): Promise<ViewListResponse> {
  const res = await http.get<ViewListResponse>(
    `/research/workspaces/${workspaceId}/views`,
  );
  return res.data;
}

export async function apiGetView(
  workspaceId: string,
  viewId: string,
): Promise<ViewDetail> {
  const res = await http.get<ViewDetail>(
    `/research/workspaces/${workspaceId}/views/${viewId}`,
  );
  return res.data;
}

export async function apiUpdateViewMetadata(
  workspaceId: string,
  viewId: string,
  body: { name?: string; caption?: string; display_order?: number },
): Promise<View> {
  const res = await http.patch<View>(
    `/research/workspaces/${workspaceId}/views/${viewId}`,
    body,
  );
  return res.data;
}

export async function apiListViewVersions(
  workspaceId: string,
  viewId: string,
): Promise<ViewVersionListResponse> {
  const res = await http.get<ViewVersionListResponse>(
    `/research/workspaces/${workspaceId}/views/${viewId}/versions`,
  );
  return res.data;
}

export async function apiGetViewVersion(
  workspaceId: string,
  viewId: string,
  versionNumber: number,
): Promise<ViewVersionDetail> {
  const res = await http.get<ViewVersionDetail>(
    `/research/workspaces/${workspaceId}/views/${viewId}/versions/${versionNumber}`,
  );
  return res.data;
}

export function getViewImageUrl(
  workspaceId: string,
  viewId: string,
  versionNumber: number,
): string {
  const baseURL = (import.meta as any).env?.VITE_API_BASE_URL ?? '/api/v1';
  return `${baseURL}/research/workspaces/${workspaceId}/views/${viewId}/versions/${versionNumber}/image`;
}

// ============================================================
// API 函数 — Insight
// ============================================================

export async function apiListInsights(
  workspaceId: string,
): Promise<InsightListResponse> {
  const res = await http.get<InsightListResponse>(
    `/research/workspaces/${workspaceId}/insights`,
  );
  return res.data;
}

export async function apiGetInsight(
  workspaceId: string,
  insightId: string,
): Promise<InsightDetail> {
  const res = await http.get<InsightDetail>(
    `/research/workspaces/${workspaceId}/insights/${insightId}`,
  );
  return res.data;
}

export async function apiUpdateInsightMetadata(
  workspaceId: string,
  insightId: string,
  body: { name: string },
): Promise<Insight> {
  const res = await http.patch<Insight>(
    `/research/workspaces/${workspaceId}/insights/${insightId}`,
    body,
  );
  return res.data;
}

export async function apiListInsightVersions(
  workspaceId: string,
  insightId: string,
): Promise<InsightVersionListResponse> {
  const res = await http.get<InsightVersionListResponse>(
    `/research/workspaces/${workspaceId}/insights/${insightId}/versions`,
  );
  return res.data;
}

// ============================================================
// API 函数 — Insight Candidate
// ============================================================

export async function apiListInsightCandidates(
  workspaceId: string,
  runId: string,
): Promise<InsightCandidateListResponse> {
  const res = await http.get<InsightCandidateListResponse>(
    `/research/workspaces/${workspaceId}/runs/${runId}/insight-candidates`,
  );
  return res.data;
}

export async function apiAcceptCandidate(
  workspaceId: string,
  runId: string,
  candidateId: string,
): Promise<Insight> {
  const res = await http.post<Insight>(
    `/research/workspaces/${workspaceId}/runs/${runId}/insight-candidates/${candidateId}/accept`,
  );
  return res.data;
}

export async function apiModifyCandidate(
  workspaceId: string,
  runId: string,
  candidateId: string,
  body: {
    conclusion?: string;
    scope?: string;
    evidence_refs?: Array<Record<string, unknown>>;
    method_refs?: Array<Record<string, unknown>>;
    confidence_level?: string;
    limitations?: string;
    evidence_source_label?: string;
    modification_note: string;
  },
): Promise<Insight> {
  const res = await http.post<Insight>(
    `/research/workspaces/${workspaceId}/runs/${runId}/insight-candidates/${candidateId}/modify`,
    body,
  );
  return res.data;
}

export async function apiRejectCandidate(
  workspaceId: string,
  runId: string,
  candidateId: string,
  reason?: string,
): Promise<void> {
  await http.post(
    `/research/workspaces/${workspaceId}/runs/${runId}/insight-candidates/${candidateId}/reject`,
    { reason: reason ?? null },
  );
}

export async function apiRejectAnyCandidate(
  workspaceId: string,
  runId: string,
  candidateId: string,
  reason?: string,
): Promise<void> {
  await http.post(
    `/research/workspaces/${workspaceId}/runs/${runId}/candidates/${candidateId}/reject`,
    { reason: reason ?? null },
  );
}

export async function apiDeleteInsight(
  workspaceId: string,
  insightId: string,
): Promise<void> {
  await http.delete(`/research/workspaces/${workspaceId}/insights/${insightId}`);
}

export async function apiDeleteDataset(
  workspaceId: string,
  datasetId: string,
): Promise<void> {
  await http.delete(`/research/workspaces/${workspaceId}/derived-datasets/${datasetId}`);
}

export async function apiDeleteView(
  workspaceId: string,
  viewId: string,
): Promise<void> {
  await http.delete(`/research/workspaces/${workspaceId}/views/${viewId}`);
}

// ============================================================
// API 函数 — 产物列表
// ============================================================

export async function apiListProducts(
  workspaceId: string,
): Promise<ProductListResponse> {
  const res = await http.get<ProductListResponse>(
    `/research/workspaces/${workspaceId}/products`,
  );
  return res.data;
}

// ============================================================
// API 函数 — ResearchCatalog
// ============================================================

export async function apiSearchCatalog(
  query: string,
  workspaceId?: string,
): Promise<CatalogSearchResponse> {
  const params: Record<string, string> = { query };
  if (workspaceId) params.workspace_id = workspaceId;
  const res = await http.get<CatalogSearchResponse>(
    `/research/catalog/search`,
    { params },
  );
  return res.data;
}
