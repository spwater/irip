/**
 * 研究发布与复用 API 客户端（阶段 4 新增）
 *
 * 端点列表（前缀 /api/v1/research）：
 *   POST   /workspaces/{id}/results                              — 组装并发布成果包
 *   GET    /workspaces/{id}/results                             — 列出工作空间成果包
 *   GET    /workspaces/{id}/results/{result_id}                — 成果包详情
 *   PATCH  /workspaces/{id}/results/{result_id}                — 编辑成果包元数据
 *   POST   /workspaces/{id}/results/{result_id}/versions       — 发布新版本
 *   GET    /workspaces/{id}/results/{result_id}/versions       — 版本历史
 *   GET    /workspaces/{id}/results/{result_id}/versions/{vn}  — 版本详情
 *   POST   /workspaces/{id}/results/{result_id}/versions/{vn}/withdraw — 撤回版本
 *   GET    /workspaces/{id}/results/{result_id}/acl            — 查看 ACL
 *   PUT    /workspaces/{id}/results/{result_id}/acl            — 修改 ACL
 *   POST   /workspaces/{id}/results/{result_id}/declassify      — 突破权限包络
 *   GET    /publications                                        — 搜索已发布成果包
 *   GET    /publications/{result_id}                           — 成果包详情
 *   GET    /publications/{result_id}/versions/{vn}             — 版本详情
 *   GET    /publications/{result_id}/items/{item_type}/{item_id} — 内部对象引用
 *   GET    /publications/{result_id}/provenance                — 来源信息
 *   POST   /workspaces/{id}/evidence/from-publication            — 从已发布成果添加证据
 *   POST   /workspaces/from-publication/{result_id}            — 基于此成果新建 Workspace
 *   POST   /publications/{result_id}/favorite                  — 收藏
 *   DELETE /publications/{result_id}/favorite                   — 取消收藏
 *   GET    /publications/favorites                             — 收藏列表
 *   GET    /catalog/search-published                            — 搜索已发布 DerivedDataset
 *
 * 风格参考 apps/web/src/api/research.ts：纯 async 函数 + http 实例。
 */
import { http } from './client';

// ============================================================
// 类型定义
// ============================================================

export type ResultRef = {
  result_id: string;
  name: string;
  status: string;
  current_version: number;
  current_acl_type: string;
  workspace_id?: string;
};

export type ResultVersionRef = {
  result_id: string;
  version_number: number;
  title: string;
  status: string;
  published_at: string | null;
};

export type ResultVersionDetail = {
  result_id: string;
  version_number: number;
  title: string;
  summary: string;
  tags: string[];
  release_notes: string;
  dataset_version_refs: Array<Record<string, unknown>>;
  view_version_refs: Array<Record<string, unknown>>;
  insight_version_refs: Array<Record<string, unknown>>;
  evidence_snapshot_ids: string[];
  analysis_run_ids: string[];
  source_run_statuses: Record<string, string>;
  publisher: string;
  published_at: string | null;
  content_hash: string;
  published_permission_envelope: Record<string, unknown>;
  status: string;
};

export type AclRevisionRef = {
  revision_number: number;
  acl_type: string;
  explicit_user_ids: string[];
  previous_acl_type: string | null;
  previous_explicit_user_ids: string[] | null;
  changed_by: string;
  changed_at: string | null;
  change_reason: string | null;
  is_declassify: boolean;
  declassify_reason: string | null;
};

export type ResultDetail = {
  result: ResultRef;
  current_version: ResultVersionDetail | null;
  version_history: ResultVersionRef[];
  acl_revisions: AclRevisionRef[];
  is_favorited: boolean;
};

export type SearchResultItem = {
  result_id: string;
  name: string;
  title: string;
  summary: string;
  tags: string[];
  publisher: string;
  published_at: string | null;
  current_version: number;
  current_acl_type: string;
  dataset_count: number;
  view_count: number;
  insight_count: number;
  workspace_id: string;
};

export type SearchResultPage = {
  items: SearchResultItem[];
  total: number;
  page: number;
  page_size: number;
};

export type PublishResultBody = {
  title: string;
  summary?: string;
  tags?: string[];
  release_notes?: string;
  dataset_ids: string[];
  view_ids: string[];
  insight_ids: string[];
  requested_acl?: string;
  explicit_user_ids?: string[];
  is_declassify?: boolean;
  declassify_reason?: string;
};

export type PublishNewVersionBody = PublishResultBody;

export type UpdateMetadataBody = {
  name: string;
};

export type UpdateAclBody = {
  acl_type: string;
  explicit_user_ids?: string[];
  reason?: string;
};

export type DeclassifyBody = {
  acl_type: string;
  explicit_user_ids?: string[];
  declassify_reason: string;
};

export type WithdrawBody = {
  reason?: string;
};

export type AddFromPublicationBody = {
  result_id: string;
  dataset_id: string;
  version_number?: number | null;
};

export type NewWorkspaceFromPublicationBody = {
  workspace_name: string;
  question_text: string;
};

export type EvidenceFromPublicationRef = {
  ref_id: string;
  source_namespace: string;
  source_id: string;
  source_version: string | null;
  source_name: string;
  status: string;
};

export type WorkspaceFromPublicationRef = {
  workspace_id: string;
  name: string;
  status: string;
  current_question_version: number;
};

export type CatalogPublishedSearchResult = {
  result_id: string;
  dataset_id: string;
  version_number: number;
  result_title: string;
  publisher: string;
  published_at: string | null;
};

export type CatalogPublishedSearchResponse = {
  items: CatalogPublishedSearchResult[];
};

export type ProvenanceInfo = {
  result_id: string;
  name: string;
  current_version: number;
  evidence_snapshot_ids: string[];
  evidence_snapshot_labels?: { id: string; label: string }[];
  analysis_run_ids: string[];
  analysis_run_labels?: { id: string; label: string }[];
  source_run_statuses: Record<string, string>;
  publisher: string | null;
  published_at: string | null;
};

// ============================================================
// API 函数 — 成果包发布
// ============================================================

export async function apiPublishResult(
  workspaceId: string,
  body: PublishResultBody,
): Promise<ResultVersionRef> {
  const res = await http.post<ResultVersionRef>(
    `/research/workspaces/${workspaceId}/results`,
    body,
  );
  return res.data;
}

export async function apiListWorkspaceResults(
  workspaceId: string,
): Promise<ResultRef[]> {
  const res = await http.get<ResultRef[]>(
    `/research/workspaces/${workspaceId}/results`,
  );
  return res.data;
}

export async function apiGetWorkspaceResult(
  workspaceId: string,
  resultId: string,
): Promise<ResultDetail> {
  const res = await http.get<ResultDetail>(
    `/research/workspaces/${workspaceId}/results/${resultId}`,
  );
  return res.data;
}

export async function apiUpdateResultMetadata(
  workspaceId: string,
  resultId: string,
  body: UpdateMetadataBody,
): Promise<ResultRef> {
  const res = await http.patch<ResultRef>(
    `/research/workspaces/${workspaceId}/results/${resultId}`,
    body,
  );
  return res.data;
}

export async function apiPublishNewVersion(
  workspaceId: string,
  resultId: string,
  body: PublishNewVersionBody,
): Promise<ResultVersionRef> {
  const res = await http.post<ResultVersionRef>(
    `/research/workspaces/${workspaceId}/results/${resultId}/versions`,
    body,
  );
  return res.data;
}

export async function apiListResultVersions(
  workspaceId: string,
  resultId: string,
): Promise<ResultVersionRef[]> {
  const res = await http.get<ResultVersionRef[]>(
    `/research/workspaces/${workspaceId}/results/${resultId}/versions`,
  );
  return res.data;
}

export async function apiGetResultVersionDetail(
  workspaceId: string,
  resultId: string,
  versionNumber: number,
): Promise<ResultVersionDetail> {
  const res = await http.get<ResultVersionDetail>(
    `/research/workspaces/${workspaceId}/results/${resultId}/versions/${versionNumber}`,
  );
  return res.data;
}

// ============================================================
// API 函数 — 版本管理
// ============================================================

export async function apiWithdrawVersion(
  workspaceId: string,
  resultId: string,
  versionNumber: number,
  body: WithdrawBody,
): Promise<{ status: string }> {
  const res = await http.post<{ status: string }>(
    `/research/workspaces/${workspaceId}/results/${resultId}/versions/${versionNumber}/withdraw`,
    body,
  );
  return res.data;
}

export async function apiWithdrawResult(
  resultId: string,
  reason?: string,
): Promise<void> {
  await http.patch(`/research/publications/${resultId}/withdraw`, { reason: reason ?? '' });
}

// ============================================================
// API 函数 — ACL 管理
// ============================================================

export async function apiGetAcl(
  workspaceId: string,
  resultId: string,
): Promise<{ revisions: AclRevisionRef[] }> {
  const res = await http.get<{ revisions: AclRevisionRef[] }>(
    `/research/workspaces/${workspaceId}/results/${resultId}/acl`,
  );
  return res.data;
}

export async function apiUpdateAcl(
  workspaceId: string,
  resultId: string,
  body: UpdateAclBody,
): Promise<AclRevisionRef> {
  const res = await http.put<AclRevisionRef>(
    `/research/workspaces/${workspaceId}/results/${resultId}/acl`,
    body,
  );
  return res.data;
}

export async function apiDeclassify(
  workspaceId: string,
  resultId: string,
  body: DeclassifyBody,
): Promise<AclRevisionRef> {
  const res = await http.post<AclRevisionRef>(
    `/research/workspaces/${workspaceId}/results/${resultId}/declassify`,
    body,
  );
  return res.data;
}

// ============================================================
// API 函数 — 成果包搜索与发现（跨 Workspace）
// ============================================================

export async function apiSearchPublications(params: {
  query?: string;
  publisher?: string;
  tags?: string;
  date_from?: string;
  date_to?: string;
  data_type?: string;
  workspace_id?: string;
  view_mode?: string;
  page?: number;
  page_size?: number;
}): Promise<SearchResultPage> {
  const res = await http.get<SearchResultPage>('/research/publications', { params });
  return res.data;
}

export async function apiGetPublicationDetail(
  resultId: string,
): Promise<ResultDetail> {
  const res = await http.get<ResultDetail>(`/research/publications/${resultId}`);
  return res.data;
}

export async function apiGetPublicationVersion(
  resultId: string,
  versionNumber: number,
): Promise<ResultVersionDetail> {
  const res = await http.get<ResultVersionDetail>(
    `/research/publications/${resultId}/versions/${versionNumber}`,
  );
  return res.data;
}

export async function apiGetPublicationItem(
  resultId: string,
  itemType: string,
  itemId: string,
): Promise<Record<string, unknown>> {
  const res = await http.get<Record<string, unknown>>(
    `/research/publications/${resultId}/items/${itemType}/${itemId}`,
  );
  return res.data;
}

export async function apiGetPublicationProvenance(
  resultId: string,
): Promise<ProvenanceInfo> {
  const res = await http.get<ProvenanceInfo>(
    `/research/publications/${resultId}/provenance`,
  );
  return res.data;
}

// ============================================================
// API 函数 — 复用
// ============================================================

export async function apiAddEvidenceFromPublication(
  workspaceId: string,
  body: AddFromPublicationBody,
): Promise<EvidenceFromPublicationRef> {
  const res = await http.post<EvidenceFromPublicationRef>(
    `/research/workspaces/${workspaceId}/evidence/from-publication`,
    body,
  );
  return res.data;
}

export async function apiNewWorkspaceFromPublication(
  resultId: string,
  body: NewWorkspaceFromPublicationBody,
): Promise<WorkspaceFromPublicationRef> {
  const res = await http.post<WorkspaceFromPublicationRef>(
    `/research/workspaces/from-publication/${resultId}`,
    body,
  );
  return res.data;
}

// ============================================================
// API 函数 — 收藏
// ============================================================

export async function apiAddFavorite(
  resultId: string,
): Promise<{ status: string }> {
  const res = await http.post<{ status: string }>(
    `/research/publications/${resultId}/favorite`,
  );
  return res.data;
}

export async function apiRemoveFavorite(
  resultId: string,
): Promise<{ status: string }> {
  const res = await http.delete<{ status: string }>(
    `/research/publications/${resultId}/favorite`,
  );
  return res.data;
}

export async function apiListFavorites(): Promise<{
  items: SearchResultItem[];
  total: number;
}> {
  const res = await http.get<{ items: SearchResultItem[]; total: number }>(
    '/research/publications/favorites',
  );
  return res.data;
}

// ============================================================
// API 函数 — ResearchCatalog 跨用户搜索
// ============================================================

export async function apiSearchPublishedCatalog(
  query: string,
  resultId?: string,
): Promise<CatalogPublishedSearchResponse> {
  const params: Record<string, string> = { query };
  if (resultId) params.result_id = resultId;
  const res = await http.get<CatalogPublishedSearchResponse>(
    '/research/catalog/search-published',
    { params },
  );
  return res.data;
}
