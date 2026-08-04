/**
 * V1 Facts + Provenance + Parameters API
 *
 * 从 client.ts 拆分，通过 re-export 保持兼容。
 */
import { http } from './client';
import type {
  CursorPage,
  FactSummary,
  FactDetail,
  EvidenceSet,
  Recipe,
  DerivationRun,
  ProvenanceGraph,
  ParameterSummary,
  ParameterDetail,
  ParameterVersion,
  ParameterCandidate,
} from './types';

// ============================================================
// Facts API（/facts）
// ============================================================

export async function apiCreateFact(body: {
  fact_type: string;
  subject_id: string;
  value: unknown;
  unit?: string;
  conditions?: Record<string, unknown>;
}): Promise<FactDetail> {
  const res = await http.post<FactDetail>('/facts', body);
  return res.data;
}

/** 事实列表响应（含每个 task_code 的总数，不受分页限制） */
export type FactListResult = {
  items: FactSummary[];
  next_cursor: string | null;
  has_more: boolean;
  group_counts: Record<string, number>;
};

export async function apiListFacts(params?: {
  cursor?: string;
  page_size?: number;
  fact_type?: string;
  status?: string;
  object_id?: string;
}): Promise<FactListResult> {
  const res = await http.get<FactListResult>('/facts', { params });
  return res.data;
}

export async function apiSearchFacts(params: {
  q: string;
  cursor?: string;
  page_size?: number;
  fact_type?: string;
  status?: string;
  object_id?: string;
}): Promise<FactListResult> {
  const res = await http.get<FactListResult>('/facts/search', { params });
  return res.data;
}

export async function apiSearchFactsByData(params: {
  q?: string;
  key?: string;
  value?: string;
  min_value?: number;
  max_value?: number;
  page_size?: number;
}): Promise<FactListResult> {
  const res = await http.get<FactListResult>('/facts/search-data', { params });
  return res.data;
}

export async function apiGetFact(factId: string): Promise<FactDetail> {
  const res = await http.get<FactDetail>(`/facts/${factId}`);
  return res.data;
}

export type DataSourceItem = {
  component: string;
  component_display_name?: string;
  experimental_object_code?: string;
  object_name?: string;
  equipment_name?: string;
  department_name?: string;
};

export type TaskInfo = {
  task_name: string | null;
  task_source: string | null;
  operator: string | null;
  run_operator: string | null;
  equipment_name: string | null;
  project_name: string | null;
  owner_name: string | null;
  job_id: string | null;
  department_name: string | null;
  data_interface: string | null;
  created_at: string | null;
  experimental_object_codes: string[] | null;
  data_source_list?: DataSourceItem[];
};

export type FactData = {
  metadata: Record<string, unknown>;
  /** 单点数据 */
  points?: { name: string; value: unknown; unit: string | null }[];
  /** 序列数据 */
  series?: { name: string; columns: string[]; rows: unknown[][] }[];
  /** 旧格式兼容：多行数据 */
  data?: Record<string, unknown>[];
  task_info?: TaskInfo;
  source_file?: {
    filename: string;
    media_type: string;
    artifact_id: string;
  };
};

export async function apiGetFactData(factId: string): Promise<FactData> {
  const res = await http.get<FactData>(`/facts/${factId}/data`);
  return res.data;
}

export async function apiDeleteFact(factId: string): Promise<void> {
  await http.delete(`/facts/${factId}`);
}

export async function apiDeleteFactsByTask(taskCode: string): Promise<void> {
  await http.delete(`/facts/by-task/${encodeURIComponent(taskCode)}`);
}

// ============================================================
// Provenance API（/provenance）
// ============================================================

export async function apiCreateEvidenceSet(body: {
  name: string;
}): Promise<EvidenceSet> {
  const res = await http.post<EvidenceSet>('/provenance/evidence-sets', body);
  return res.data;
}

export async function apiListEvidenceSets(params?: {
  status?: string;
  cursor?: string;
  page_size?: number;
}): Promise<CursorPage<EvidenceSet>> {
  const res = await http.get<CursorPage<EvidenceSet>>('/provenance/evidence-sets', { params });
  return res.data;
}

export async function apiFreezeEvidenceSet(setId: string): Promise<EvidenceSet> {
  const res = await http.post<EvidenceSet>(`/provenance/evidence-sets/${setId}/freeze`, { fact_filter: null });
  return res.data;
}

export async function apiGetEvidenceSet(setId: string): Promise<EvidenceSet> {
  const res = await http.get<EvidenceSet>(`/provenance/evidence-sets/${setId}`);
  return res.data;
}

export async function apiListEvidenceSetMembers(setId: string): Promise<unknown[]> {
  const res = await http.get<unknown[]>(`/provenance/evidence-sets/${setId}/members`);
  return res.data;
}

export async function apiCreateRecipe(body: {
  code: string;
  display_name: string;
}): Promise<Recipe> {
  const res = await http.post<Recipe>('/provenance/recipes', body);
  return res.data;
}

export async function apiPublishRecipe(recipeId: string): Promise<Recipe> {
  const res = await http.post<Recipe>(`/provenance/recipes/${recipeId}/publish`, {
    component_name: 'default',
    component_version: '1.0.0',
    parameters: {},
    random_seed: 42,
    output_definitions: [],
  });
  return res.data;
}

export async function apiListRecipes(params?: {
  status?: string;
  cursor?: string;
  page_size?: number;
}): Promise<CursorPage<Recipe>> {
  const res = await http.get<CursorPage<Recipe>>('/provenance/recipes', { params });
  return res.data;
}

export async function apiGetRecipe(recipeId: string): Promise<Recipe> {
  const res = await http.get<Recipe>(`/provenance/recipes/${recipeId}`);
  return res.data;
}

export async function apiCreateDerivationRun(body: {
  evidence_set_version_id: string;
  recipe_version_id: string;
}): Promise<DerivationRun> {
  const res = await http.post<DerivationRun>('/provenance/derivation-runs', body);
  return res.data;
}

export async function apiReplayDerivation(runId: string): Promise<DerivationRun> {
  const res = await http.post<DerivationRun>(`/provenance/derivation-runs/${runId}/replay`);
  return res.data;
}

export async function apiGetDerivationRun(runId: string): Promise<DerivationRun> {
  const res = await http.get<DerivationRun>(`/provenance/derivation-runs/${runId}`);
  return res.data;
}

export async function apiListDerivationRuns(params?: {
  status?: string;
  cursor?: string;
  page_size?: number;
}): Promise<CursorPage<DerivationRun>> {
  const res = await http.get<CursorPage<DerivationRun>>('/provenance/derivation-runs', { params });
  return res.data;
}

export async function apiGetProvenanceGraph(runId: string): Promise<ProvenanceGraph> {
  const res = await http.get<ProvenanceGraph>(`/provenance/derivation-runs/${runId}/graph`);
  return res.data;
}

// ============================================================
// Parameters API（/parameters）
// ============================================================

export async function apiCreateParameter(body: {
  code: string;
  name_zh: string;
  unit?: string;
  description?: string;
}): Promise<ParameterDetail> {
  const res = await http.post<ParameterDetail>('/parameters', body);
  return res.data;
}

export async function apiListParameters(params?: {
  status?: string;
  cursor?: string;
  limit?: number;
}): Promise<CursorPage<ParameterSummary>> {
  const res = await http.get<CursorPage<ParameterSummary>>('/parameters', { params });
  return res.data;
}

export async function apiGetParameter(parameterId: string): Promise<ParameterDetail> {
  const res = await http.get<ParameterDetail>(`/parameters/${parameterId}`);
  return res.data;
}

export async function apiListParameterVersions(parameterId: string): Promise<ParameterVersion[]> {
  const res = await http.get<ParameterVersion[]>(`/parameters/${parameterId}/versions`);
  return res.data;
}

export async function apiGetParameterVersion(parameterId: string, version: string): Promise<ParameterVersion> {
  const res = await http.get<ParameterVersion>(`/parameters/${parameterId}/versions/${version}`);
  return res.data;
}

export async function apiCreateCandidate(parameterId: string, body: {
  value: number;
  unit: string;
  conditions?: Record<string, unknown>;
  confidence_interval?: { lower: number; upper: number };
  evidence_count?: number;
  quality_level?: string;
  derivation_run_id?: string;
}): Promise<ParameterCandidate> {
  const res = await http.post<ParameterCandidate>(`/parameters/${parameterId}/candidates`, body);
  return res.data;
}

export async function apiListCandidates(parameterId: string): Promise<ParameterCandidate[]> {
  const res = await http.get<ParameterCandidate[]>(`/parameters/${parameterId}/candidates`);
  return res.data;
}

export async function apiApproveCandidate(candidateId: string): Promise<ParameterCandidate> {
  const res = await http.post<ParameterCandidate>(`/parameters/candidates/${candidateId}/approve`);
  return res.data;
}

export async function apiRejectCandidate(candidateId: string): Promise<ParameterCandidate> {
  const res = await http.post<ParameterCandidate>(`/parameters/candidates/${candidateId}/reject`);
  return res.data;
}

export async function apiDeprecateParameter(parameterId: string): Promise<ParameterDetail> {
  const res = await http.post<ParameterDetail>(`/parameters/${parameterId}/deprecate`);
  return res.data;
}
