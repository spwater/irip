import axios, { type AxiosError, type InternalAxiosRequestConfig } from 'axios';

/**
 * IRIP API 客户端类型定义
 */

export type CurrentUser = {
  id: string;
  displayName: string;
  roles: string[];
  permissions: string[];
};

export type JobStatus =
  | 'accepted'
  | 'queued'
  | 'running'
  | 'retry_wait'
  | 'succeeded'
  | 'failed'
  | 'cancel_requested'
  | 'cancelled';

export type JobSummary = {
  id: string;
  kind: string;
  status: JobStatus;
  stage: string;
  progress: number;
  retryable: boolean;
};

export type LoginResponse = {
  access_token: string;
  expires_in: number;
};

/** 后端 /jobs/{id} 实际返回的原始结构 */
type JobApiResponse = {
  job_id: string;
  status: string;
  kind: string;
  stage?: string;
  progress?: number;
  retryable?: boolean;
};

/** 后端 /me 实际返回的原始结构 */
type MeApiResponse = {
  id: string;
  email: string;
  display_name: string;
  roles: string[];
  permissions: string[];
};

/**
 * Axios 实例
 * - baseURL 从环境变量 VITE_API_BASE_URL 读取，默认 /api/v1
 * - withCredentials: true — 携带 HttpOnly refresh cookie
 */
const baseURL = import.meta.env.VITE_API_BASE_URL ?? '/api/v1';

export const http = axios.create({
  baseURL,
  withCredentials: true,
});

/** access token 仅存于内存，不持久化到 localStorage */
let accessToken: string | null = null;

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function getAccessToken(): string | null {
  return accessToken;
}

/**
 * 请求拦截器：自动添加 Authorization header
 */
http.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  if (accessToken) {
    config.headers.set('Authorization', `Bearer ${accessToken}`);
  }
  return config;
});

/**
 * 响应拦截器：401 时自动尝试 refresh 并重试一次
 * 重试仍 401 → 跳转登录页
 */
let isRefreshing = false;

http.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const originalRequest = error.config as InternalAxiosRequestConfig & { _retried?: boolean } | undefined;
    if (error.response?.status === 401 && originalRequest && !originalRequest._retried) {
      if (isRefreshing) {
        return Promise.reject(error);
      }
      isRefreshing = true;
      try {
        const res = await axios.post<LoginResponse>(
          `${baseURL}/auth/refresh`,
          {},
          { withCredentials: true },
        );
        accessToken = res.data.access_token;
        originalRequest._retried = true;
        originalRequest.headers.set('Authorization', `Bearer ${accessToken}`);
        return http.request(originalRequest);
      } catch {
        // 不用 window.location.href 硬跳转，否则会触发整页刷新→init()→401→刷新 死循环
        // 只清除 token，让 AuthProvider/AppShell 通过 router navigate 处理跳转
        accessToken = null;
        return Promise.reject(error);
      } finally {
        isRefreshing = false;
      }
    }
    return Promise.reject(error);
  },
);

/**
 * API 函数封装
 */

export async function apiLogin(email: string, password: string): Promise<LoginResponse> {
  const res = await http.post<LoginResponse>('/auth/login', { email, password });
  return res.data;
}

export async function apiRefresh(): Promise<LoginResponse | null> {
  try {
    // 用裸 axios 绕过响应拦截器，避免 refresh 失败时触发 window.location.href 硬跳转导致无限刷新
    const res = await axios.post<LoginResponse>(
      `${baseURL}/auth/refresh`,
      {},
      { withCredentials: true },
    );
    return res.data;
  } catch {
    return null;
  }
}

export async function apiGetMe(): Promise<CurrentUser> {
  const res = await http.get<MeApiResponse>('/me');
  return {
    id: res.data.id,
    displayName: res.data.display_name,
    roles: res.data.roles ?? [],
    permissions: res.data.permissions ?? [],
  };
}

export async function apiLogout(): Promise<void> {
  await http.post('/auth/logout');
}

export async function apiGetJob(id: string): Promise<JobSummary> {
  const res = await http.get<JobApiResponse>(`/jobs/${id}`);
  return {
    id: res.data.job_id,
    kind: res.data.kind,
    status: res.data.status as JobStatus,
    stage: res.data.stage ?? '',
    progress: res.data.progress ?? 0,
    retryable: res.data.retryable ?? false,
  };
}

export async function apiCreateJob(
  kind: string,
  payload: Record<string, unknown>,
  idempotencyKey: string,
): Promise<{ job_id: string }> {
  const res = await http.post<{ job_id: string }>('/jobs', {
    kind,
    payload,
    idempotency_key: idempotencyKey,
  });
  return res.data;
}

export async function apiCancelJob(id: string): Promise<JobSummary> {
  const res = await http.post<JobSummary>(`/jobs/${id}/cancel`);
  return res.data;
}

// ============================================================
// 机构/实验室管理 API
// ============================================================

/** 实验室详情 */
export type Department = {
  id: string;
  organization_id: string;
  code: string;
  display_name: string;
  description: string | null;
  status: string;
  sort_order: number;
  created_at: string;
  updated_at: string;
  lock_version: number;
};

/** 实验室列表项（含成员数） */
export type DepartmentListItem = {
  id: string;
  code: string;
  display_name: string;
  status: string;
  sort_order: number;
  member_count: number;
};

/** 实验室分页列表响应 */
export type DepartmentListResponse = {
  items: DepartmentListItem[];
  next_cursor: string | null;
  has_more: boolean;
};

/** 实验室下用户项 */
export type DepartmentUser = {
  user_id: string;
  email: string;
  display_name: string;
  is_primary: boolean;
};

/** 用户-实验室关联项 */
export type UserDepartment = {
  user_id: string;
  department_id: string;
  department_code: string;
  department_display_name: string;
  is_primary: boolean;
};

export async function apiListDepartments(
  params?: { status?: string; cursor?: string; limit?: number },
): Promise<DepartmentListResponse> {
  const res = await http.get<DepartmentListResponse>('/departments', { params });
  return res.data;
}

export async function apiGetDepartment(id: string): Promise<Department> {
  const res = await http.get<Department>(`/departments/${id}`);
  return res.data;
}

export async function apiCreateDepartment(body: {
  code: string;
  display_name: string;
  description?: string;
  sort_order?: number;
}): Promise<Department> {
  const res = await http.post<Department>('/departments', body);
  return res.data;
}

export async function apiUpdateDepartment(
  id: string,
  body: {
    display_name: string;
    description?: string;
    sort_order?: number;
    lock_version: number;
  },
): Promise<Department> {
  const res = await http.patch<Department>(`/departments/${id}`, body);
  return res.data;
}

export async function apiUpdateDepartmentStatus(
  id: string,
  body: { status: 'active' | 'disabled'; lock_version: number },
): Promise<Department> {
  const res = await http.patch<Department>(`/departments/${id}/status`, body);
  return res.data;
}

export async function apiGetDepartmentUsers(
  departmentId: string,
): Promise<DepartmentUser[]> {
  const res = await http.get<DepartmentUser[]>(`/departments/${departmentId}/users`);
  return res.data;
}

export async function apiGetUserDepartments(
  userId: string,
): Promise<UserDepartment[]> {
  const res = await http.get<UserDepartment[]>(`/users/${userId}/departments`);
  return res.data;
}

export async function apiSetUserDepartments(
  userId: string,
  body: { department_ids: string[]; primary_department_id?: string },
): Promise<{ ok: boolean }> {
  const res = await http.put<{ ok: boolean }>(`/users/${userId}/departments`, body);
  return res.data;
}

// ============================================================
// V1 业务 API 类型定义
// ============================================================

/** 通用游标分页响应 */
export type CursorPage<T> = {
  items: T[];
  next_cursor: string | null;
  has_more: boolean;
};

// ---- Standards: Variables ----
export type VariableSummary = {
  id: string;
  code: string;
  name_zh: string;
  name_en: string;
  quantity_kind: string;
  data_type: string;
  status: string;
  current_version: string | null;
};

export type VariableDetail = VariableSummary & {
  description: string | null;
  unit: string | null;
  aliases: string[];
  lock_version: number;
  created_at: string;
  updated_at: string;
};

export type VariableVersion = {
  version: string;
  status: string;
  created_at: string;
  created_by: string;
  change_note: string | null;
};

// ---- Objects ----
export type IndustrialObject = {
  id: string;
  code: string;
  name_zh: string;
  name_en: string;
  object_type: string;
  description: string | null;
  status: string;
  parent_id: string | null;
};

export type ObjectRelation = {
  id: string;
  source_id: string;
  target_id: string;
  relation_type: string;
  description: string | null;
};

// ---- Templates ----
export type TemplateSummary = {
  id: string;
  code: string;
  name_zh: string;
  status: string;
  current_version: string | null;
};

// ---- Methods ----
export type MethodSummary = {
  id: string;
  code: string;
  name_zh: string;
  status: string;
  current_version: string | null;
};

// ---- Packages ----
export type PackageSummary = {
  id: string;
  code: string;
  name_zh: string;
  status: string;
  current_version: string | null;
};

// ---- Ingestions ----
export type SourceColumn = {
  name: string;
  inferred_type: string;
  sample_values: unknown[];
};

export type SourcePreview = {
  columns: SourceColumn[];
  rows: Record<string, unknown>[];
  total_rows: number;
};

export type MappingCandidate = {
  variableVersionId: string;
  variableCode: string;
  score: number;
  reasons: string[];
};

export type MappingRankResponse = {
  candidates: MappingCandidate[];
};

// ---- Facts ----
export type FactSummary = {
  id: string;
  fact_type: string;
  subject_id: string;
  status: string;
  quality_level: string;
  revision_count: number;
  created_at: string;
};

export type FactDetail = {
  id: string;
  fact_type: string;
  subject_id: string;
  status: string;
  quality_level: string;
  current_revision: number;
  value: unknown;
  unit: string | null;
  conditions: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
};

export type FactRevision = {
  revision: number;
  status: string;
  created_at: string;
  created_by: string;
  change_note: string | null;
};

export type Observation = {
  id: string;
  fact_id: string;
  revision: number;
  source_field: string;
  source_value: string;
  source_unit: string | null;
  normalized_value: string;
  normalized_unit: string | null;
  artifact_id: string | null;
  artifact_url: string | null;
  quality_level: string;
};

// ---- Provenance ----
export type ProvenanceNode = {
  id: string;
  type: 'fact_revision' | 'observation' | 'intermediate_artifact' | 'derivation_run' | 'parameter_version';
  label: string;
  version: string;
  status: string;
};

export type ProvenanceEdge = {
  source: string;
  target: string;
  label: string;
};

export type ProvenanceGraph = {
  nodes: ProvenanceNode[];
  edges: ProvenanceEdge[];
};

export type EvidenceSet = {
  id: string;
  label: string;
  status: string;
  member_count: number;
  created_at: string;
  frozen_at: string | null;
};

export type Recipe = {
  id: string;
  label: string;
  status: string;
  version: string | null;
  created_at: string;
};

export type DerivationRun = {
  id: string;
  recipe_id: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  output_count: number;
};

// ---- Parameters ----
export type ParameterSummary = {
  id: string;
  code: string;
  name_zh: string;
  status: string;
  current_version: string | null;
  evidence_count: number;
  staleness_status: string | null;
};

export type ParameterDetail = ParameterSummary & {
  description: string | null;
  unit: string | null;
  lock_version: number;
  created_at: string;
  updated_at: string;
};

export type ParameterVersion = {
  version: string;
  value: number;
  unit: string;
  status: string;
  created_at: string;
  created_by: string;
};

export type ParameterCandidate = {
  id: string;
  parameter_id: string;
  version_label: string;
  value: number;
  unit: string;
  conditions: Record<string, unknown> | null;
  confidence_interval: { lower: number; upper: number } | null;
  evidence_count: number;
  quality_level: string;
  status: string;
  submitted_by: string;
  derivation_run_id: string | null;
  created_at?: string;
};

// ============================================================
// V1 通用类型别名
// ============================================================

export type StandardStatus = 'draft' | 'in_review' | 'published' | 'deprecated' | 'rejected';
export type QualityLevel = 'Q0' | 'Q1' | 'Q2' | 'Q3';

export type PreviewTable = {
  columns: { name: string; data_type: string; sample_values: string[] }[];
  rows: Record<string, string>[];
  row_count: number;
};

// ============================================================
// 通用工具函数
// ============================================================

/** 从 Axios 错误中提取后端错误消息 */
export function extractApiError(err: unknown): string {
  if (err && typeof err === 'object' && 'response' in err) {
    const response = (err as { response?: { data?: { error?: { message?: string } } } }).response;
    if (response?.data?.error?.message) {
      return response.data.error.message;
    }
  }
  if (err instanceof Error) {
    return err.message;
  }
  return '操作失败';
}

// ============================================================
// Standards API（/standards）
// ============================================================

export async function apiCreateVariable(body: {
  code: string;
  name_zh: string;
  name_en: string;
  quantity_kind: string;
  data_type: string;
  unit?: string;
  description?: string;
}): Promise<VariableDetail> {
  const res = await http.post<VariableDetail>('/standards/variables', body);
  return res.data;
}

export async function apiListVariables(params?: {
  status?: string;
  cursor?: string;
  limit?: number;
}): Promise<CursorPage<VariableSummary>> {
  const res = await http.get<CursorPage<VariableSummary>>('/standards/variables', { params });
  return res.data;
}

export async function apiGetVariable(variableId: string): Promise<VariableDetail> {
  const res = await http.get<VariableDetail>(`/standards/variables/${variableId}`);
  return res.data;
}

export async function apiListVariableVersions(variableId: string): Promise<VariableVersion[]> {
  const res = await http.get<VariableVersion[]>(`/standards/variables/${variableId}/versions`);
  return res.data;
}

export async function apiSubmitVariable(variableId: string): Promise<VariableDetail> {
  const res = await http.post<VariableDetail>(`/standards/variables/${variableId}/submit`);
  return res.data;
}

export async function apiPublishVariable(variableId: string): Promise<VariableDetail> {
  const res = await http.post<VariableDetail>(`/standards/variables/${variableId}/publish`);
  return res.data;
}

export async function apiRejectVariable(variableId: string): Promise<VariableDetail> {
  const res = await http.post<VariableDetail>(`/standards/variables/${variableId}/reject`);
  return res.data;
}

export async function apiDeprecateVariable(variableId: string): Promise<VariableDetail> {
  const res = await http.post<VariableDetail>(`/standards/variables/${variableId}/deprecate`);
  return res.data;
}

export async function apiResubmitVariable(variableId: string): Promise<VariableDetail> {
  const res = await http.post<VariableDetail>(`/standards/variables/${variableId}/resubmit`);
  return res.data;
}

export async function apiAddVariableAlias(variableId: string, alias: string): Promise<VariableDetail> {
  const res = await http.post<VariableDetail>(`/standards/variables/${variableId}/aliases`, { alias });
  return res.data;
}

export async function apiConvertUnits(params: {
  value: number;
  from_unit: string;
  to_unit: string;
}): Promise<{ value: number; from_unit: string; to_unit: string }> {
  const res = await http.get<{ value: number; from_unit: string; to_unit: string }>('/standards/units/convert', { params });
  return res.data;
}

// ============================================================
// Objects API（/objects）
// ============================================================

export async function apiCreateObject(body: {
  code: string;
  name_zh: string;
  name_en: string;
  object_type: string;
  description?: string;
  parent_id?: string;
}): Promise<IndustrialObject> {
  const res = await http.post<IndustrialObject>('/objects', body);
  return res.data;
}

export async function apiListObjects(params?: {
  status?: string;
  object_type?: string;
  cursor?: string;
  limit?: number;
}): Promise<CursorPage<IndustrialObject>> {
  const res = await http.get<CursorPage<IndustrialObject>>('/objects', { params });
  return res.data;
}

export async function apiGetObject(objectId: string): Promise<IndustrialObject> {
  const res = await http.get<IndustrialObject>(`/objects/${objectId}`);
  return res.data;
}

export async function apiAddObjectRelation(objectId: string, body: {
  target_id: string;
  relation_type: string;
  description?: string;
}): Promise<ObjectRelation> {
  const res = await http.post<ObjectRelation>(`/objects/${objectId}/relations`, body);
  return res.data;
}

export async function apiRemoveObjectRelation(objectId: string, relationId: string): Promise<void> {
  await http.delete(`/objects/${objectId}/relations`, { params: { relation_id: relationId } });
}

export async function apiListObjectRelations(objectId: string): Promise<ObjectRelation[]> {
  const res = await http.get<ObjectRelation[]>(`/objects/${objectId}/relations`);
  return res.data;
}

/** Alias for apiListObjectRelations */
export const apiGetObjectRelations = apiListObjectRelations;

export async function apiGetObjectDescendants(objectId: string): Promise<IndustrialObject[]> {
  const res = await http.get<IndustrialObject[]>(`/objects/${objectId}/descendants`);
  return res.data;
}

// ============================================================
// Templates API（/templates）
// ============================================================

export async function apiCreateTemplate(body: {
  code: string;
  name_zh: string;
  description?: string;
}): Promise<TemplateSummary> {
  const res = await http.post<TemplateSummary>('/templates', body);
  return res.data;
}

export async function apiListTemplates(params?: {
  status?: string;
  cursor?: string;
  limit?: number;
}): Promise<CursorPage<TemplateSummary>> {
  const res = await http.get<CursorPage<TemplateSummary>>('/templates', { params });
  return res.data;
}

export async function apiGetTemplate(templateId: string): Promise<TemplateSummary> {
  const res = await http.get<TemplateSummary>(`/templates/${templateId}`);
  return res.data;
}

export async function apiSubmitTemplate(templateId: string): Promise<TemplateSummary> {
  const res = await http.post<TemplateSummary>(`/templates/${templateId}/submit`);
  return res.data;
}

export async function apiPublishTemplate(templateId: string): Promise<TemplateSummary> {
  const res = await http.post<TemplateSummary>(`/templates/${templateId}/publish`);
  return res.data;
}

export async function apiRejectTemplate(templateId: string): Promise<TemplateSummary> {
  const res = await http.post<TemplateSummary>(`/templates/${templateId}/reject`);
  return res.data;
}

export async function apiDeprecateTemplate(templateId: string): Promise<TemplateSummary> {
  const res = await http.post<TemplateSummary>(`/templates/${templateId}/deprecate`);
  return res.data;
}

export async function apiAddObservationRequirement(templateId: string, body: {
  variable_id: string;
  required: boolean;
}): Promise<unknown> {
  const res = await http.post(`/templates/${templateId}/observations`, body);
  return res.data;
}

// ============================================================
// Methods API（/methods）
// ============================================================

export async function apiCreateMethod(body: {
  code: string;
  name_zh: string;
  description?: string;
}): Promise<MethodSummary> {
  const res = await http.post<MethodSummary>('/methods', body);
  return res.data;
}

export async function apiListMethods(params?: {
  status?: string;
  cursor?: string;
  limit?: number;
}): Promise<CursorPage<MethodSummary>> {
  const res = await http.get<CursorPage<MethodSummary>>('/methods', { params });
  return res.data;
}

export async function apiGetMethod(methodId: string): Promise<MethodSummary> {
  const res = await http.get<MethodSummary>(`/methods/${methodId}`);
  return res.data;
}

export async function apiSubmitMethod(methodId: string): Promise<MethodSummary> {
  const res = await http.post<MethodSummary>(`/methods/${methodId}/submit`);
  return res.data;
}

export async function apiPublishMethod(methodId: string): Promise<MethodSummary> {
  const res = await http.post<MethodSummary>(`/methods/${methodId}/publish`);
  return res.data;
}

// ============================================================
// Packages API（/packages）
// ============================================================

export async function apiCreatePackage(body: {
  code: string;
  name_zh: string;
  description?: string;
}): Promise<PackageSummary> {
  const res = await http.post<PackageSummary>('/packages', body);
  return res.data;
}

export async function apiListPackages(params?: {
  status?: string;
  cursor?: string;
  limit?: number;
}): Promise<CursorPage<PackageSummary>> {
  const res = await http.get<CursorPage<PackageSummary>>('/packages', { params });
  return res.data;
}

export async function apiGetPackage(packageId: string): Promise<PackageSummary> {
  const res = await http.get<PackageSummary>(`/packages/${packageId}`);
  return res.data;
}

export async function apiAddPackageRef(packageId: string, body: {
  ref_type: string;
  ref_id: string;
}): Promise<unknown> {
  const res = await http.post(`/packages/${packageId}/refs`, body);
  return res.data;
}

export async function apiSubmitPackage(packageId: string): Promise<PackageSummary> {
  const res = await http.post<PackageSummary>(`/packages/${packageId}/submit`);
  return res.data;
}

export async function apiPublishPackage(packageId: string): Promise<PackageSummary> {
  const res = await http.post<PackageSummary>(`/packages/${packageId}/publish`);
  return res.data;
}

export async function apiRejectPackage(packageId: string): Promise<PackageSummary> {
  const res = await http.post<PackageSummary>(`/packages/${packageId}/reject`);
  return res.data;
}

// ============================================================
// Ingestions API（/ingestions）
// ============================================================

export async function apiPreviewIngestion(file: File): Promise<SourcePreview> {
  const formData = new FormData();
  formData.append('file', file);
  const res = await http.post<SourcePreview>('/ingestions/preview', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return res.data;
}

/** V1 preview source — takes source config object instead of File */
export async function apiPreviewSource(body: {
  source_type: 'file' | 'postgres' | 'rest';
  file_id?: string;
  dsn_secret_id?: string;
  base_url_secret_id?: string;
  table_name?: string;
  limit?: number;
}): Promise<SourcePreview> {
  const res = await http.post<SourcePreview>('/ingestions/preview', body);
  return res.data;
}

export async function apiRankMappings(body: {
  columns: string[];
  rows?: Record<string, unknown>[];
}): Promise<MappingRankResponse> {
  const res = await http.post<MappingRankResponse>('/ingestions/mapping/rank', body);
  return res.data;
}

export async function apiCreateMappingProfile(body: {
  name: string;
  mappings: Record<string, string>;
}): Promise<unknown> {
  const res = await http.post('/ingestions/mapping-profiles', body);
  return res.data;
}

export async function apiListMappingProfiles(): Promise<unknown[]> {
  const res = await http.get<unknown[]>('/ingestions/mapping-profiles');
  return res.data;
}

export async function apiGetMappingProfile(profileId: string): Promise<unknown> {
  const res = await http.get<unknown>(`/ingestions/mapping-profiles/${profileId}`);
  return res.data;
}

export async function apiSubmitMappingProfile(profileId: string): Promise<unknown> {
  const res = await http.post(`/ingestions/mapping-profiles/${profileId}/submit`);
  return res.data;
}

export async function apiPublishMappingProfile(profileId: string): Promise<unknown> {
  const res = await http.post(`/ingestions/mapping-profiles/${profileId}/publish`);
  return res.data;
}

export async function apiRejectMappingProfile(profileId: string): Promise<unknown> {
  const res = await http.post(`/ingestions/mapping-profiles/${profileId}/reject`);
  return res.data;
}

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

export async function apiListFacts(params?: {
  cursor?: string;
  limit?: number;
  fact_type?: string;
  status?: string;
}): Promise<CursorPage<FactSummary>> {
  const res = await http.get<CursorPage<FactSummary>>('/facts', { params });
  return res.data;
}

export async function apiSearchFacts(params: {
  q: string;
  cursor?: string;
  limit?: number;
}): Promise<CursorPage<FactSummary>> {
  const res = await http.get<CursorPage<FactSummary>>('/facts/search', { params });
  return res.data;
}

export async function apiGetFact(factId: string): Promise<FactDetail> {
  const res = await http.get<FactDetail>(`/facts/${factId}`);
  return res.data;
}

export async function apiListFactRevisions(factId: string): Promise<FactRevision[]> {
  const res = await http.get<FactRevision[]>(`/facts/${factId}/revisions`);
  return res.data;
}

export async function apiGetFactRevision(factId: string, revision: number): Promise<FactRevision> {
  const res = await http.get<FactRevision>(`/facts/${factId}/revisions/${revision}`);
  return res.data;
}

export async function apiGetFactObservations(factId: string): Promise<Observation[]> {
  const res = await http.get<Observation[]>(`/facts/${factId}/observations`);
  return res.data;
}

export async function apiReviseFact(factId: string, body: {
  value: unknown;
  unit?: string;
  change_note?: string;
}): Promise<FactDetail> {
  const res = await http.post<FactDetail>(`/facts/${factId}/revise`, body);
  return res.data;
}

// ============================================================
// Provenance API（/provenance）
// ============================================================

export async function apiCreateEvidenceSet(body: {
  label: string;
  member_ids: string[];
}): Promise<EvidenceSet> {
  const res = await http.post<EvidenceSet>('/provenance/evidence-sets', body);
  return res.data;
}

export async function apiListEvidenceSets(params?: {
  status?: string;
  cursor?: string;
  limit?: number;
}): Promise<CursorPage<EvidenceSet>> {
  const res = await http.get<CursorPage<EvidenceSet>>('/provenance/evidence-sets', { params });
  return res.data;
}

export async function apiFreezeEvidenceSet(setId: string): Promise<EvidenceSet> {
  const res = await http.post<EvidenceSet>(`/provenance/evidence-sets/${setId}/freeze`);
  return res.data;
}

export async function apiGetEvidenceSet(setId: string): Promise<EvidenceSet> {
  const res = await http.get<EvidenceSet>(`/provenance/evidence-sets/${setId}`);
  return res.data;
}

// Note: apiListEvidenceSets is defined above near apiCreateEvidenceSet — do not redefine.

export async function apiListEvidenceSetMembers(setId: string): Promise<unknown[]> {
  const res = await http.get<unknown[]>(`/provenance/evidence-sets/${setId}/members`);
  return res.data;
}

export async function apiCreateRecipe(body: {
  label: string;
  steps: unknown[];
}): Promise<Recipe> {
  const res = await http.post<Recipe>('/provenance/recipes', body);
  return res.data;
}

export async function apiPublishRecipe(recipeId: string): Promise<Recipe> {
  const res = await http.post<Recipe>(`/provenance/recipes/${recipeId}/publish`);
  return res.data;
}

export async function apiListRecipes(params?: {
  status?: string;
  cursor?: string;
  limit?: number;
}): Promise<CursorPage<Recipe>> {
  const res = await http.get<CursorPage<Recipe>>('/provenance/recipes', { params });
  return res.data;
}

export async function apiGetRecipe(recipeId: string): Promise<Recipe> {
  const res = await http.get<Recipe>(`/provenance/recipes/${recipeId}`);
  return res.data;
}

export async function apiCreateDerivationRun(body: {
  recipe_id: string;
  inputs: Record<string, unknown>;
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
  limit?: number;
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

export async function apiCheckStaleness(parameterId: string): Promise<{ staleness_status: string | null }> {
  const res = await http.get<{ staleness_status: string | null }>(`/parameters/${parameterId}/staleness`);
  return res.data;
}

export async function apiDeprecateParameter(parameterId: string): Promise<ParameterDetail> {
  const res = await http.post<ParameterDetail>(`/parameters/${parameterId}/deprecate`);
  return res.data;
}

// ============================================================
// Equipment API（/equipment）— 设备仪器管理
// ============================================================

/** 设备仪器详情 */
export type Equipment = {
  id: string;
  organization_id: string;
  code: string;
  display_name: string;
  description: string | null;
  department_id: string;
  status: string;
  sort_order: number;
  created_at: string;
  updated_at: string;
  lock_version: number;
};

/** 设备仪器列表项（含部门名 + 物理量数） */
export type EquipmentListItem = {
  id: string;
  code: string;
  display_name: string;
  department_id: string;
  department_name: string;
  status: string;
  sort_order: number;
  variable_count: number;
};

/** 设备仪器分页列表响应 */
export type EquipmentListResponse = {
  items: EquipmentListItem[];
  next_cursor: string | null;
  has_more: boolean;
};

/** 设备物理量列表项 */
export type EquipmentVariable = {
  id: string;
  code: string;
  name_zh: string;
  name_en: string;
  quantity_kind: string;
  data_type: string;
  status: string;
  current_version: string | null;
};

export async function apiListEquipment(params?: {
  department_id?: string;
  status?: string;
  cursor?: string;
  limit?: number;
}): Promise<EquipmentListResponse> {
  const res = await http.get<EquipmentListResponse>('/equipment', { params });
  return res.data;
}

export async function apiGetEquipment(id: string): Promise<Equipment> {
  const res = await http.get<Equipment>(`/equipment/${id}`);
  return res.data;
}

export async function apiCreateEquipment(body: {
  code: string;
  display_name: string;
  description?: string;
  department_id: string;
  sort_order?: number;
}): Promise<Equipment> {
  const res = await http.post<Equipment>('/equipment', body);
  return res.data;
}

export async function apiUpdateEquipment(
  id: string,
  body: {
    display_name: string;
    description?: string;
    department_id?: string;
    sort_order?: number;
    lock_version: number;
  },
): Promise<Equipment> {
  const res = await http.patch<Equipment>(`/equipment/${id}`, body);
  return res.data;
}

export async function apiUpdateEquipmentStatus(
  id: string,
  body: { status: string; lock_version: number },
): Promise<Equipment> {
  const res = await http.patch<Equipment>(`/equipment/${id}/status`, body);
  return res.data;
}

export async function apiGetEquipmentVariables(
  id: string,
): Promise<EquipmentVariable[]> {
  const res = await http.get<EquipmentVariable[]>(`/equipment/${id}/variables`);
  return res.data;
}

export async function apiSetEquipmentVariables(
  id: string,
  body: { variable_ids: string[] },
): Promise<{ ok: boolean }> {
  const res = await http.put<{ ok: boolean }>(`/equipment/${id}/variables`, body);
  return res.data;
}

