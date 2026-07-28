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

export async function apiCancelJob(id: string): Promise<{ job_id: string; status: string; kind: string }> {
  const res = await http.post<{ job_id: string; status: string; kind: string }>(`/jobs/${id}/cancel`);
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
  parent_id: string | null;
};

/** 实验室列表项（含成员数、子部门数、仪器数） */
export type DepartmentListItem = {
  id: string;
  code: string;
  display_name: string;
  description: string | null;
  status: string;
  sort_order: number;
  member_count: number;
  parent_id: string | null;
  children_count: number;
  equipment_count: number;
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

/** 部门名称映射项（仅 id + display_name，不受部门隔离限制） */
export type DepartmentNameMapItem = {
  id: string;
  display_name: string;
};

/**
 * 获取全部门 ID→名称映射（不受部门隔离限制）。
 *
 * 专用于前端名称展示场景（如设备可见单位列渲染），只返回 id 和
 * display_name，不含敏感数据。所有有 department:read 权限的用户可调用。
 */
export async function apiGetDepartmentNameMap(): Promise<DepartmentNameMapItem[]> {
  const res = await http.get<DepartmentNameMapItem[]>('/departments/name-map');
  return res.data;
}

export async function apiGetDepartment(id: string): Promise<Department> {
  const res = await http.get<Department>(`/departments/${id}`);
  return res.data;
}

export async function apiCreateDepartment(body: {
  display_name: string;
  description?: string;
  sort_order?: number;
  parent_id?: string | null;
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
    parent_id?: string | null;
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

export async function apiDeleteDepartment(id: string): Promise<void> {
  await http.delete(`/departments/${id}`);
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
  display_name: string;
  canonical_unit: string | null;
  quantity_kind: string | null;
  data_type: string;
  status: string;
  version_count: number;
  created_at: string;
  updated_at: string;
  lock_version: number;
};

export type VariableDetail = VariableSummary & {
  description: string | null;
  canonical_unit: string | null;
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
  display_name: string;
  object_type: string;
  description: string | null;
  status: string;
  parent_id: string | null;
  equipment_id: string | null;
  department_id: string | null;
  visible_departments: string[];
  created_at: string;
  updated_at: string;
  lock_version: number;
};

export type ObjectRelation = {
  id: string;
  source_id: string;
  target_id: string;
  relation_type: string;
  is_active: boolean;
  created_at: string;
};

/** 后端 /objects/{id}/descendants 实际返回结构 */
export type DescendantsResponse = {
  root_id: string;
  descendant_ids: string[];
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
  fact_id: string;
  revision: number;
  revision_id: string;
  fact_type: string;
  subject_id: string;
  status: string;
  task_code: string | null;
  task_name: string | null;
  department_name: string | null;
  operator: string | null;
  data_summary: string | null;
};

export type FactDetail = {
  fact_id: string;
  revision: number;
  revision_id: string;
  fact_type: string;
  subject_id: string;
  status: string;
};

export type FactRevision = {
  fact_id: string;
  revision: number;
  revision_id: string;
  fact_type: string;
  subject_id: string;
  status: string;
};

export type RawObservation = {
  id: string;
  fact_revision_id: string;
  source_path: string;
  source_value: string;
  source_unit: string | null;
  source_name: string | null;
  artifact_id: string | null;
};

export type NormalizedObservation = {
  id: string;
  fact_revision_id: string;
  variable_version_id: string;
  raw_observation_id: string;
  value: string;
  unit: string | null;
};

export type ObservationsResponse = {
  raw: RawObservation[];
  normalized: NormalizedObservation[];
};

// ---- Provenance ----
export type ProvenanceNode = {
  id: string;
  node_type: string;
  label: string;
  version: string;
  status: string;
};

export type ProvenanceEdge = {
  source_id: string;
  source_type: string;
  target_id: string;
  target_type: string;
  edge_type: string;
};

export type ProvenanceGraph = {
  nodes: ProvenanceNode[];
  edges: ProvenanceEdge[];
};

export type EvidenceSet = {
  set_id: string;
  name: string;
  status: string;
  version: number;
  version_id: string | null;
  member_count: number;
};

export type Recipe = {
  recipe_id: string;
  code: string;
  display_name: string;
  status: string;
  version: number;
};

export type DerivationRun = {
  id: string;
  status: string;
  output_digest: string;
  outputs: DerivationRunOutput[];
};

export type DerivationRunOutput = {
  variable_code: string;
  value: string;
  unit: string | null;
  confidence: number;
  exclusion_reasons: string[];
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
  display_name: string;
  data_type: string;
  canonical_unit?: string;
  quantity_kind?: string;
  valid_range?: string[];
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
  display_name: string;
  object_type: string;
  description?: string;
  parent_id?: string;
  equipment_id?: string;
  department_id?: string;
  visible_departments?: string[];
}): Promise<IndustrialObject> {
  const res = await http.post<IndustrialObject>('/objects', body);
  return res.data;
}

export async function apiListObjects(params?: {
  object_type?: string;
  cursor?: string;
  page_size?: number;
}): Promise<CursorPage<IndustrialObject>> {
  const res = await http.get<CursorPage<IndustrialObject>>('/objects', { params });
  return res.data;
}

export async function apiGetObject(objectId: string): Promise<IndustrialObject> {
  const res = await http.get<IndustrialObject>(`/objects/${objectId}`);
  return res.data;
}

export async function apiUpdateObject(objectId: string, body: {
  display_name: string;
  description?: string | null;
  object_type?: string;
  equipment_id?: string | null;
  department_id?: string | null;
  visible_departments?: string[] | null;
}): Promise<IndustrialObject> {
  const res = await http.patch<IndustrialObject>(`/objects/${objectId}`, body);
  return res.data;
}

export async function apiUpdateObjectStatus(objectId: string, body: {
  status: 'active' | 'inactive';
}): Promise<IndustrialObject> {
  const res = await http.patch<IndustrialObject>(`/objects/${objectId}/status`, body);
  return res.data;
}

export async function apiDeleteObject(objectId: string): Promise<void> {
  await http.delete(`/objects/${objectId}`);
}

// ============================================================
// Object Types API（/object-types）— 实验对象类型管理
// ============================================================

export type ObjectTypeDictItem = {
  id: string;
  code: string;
  display_name: string;
  description: string | null;
  sort_order: number;
  created_at: string;
  updated_at: string;
};

export async function apiListObjectTypes(): Promise<ObjectTypeDictItem[]> {
  const res = await http.get<ObjectTypeDictItem[]>('/object-types');
  return res.data;
}

export async function apiCreateObjectType(body: {
  display_name: string;
  description?: string;
}): Promise<ObjectTypeDictItem> {
  const res = await http.post<ObjectTypeDictItem>('/object-types', body);
  return res.data;
}

export async function apiUpdateObjectType(
  typeId: string,
  body: { display_name?: string; description?: string },
): Promise<ObjectTypeDictItem> {
  const res = await http.patch<ObjectTypeDictItem>(`/object-types/${typeId}`, body);
  return res.data;
}

export async function apiDeleteObjectType(typeId: string): Promise<void> {
  await http.delete(`/object-types/${typeId}`);
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

export async function apiGetObjectDescendants(objectId: string): Promise<DescendantsResponse> {
  const res = await http.get<DescendantsResponse>(`/objects/${objectId}/descendants`);
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

export async function apiListFactRevisions(factId: string): Promise<{ items: FactRevision[]; next_cursor: string | null }> {
  const res = await http.get<{ items: FactRevision[]; next_cursor: string | null }>(`/facts/${factId}/revisions`);
  return res.data;
}

export async function apiGetFactRevision(factId: string, revision: number): Promise<FactRevision> {
  const res = await http.get<FactRevision>(`/facts/${factId}/revisions/${revision}`);
  return res.data;
}

export async function apiGetFactObservations(factId: string): Promise<ObservationsResponse> {
  const res = await http.get<ObservationsResponse>(`/facts/${factId}/observations`);
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
  project_name: string | null;
  department_name: string | null;
  data_interface: string | null;
  created_at: string | null;
  experimental_object_codes: string[] | null;
  data_source_list?: DataSourceItem[];
};

export type FactData = {
  metadata: Record<string, unknown>;
  /** 新格式：单点数据 */
  points?: { name: string; value: unknown; unit: string | null }[];
  /** 新格式：序列数据 */
  series?: { name: string; columns: string[]; rows: unknown[][] }[];
  /** 旧格式兼容：多行数据（旧数据存为 data，新数据存为 points + series） */
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

// Note: apiListEvidenceSets is defined above near apiCreateEvidenceSet — do not redefine.

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
  visible_departments: string[];
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
  description: string | null;
  department_id: string;
  department_name: string;
  visible_departments: string[];
  status: string;
  sort_order: number;
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
  display_name: string;
  description?: string;
  department_id: string;
  visible_departments?: string[];
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
    visible_departments?: string[];
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

export async function apiDeleteEquipment(id: string): Promise<void> {
  await http.delete(`/equipment/${id}`);
}

// ============================================================
// V2 组件管理 API（/components）— IRIP V2-T01
// ============================================================

/** 组件摘要（列表项）。 */
export type ComponentSummary = {
  id: string;
  name: string;
  display_name: string;
  description: string;
  version: string;
  kind: string;
  runtime: string;
  engine: string;
  experimental_object_code: string;
  status: string;
  manifest_sha256: string;
  published_at: string | null;
  created_at: string;
  prompt?: string | null;
};

/** 组件详情（含 manifest 全文 + 可选解析字段）。 */
export type ComponentDetail = ComponentSummary & {
  manifest_yaml: string;
  active_version_id?: string | null;
  /** 清单中声明的参数（从 manifest_yaml 解析；后端可能不直接返回）。 */
  parameters?: Record<string, unknown>;
  /** 清单中声明的输入端口（从 manifest_yaml 解析；后端可能不直接返回）。 */
  inputs?: unknown[];
  /** 清单中声明的输出端口（从 manifest_yaml 解析；后端可能不直接返回）。 */
  outputs?: unknown[];
};

export async function apiListComponents(params?: {
  kind?: string;
  status?: string;
}): Promise<CursorPage<ComponentSummary>> {
  const res = await http.get<{ items: ComponentSummary[] }>('/components/', { params });
  // 后端 ComponentListResponse 仅含 items（无分页游标），适配为 CursorPage
  return { items: res.data.items, next_cursor: null, has_more: false };
}

export async function apiGetComponent(id: string): Promise<ComponentDetail> {
  const res = await http.get<ComponentDetail>(`/components/${id}`);
  return res.data;
}

export async function apiPublishComponent(body: {
  manifest_yaml: string;
  experimental_object_code?: string | null;
}): Promise<ComponentSummary> {
  const res = await http.post<ComponentSummary>('/components/', body);
  return res.data;
}

/** 组件版本列表项。 */
export type ComponentVersionItem = {
  id: string;
  version: string;
  status: string;
  manifest_sha256: string;
  created_at: string;
};

export async function apiListComponentVersions(
  componentId: string,
): Promise<ComponentVersionItem[]> {
  const res = await http.get<ComponentVersionItem[]>(
    `/components/${componentId}/versions`,
  );
  return res.data;
}

export async function apiArchiveComponent(componentId: string): Promise<void> {
  await http.patch(`/components/${componentId}/archive`);
}

export async function apiRestoreComponent(componentId: string): Promise<void> {
  await http.patch(`/components/${componentId}/restore`);
}

export async function apiActivateVersion(versionId: string): Promise<void> {
  await http.post(`/components/${versionId}/activate`);
}

export async function apiDeleteComponent(componentId: string): Promise<void> {
  await http.delete(`/components/${componentId}`);
}

export type PersistFactResult = {
  fact_id: string;
  revision: number;
  subject_id: string;
  raw_count: number;
  artifact_id: string | null;
};

export async function apiPersistRunAsFact(
  runId: string,
  body: {
    object_id: string;
    template_version_id?: string | null;
    custom_data?: { metadata: Record<string, unknown>; points?: { name: string; value: unknown; unit: string | null }[]; series?: unknown[]; data?: Record<string, unknown>[] } | null;
  },
): Promise<PersistFactResult> {
  const res = await http.post<PersistFactResult>(
    `/flows/runs/${runId}/persist-fact`,
    body,
  );
  return res.data;
}

// ============================================================
// V2 流程编排 API（/flows）— IRIP V2-T03
// ============================================================

/** 流程定义摘要。 */
export type FlowSummary = {
  id: string;
  code: string;
  display_name: string;
  status: string;
  lock_version: number;
  department_id: string | null;
  project_name: string | null;
  operator: string | null;
  created_at: string;
  updated_at: string;
  latest_version: {
    id: string;
    version: number;
    digest: string;
    status: string;
    published_at: string | null;
    nodes?: Record<string, unknown>[];
    edges?: Record<string, unknown>[];
    random_seed?: number;
  } | null;
};

/** 流程版本（发布端点返回）。 */
export type FlowVersion = {
  id: string;
  flow_definition_id: string;
  version: number;
  digest: string;
  random_seed: number;
  status: string;
  published_at: string | null;
  created_at: string;
  nodes: unknown[];
  edges: unknown[];
};

/** 流程运行摘要。 */
export type FlowRunSummary = {
  id: string;
  flow_version_id: string;
  status: string;
  job_id: string | null;
  output_digest: string | null;
  output_summary: Record<string, unknown> | null;
  error_message?: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  persisted_as_fact?: boolean;
};

/** 节点执行记录。 */
export type FlowNodeExecution = {
  id: string;
  node_id: string;
  status: string;
  input_summary: Record<string, unknown> | null;
  output_summary: Record<string, unknown> | null;
  diagnostics: Record<string, unknown> | null;
  duration_ms: number | null;
  started_at: string | null;
  completed_at: string | null;
};

/** 流程运行详情（含节点执行列表）。 */
export type FlowRunDetail = FlowRunSummary & {
  node_executions: FlowNodeExecution[];
  nodes: FlowNodeExecution[];
};

/** 流程节点定义（请求体）。 */
export type FlowNodeSchema = {
  node_id: string;
  component_name: string;
  component_version: string;
  params?: Record<string, unknown>;
  input_bindings?: Record<string, string>;
};

/** 流程边定义（请求体）。 */
export type FlowEdgeSchema = {
  source_node: string;
  source_port: string;
  target_node: string;
  target_port: string;
};

export async function apiCreateFlow(body: {
  display_name: string;
  department_id?: string | null;
  project_name?: string | null;
  operator: string;
  nodes?: FlowNodeSchema[];
  edges?: FlowEdgeSchema[];
}): Promise<FlowSummary> {
  const res = await http.post<FlowSummary>('/flows/', body);
  return res.data;
}

export async function apiPublishFlow(
  flowId: string,
  body: { nodes: FlowNodeSchema[]; edges?: FlowEdgeSchema[]; random_seed?: number },
): Promise<FlowSummary> {
  // 后端发布端点返回 FlowVersionResponse；此处发布后重新获取定义，
  // 返回含最新版本摘要的 FlowSummary（满足 UI 刷新需求）。
  await http.post(`/flows/${flowId}/publish`, body);
  return apiGetFlow(flowId);
}

export async function apiListFlows(params?: {
  status?: string;
}): Promise<CursorPage<FlowSummary>> {
  const res = await http.get<{ items: FlowSummary[] }>('/flows/', { params });
  return { items: res.data.items, next_cursor: null, has_more: false };
}

export async function apiGetFlow(flowId: string): Promise<FlowSummary> {
  const res = await http.get<FlowSummary>(`/flows/${flowId}`);
  return { ...res.data, latest_version: res.data.latest_version ?? null };
}

export async function apiArchiveFlow(flowId: string): Promise<FlowSummary> {
  const res = await http.post<FlowSummary>(`/flows/${flowId}/archive`);
  return { ...res.data, latest_version: res.data.latest_version ?? null };
}

export async function apiRestoreFlow(flowId: string): Promise<FlowSummary> {
  const res = await http.post<FlowSummary>(`/flows/${flowId}/restore`);
  return { ...res.data, latest_version: res.data.latest_version ?? null };
}

export async function apiDeleteFlow(flowId: string): Promise<void> {
  await http.delete(`/flows/${flowId}`);
}

export async function apiUpdateFlow(flowId: string, displayName: string, departmentId?: string | null, projectName?: string | null, operator?: string | null): Promise<FlowSummary> {
  const res = await http.patch<FlowSummary>(`/flows/${flowId}`, { display_name: displayName, department_id: departmentId ?? null, project_name: projectName ?? null, operator: operator ?? null });
  return { ...res.data, latest_version: res.data.latest_version ?? null };
}

/** 事实模板版本列表项 — 对应后端 TemplateSummary */
export type FactTemplateVersionItem = {
  id: string;
  code: string;
  display_name: string;
  fact_type: string;
  status: string;
  version_count: number;
  latest_version: {
    id: string;
    template_id: string;
    version: number;
    display_name: string;
    fact_type: string;
  } | null;
};

export async function apiListFactTemplateVersions(): Promise<FactTemplateVersionItem[]> {
  const res = await http.get<{ items: FactTemplateVersionItem[] }>('/templates', {
    params: { page_size: 100 },
  });
  return res.data.items;
}

export async function apiCreateFlowRun(
  flowId: string,
  body: { inputs?: Record<string, unknown> },
): Promise<FlowRunSummary> {
  const res = await http.post<FlowRunSummary>(`/flows/${flowId}/runs`, body);
  return res.data;
}

export async function apiListFlowRuns(flowId: string): Promise<FlowRunSummary[]> {
  const res = await http.get<FlowRunSummary[]>(`/flows/${flowId}/runs`);
  return res.data;
}

export async function apiResumeFlowRun(runId: string): Promise<FlowRunSummary> {
  const res = await http.post<FlowRunSummary>(`/flows/runs/${runId}/resume`);
  return res.data;
}

export async function apiCancelFlowRun(runId: string): Promise<FlowRunSummary> {
  const res = await http.post<FlowRunSummary>(`/flows/runs/${runId}/cancel`);
  return res.data;
}

export async function apiRetryFlowNode(
  runId: string,
  nodeId: string,
): Promise<FlowRunSummary> {
  // 后端重试端点返回单节点执行记录；此处重试后重新获取运行详情，
  // 返回最新运行状态（FlowRunDetail 兼容 FlowRunSummary）。
  await http.post(`/flows/runs/${runId}/retry/${encodeURIComponent(nodeId)}`);
  return apiGetFlowRun(runId);
}

export async function apiDeleteFlowRun(runId: string): Promise<void> {
  await http.delete(`/flows/runs/${runId}`);
}

export async function apiGetFlowRun(runId: string): Promise<FlowRunDetail> {
  const res = await http.get<{
    id: string;
    flow_version_id: string;
    status: string;
    job_id: string | null;
    output_digest: string | null;
    started_at: string | null;
    completed_at: string | null;
    created_at: string;
    node_executions: FlowNodeExecution[];
  }>(`/flows/runs/${runId}`);
  // 后端字段 node_executions → 前端字段 nodes（兼容两个字段名）
  return {
    ...res.data,
    output_summary: null,
    nodes: res.data.node_executions,
  };
}

// ============================================================
// V2 模型管理 API（/models）— IRIP V2-T04
// ============================================================

// ============================================================
// 文件浏览 API（/files）— 组件参数文件选择器
// ============================================================

export type FileItem = {
  name: string;
  type: string; // "file" | "dir"
  size: number | null;
};

export type BrowseResponse = {
  current_path: string;
  parent_path: string | null;
  items: FileItem[];
};

export async function apiBrowseFiles(path?: string): Promise<BrowseResponse> {
  const res = await http.get<BrowseResponse>('/files/browse', {
    params: path ? { path } : {},
  });
  return res.data;
}

// ============================================================
// 文件上传 API（/files/upload）— 组件参数文件上传
// ============================================================

/** 文件上传响应。 */
export type UploadResponse = {
  artifact_id: string;
  filename: string;
  size: number;
};

/**
 * 上传文件到 MinIO，返回 artifact_id 供后续使用。
 *
 * 用于流程执行时文件参数的上传：用户在浏览器选择本地文件，
 * 上传到服务器 MinIO，返回 artifact_id 后以 `artifact:{id}` 格式填入参数值。
 */
export async function apiUploadFile(file: File): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append('file', file);
  // 不手动设 Content-Type，让浏览器自动设 multipart/form-data + boundary
  const res = await http.post<UploadResponse>('/files/upload', formData);
  return res.data;
}

/** 获取 artifact 预签名下载 URL */
export async function apiGetArtifactDownloadUrl(artifactId: string): Promise<string> {
  const res = await http.get<{ download_url: string }>(`/artifacts/${artifactId}/download`);
  return res.data.download_url;
}

// ============================================================
// V2 模型管理 API — end
// ============================================================

/** 模型摘要。 */
export type ModelSummary = {
  id: string;
  code: string;
  display_name: string;
  status: string;
  current_version_id: string | null;
  lock_version: number;
  created_at: string;
  updated_at: string;
};

/** 模型版本摘要。 */
export type ModelVersionSummary = {
  id: string;
  model_id: string;
  version: number;
  status: string;
  contract_sha256: string | null;
  model_artifact_id: string | null;
  metrics: Record<string, unknown>;
  applicability_domain: Record<string, unknown>;
  code_hash: string | null;
  dependency_hash: string | null;
  model_hash: string | null;
  created_at: string;
  published_at: string | null;
};

/** 预测结果。 */
export type PredictionResult = {
  model_id: string;
  model_version_id: string;
  version: number;
  predictions: Record<string, unknown>;
  metadata: Record<string, unknown>;
  fact_id: string | null;
};

export async function apiCreateModel(body: {
  code: string;
  display_name: string;
}): Promise<ModelSummary> {
  const res = await http.post<ModelSummary>('/models/', body);
  return res.data;
}

export async function apiListModels(params?: {
  status?: string;
}): Promise<CursorPage<ModelSummary>> {
  const res = await http.get<{ items: ModelSummary[] }>('/models/', { params });
  return { items: res.data.items, next_cursor: null, has_more: false };
}

export async function apiGetModel(modelId: string): Promise<ModelSummary> {
  const res = await http.get<ModelSummary>(`/models/${modelId}`);
  return res.data;
}

export async function apiGetModelVersions(
  modelId: string,
): Promise<ModelVersionSummary[]> {
  const res = await http.get<{ items: ModelVersionSummary[] }>(
    `/models/${modelId}/versions`,
  );
  return res.data.items;
}

export async function apiValidateModelVersion(
  modelId: string,
  versionId: string,
  body: {
    dataset_artifact_id?: string;
    metrics?: Record<string, unknown>;
    applicability_domain?: Record<string, unknown>;
  },
): Promise<ModelVersionSummary> {
  const res = await http.post<ModelVersionSummary>(
    `/models/${modelId}/versions/${versionId}/validate`,
    body,
  );
  return res.data;
}

export async function apiPublishModelVersion(
  modelId: string,
  versionId: string,
): Promise<ModelSummary> {
  const res = await http.post<ModelSummary>(
    `/models/${modelId}/versions/${versionId}/publish`,
  );
  return res.data;
}

export async function apiRollbackModel(
  modelId: string,
  targetVersionId: string,
): Promise<ModelSummary> {
  const res = await http.post<ModelSummary>(`/models/${modelId}/rollback`, {
    target_version_id: targetVersionId,
  });
  return res.data;
}

export async function apiPredictModel(
  modelId: string,
  body: { inputs: Record<string, unknown> },
): Promise<PredictionResult> {
  const res = await http.post<PredictionResult>(`/models/${modelId}/predict`, body);
  return res.data;
}

export async function apiDeprecateModel(modelId: string): Promise<ModelSummary> {
  const res = await http.post<ModelSummary>(`/models/${modelId}/deprecate`);
  return res.data;
}

// ============================================================
// AI 助手（IRIP V3-T01）
// ============================================================

/** 工具调用摘要 */
export type ToolCallSummary = {
  tool: string;
  args: Record<string, unknown>;
  summary: string;
  status: string;
};

/** 引用项 */
export type Citation = {
  object_type: string;
  object_id: string;
  version: string;
  label: string;
  href: string;
};

/** 对话摘要 */
export type ConversationSummary = {
  id: string;
  title: string;
  provider_mode: string;
  pinned: boolean;
  archived: boolean;
  created_at: string;
  updated_at: string;
  system_context: string | null;
};

/** AI 消息 */
export type AssistantMessage = {
  id: string;
  conversation_id: string;
  role: 'user' | 'assistant' | 'tool';
  content: string;
  tool_calls: ToolCallSummary[];
  citations: Citation[];
  uncertainty: string | null;
  created_at: string;
};

/** 问答响应 */
export type AskResponse = {
  conversation_id: string;
  answer: string;
  tool_calls: ToolCallSummary[];
  citations: Citation[];
  uncertainty: string | null;
  provider_mode: string;
};

/** 工具信息 */
export type ToolInfo = {
  name: string;
  display_name: string;
  description: string;
  required_permission: string;
  candidate: boolean;
};

/** Provider 状态 */
export type ProviderStatus = {
  provider_mode: string;
  whitelist_tools: ToolInfo[];
  candidate_tools: ToolInfo[];
};

/** 后端 /assistant/conversations 实际返回的原始结构 */
type ConversationApiResponse = {
  id: string;
  title: string;
  provider_mode: string;
  pinned: boolean;
  archived: boolean;
  created_at: string;
  updated_at: string;
  system_context: string | null;
};

/** 后端 /assistant/conversations/{id}/messages 实际返回的原始结构 */
type MessageListApiResponse = {
  items: Array<{
    id: string;
    conversation_id: string;
    role: string;
    content: string;
    tool_calls: ToolCallSummary[];
    citations: Citation[];
    uncertainty: string | null;
    created_at: string;
  }>;
};

/** 后端 /assistant/conversations 列表实际返回的原始结构 */
type ConversationListApiResponse = {
  items: ConversationApiResponse[];
};

/** 后端 /assistant/provider-status 实际返回的原始结构 */
type ProviderStatusApiResponse = {
  provider_mode: string;
  whitelist_tools: ToolInfo[];
  candidate_tools: ToolInfo[];
};

/** 后端 /assistant/conversations/{id}/messages POST 实际返回的原始结构 */
type AskApiResponse = {
  conversation_id: string;
  answer: string;
  tool_calls: ToolCallSummary[];
  citations: Citation[];
  uncertainty: string | null;
  provider_mode: string;
};

/**
 * 创建对话
 */
export async function apiCreateConversation(
  body: { title?: string; provider_mode?: string },
): Promise<ConversationSummary> {
  const res = await http.post<ConversationApiResponse>(
    '/assistant/conversations',
    { title: body.title ?? '', provider_mode: body.provider_mode ?? 'offline' },
  );
  return res.data;
}

/**
 * 列出对话
 */
export async function apiListConversations(
  params?: { limit?: number; includeArchived?: boolean; archivedOnly?: boolean },
): Promise<ConversationSummary[]> {
  const res = await http.get<ConversationListApiResponse>(
    '/assistant/conversations',
    { params: {
      limit: params?.limit ?? 50,
      include_archived: params?.includeArchived ?? false,
      archived_only: params?.archivedOnly ?? false,
    } },
  );
  return res.data.items;
}

export async function apiTogglePin(conversationId: string): Promise<ConversationSummary> {
  const res = await http.patch<ConversationSummary>(`/assistant/conversations/${conversationId}/pin`);
  return res.data;
}

export async function apiToggleArchive(conversationId: string): Promise<ConversationSummary> {
  const res = await http.patch<ConversationSummary>(`/assistant/conversations/${conversationId}/archive`);
  return res.data;
}

export async function apiDeleteConversation(conversationId: string): Promise<void> {
  await http.delete(`/assistant/conversations/${conversationId}`);
}

export async function apiCancelRequest(conversationId: string): Promise<void> {
  await http.post(`/assistant/conversations/${conversationId}/cancel`);
}

/**
 * 发送消息并获取 AI 回答
 */
export async function apiSendMessage(
  conversationId: string,
  body: { question: string; provider_name?: string; thinking_enabled?: boolean; system_context?: string },
  signal?: AbortSignal,
): Promise<AskResponse> {
  const res = await http.post<AskApiResponse>(
    `/assistant/conversations/${conversationId}/messages`,
    { question: body.question, provider_name: body.provider_name ?? 'openai_compatible', thinking_enabled: body.thinking_enabled ?? false, system_context: body.system_context ?? null },
    { signal },
  );
  return res.data;
}

/**
 * 列出对话消息
 */
export async function apiListMessages(
  conversationId: string,
): Promise<AssistantMessage[]> {
  const res = await http.get<MessageListApiResponse>(
    `/assistant/conversations/${conversationId}/messages`,
  );
  return res.data.items.map((m) => ({
    ...m,
    role: (m.role as 'user' | 'assistant' | 'tool') ?? 'user',
  }));
}

/**
 * 获取 Provider 状态
 */
export async function apiGetProviderStatus(): Promise<ProviderStatus> {
  const res = await http.get<ProviderStatusApiResponse>(
    '/assistant/provider-status',
  );
  return res.data;
}

// ============================================================
// V3 治理 API（/governance）— IRIP V3-T02
// ============================================================

/** 用户列表项 */
export type UserListItem = {
  id: string;
  email: string;
  display_name: string;
  roles: string[];
  status: string;
  department_id: string | null;
  created_at: string;
  updated_at: string;
};

/** 用户列表分页响应 */
export type UserListResponse = {
  items: UserListItem[];
  next_cursor: string | null;
  has_more: boolean;
};

/** 后端 /governance/users 原始结构 */
type UserListApiResponse = {
  items: Array<{
    id: string;
    email: string;
    display_name: string;
    roles: string[];
    status: string;
    department_id: string | null;
    created_at: string;
    updated_at: string;
  }>;
  next_cursor: string | null;
  has_more: boolean;
};

/**
 * 列出用户
 */
export async function apiListUsers(params?: {
  status?: string;
  cursor?: string;
  limit?: number;
}): Promise<UserListResponse> {
  const res = await http.get<UserListApiResponse>('/governance/users', {
    params,
  });
  return {
    items: res.data.items.map((u) => ({
      id: u.id,
      email: u.email,
      display_name: u.display_name,
      roles: u.roles ?? [],
      status: u.status,
      department_id: u.department_id ?? null,
      created_at: u.created_at,
      updated_at: u.updated_at,
    })),
    next_cursor: res.data.next_cursor,
    has_more: res.data.has_more,
  };
}

/**
 * 新建用户
 */
export async function apiCreateUser(params: {
  email: string;
  display_name: string;
  password: string;
  roles: string[];
  department_id?: string;
}): Promise<UserListItem> {
  const res = await http.post<UserListItem>('/governance/users', params);
  return res.data;
}

/**
 * 编辑用户（邮箱不可修改）
 */
export async function apiUpdateUser(
  userId: string,
  params: {
    display_name?: string;
    password?: string;
    roles?: string[];
    department_id?: string | null;
  },
): Promise<UserListItem> {
  const res = await http.patch<UserListItem>(`/governance/users/${userId}`, params);
  return res.data;
}

/**
 * 分配角色（合并到已有角色列表）
 */
export async function apiAssignRoles(
  userId: string,
  roles: string[],
): Promise<UserListItem> {
  const res = await http.post<UserListItem>(
    `/governance/users/${userId}/roles`,
    { roles },
  );
  return res.data;
}

/**
 * 移除角色
 */
export async function apiRemoveRole(
  userId: string,
  role: string,
): Promise<UserListItem> {
  const res = await http.delete<UserListItem>(
    `/governance/users/${userId}/roles/${encodeURIComponent(role)}`,
  );
  return res.data;
}

/**
 * 更新用户状态
 */
export async function apiUpdateUserStatus(
  userId: string,
  status: 'active' | 'disabled',
): Promise<UserListItem> {
  const res = await http.patch<UserListItem>(
    `/governance/users/${userId}/status`,
    { status },
  );
  return res.data;
}

/**
 * 删除用户
 */
export async function apiDeleteUser(userId: string): Promise<void> {
  await http.delete(`/governance/users/${userId}`);
}

// ============================================================
// V3 审计 API（/audit-events）— IRIP V3-T02
// ============================================================

/** 审计事件列表项 */
export type AuditEventItem = {
  id: string;
  occurred_at: string;
  actor_user_id: string | null;
  organization_id: string;
  action: string;
  resource_type: string | null;
  resource_id: string | null;
  payload: Record<string, unknown> | null;
  ip: string | null;
  user_agent: string | null;
};

/** 审计事件分页响应 */
export type AuditEventListResponse = {
  items: AuditEventItem[];
  next_cursor: string | null;
  has_more: boolean;
};

/** 后端 /audit-events 原始结构 */
type AuditEventListApiResponse = {
  items: Array<{
    id: string;
    occurred_at: string;
    actor_user_id: string | null;
    organization_id: string;
    action: string;
    resource_type: string | null;
    resource_id: string | null;
    payload: Record<string, unknown> | null;
    ip: string | null;
    user_agent: string | null;
  }>;
  next_cursor: string | null;
  has_more: boolean;
};

/** 审计导出响应 */
export type AuditExportResponse = {
  job_id: string;
  status: string;
  kind: string;
};

/**
 * 查询审计事件
 */
export async function apiListAuditEvents(params: {
  object_type?: string;
  object_id?: string;
  user_id?: string;
  action?: string;
  start_date?: string;
  end_date?: string;
  cursor?: string;
  limit?: number;
}): Promise<AuditEventListResponse> {
  const res = await http.get<AuditEventListApiResponse>('/audit-events/', {
    params,
  });
  return {
    items: res.data.items.map((e) => ({
      id: e.id,
      occurred_at: e.occurred_at,
      actor_user_id: e.actor_user_id,
      organization_id: e.organization_id,
      action: e.action,
      resource_type: e.resource_type,
      resource_id: e.resource_id,
      payload: e.payload,
      ip: e.ip,
      user_agent: e.user_agent,
    })),
    next_cursor: res.data.next_cursor,
    has_more: res.data.has_more,
  };
}

/**
 * 创建审计导出作业
 */
export async function apiCreateAuditExport(body: {
  object_type: string | null;
  object_id: string | null;
  user_id: string | null;
  action: string | null;
  start_date: string | null;
  end_date: string | null;
  format: string;
}): Promise<AuditExportResponse> {
  const res = await http.post<AuditExportResponse>(
    '/audit-events/export',
    body,
  );
  return res.data;
}

// ============================================================
// V3 作业 API 扩展（/jobs）— IRIP V3-T02
// ============================================================

/** 作业列表项（扩展） */
export type JobListItem = {
  id: string;
  kind: string;
  status: string;
  stage: string;
  progress: number;
  retryable: boolean;
  created_at: string;
  attempt: number;
  max_attempts: number;
};

/** 作业列表分页响应 */
export type JobListResponse = {
  items: JobListItem[];
  next_cursor: string | null;
  has_more: boolean;
};

/** 作业详情（扩展） */
export type JobDetail = {
  id: string;
  kind: string;
  status: string;
  stage: string;
  progress: number;
  retryable: boolean;
  attempt: number;
  max_attempts: number;
  created_at: string;
  updated_at: string;
  created_by: string | null;
  last_error: Record<string, unknown> | null;
  result: Record<string, unknown> | null;
  payload: Record<string, unknown> | null;
};

/** 后端 /jobs 列表原始结构 */
type JobListApiResponse = {
  items: Array<{
    id: string;
    kind: string;
    status: string;
    stage: string;
    progress: number;
    retryable: boolean;
    created_at: string;
    attempt: number;
    max_attempts: number;
  }>;
  next_cursor: string | null;
  has_more: boolean;
};

/** 后端 /jobs/{id}/detail 原始结构 */
type JobDetailApiResponse = {
  id: string;
  kind: string;
  status: string;
  stage: string;
  progress: number;
  retryable: boolean;
  attempt: number;
  max_attempts: number;
  created_at: string;
  updated_at: string;
  created_by: string | null;
  last_error: Record<string, unknown> | null;
  result: Record<string, unknown> | null;
  payload: Record<string, unknown> | null;
};

/** 后端 /jobs/{id}/retry 原始结构 */
type JobRetryApiResponse = {
  job_id: string;
  status: string;
  kind: string;
};

/**
 * 列出作业
 */
export async function apiListJobs(params?: {
  status?: string;
  kind?: string;
  cursor?: string;
  limit?: number;
}): Promise<JobListResponse> {
  const res = await http.get<JobListApiResponse>('/jobs', { params });
  return {
    items: res.data.items.map((j) => ({
      id: j.id,
      kind: j.kind,
      status: j.status,
      stage: j.stage ?? '',
      progress: j.progress ?? 0,
      retryable: j.retryable ?? false,
      created_at: j.created_at,
      attempt: j.attempt ?? 0,
      max_attempts: j.max_attempts ?? 3,
    })),
    next_cursor: res.data.next_cursor,
    has_more: res.data.has_more,
  };
}

/**
 * 获取作业详情
 */
export async function apiGetJobDetail(id: string): Promise<JobDetail> {
  const res = await http.get<JobDetailApiResponse>(`/jobs/${id}/detail`);
  return {
    id: res.data.id,
    kind: res.data.kind,
    status: res.data.status,
    stage: res.data.stage ?? '',
    progress: res.data.progress ?? 0,
    retryable: res.data.retryable ?? false,
    attempt: res.data.attempt ?? 0,
    max_attempts: res.data.max_attempts ?? 3,
    created_at: res.data.created_at,
    updated_at: res.data.updated_at,
    created_by: res.data.created_by,
    last_error: res.data.last_error,
    result: res.data.result,
    payload: res.data.payload,
  };
}

/**
 * 重试作业
 */
export async function apiRetryJob(id: string): Promise<{ id: string; status: string; kind: string }> {
  const res = await http.post<JobRetryApiResponse>(`/jobs/${id}/retry`);
  return {
    id: res.data.job_id,
    status: res.data.status,
    kind: res.data.kind,
  };
}

// ============================================================
// V3 系统健康 API（/health）— IRIP V3-T02
// ============================================================

/** 检查项 */
export type HealthCheck = {
  name: string;
  status: string;
  latency_ms: number | null;
  message: string | null;
};

/** 系统健康响应 */
export type SystemHealth = {
  status: string;
  checks: HealthCheck[];
  migration_version: string | null;
  worker_heartbeat: string | null;
  outbox_backlog: number;
};

/** 后端 /health/ready 原始结构 */
type HealthReadyApiResponse = {
  status: string;
  checks: Record<
    string,
    { status: string; version?: string; error?: string; [key: string]: unknown }
  >;
};

/**
 * 获取系统健康状态
 *
 * 调用 /health/ready 端点，将后端的 checks 字典结构转换为数组结构。
 * 注意：后端在系统未就绪时返回 503，但响应体仍包含健康详情，
 * 此处捕获 503 错误并提取响应体数据。
 */
export async function apiGetSystemHealth(): Promise<SystemHealth> {
  let rawData: HealthReadyApiResponse;
  try {
    const res = await http.get<HealthReadyApiResponse>('/health/ready');
    rawData = res.data;
  } catch (err) {
    // 503 时后端仍返回健康详情，从错误响应中提取
    if (err && typeof err === 'object' && 'response' in err) {
      const response = (err as { response?: { data?: HealthReadyApiResponse; status?: number } }).response;
      if (response?.data && response.status === 503) {
        rawData = response.data;
      } else {
        throw err;
      }
    } else {
      throw err;
    }
  }

  const checks: HealthCheck[] = Object.entries(rawData.checks).map(
    ([name, detail]) => {
      const status = detail.status;
      let message: string | null = null;
      if (detail.error) {
        message = String(detail.error);
      } else if (detail.version) {
        message = `version: ${detail.version}`;
      }
      return {
        name,
        status,
        latency_ms: null,
        message,
      };
    },
  );

  // 从 checks 中提取迁移版本、worker 心跳、outbox 积压
  const dbCheck = rawData.checks['database'];
  const migrationVersion: string | null =
    dbCheck?.version ?? null;

  const outboxCheck = rawData.checks['outbox'];
  const outboxBacklog: number =
    typeof outboxCheck?.stale_undelivered === 'number'
      ? outboxCheck.stale_undelivered
      : 0;

  // Worker 心跳：后端 /health/ready 不直接返回，
  // 从 redis 检查项推断（redis ok = worker 可达）
  const redisCheck = rawData.checks['redis'];
  const workerHeartbeat: string | null =
    redisCheck?.status === 'ok' ? new Date().toISOString() : null;

  const overallStatus: string =
    rawData.status === 'ok' ? 'ok' : 'not_ready';

  return {
    status: overallStatus,
    checks,
    migration_version: migrationVersion,
    worker_heartbeat: workerHeartbeat,
    outbox_backlog: outboxBacklog,
  };
}

// ---------------------------------------------------------------------------
// AI 工具管理 API（/api/v1/ai-tools）
// ---------------------------------------------------------------------------

/** AI 工具 DTO（列表 + 详情共用） */
export type AIToolDTO = {
  name: string;
  display_name: string;
  description: string;
  required_permission: string;
  candidate: boolean;
  parameters_schema: Record<string, unknown>;
  enabled: boolean;
  lock_version: number;
  updated_at: string;
  updated_by: string | null;
};

/** 列出全部 AI 工具（含禁用工具） */
export async function apiListAITools(): Promise<AIToolDTO[]> {
  const res = await http.get<AIToolDTO[]>('/ai-tools');
  return res.data;
}

/** 获取单个 AI 工具详情 */
export async function apiGetAITool(name: string): Promise<AIToolDTO> {
  const res = await http.get<AIToolDTO>(
    `/ai-tools/${encodeURIComponent(name)}`,
  );
  return res.data;
}

/** 新建 AI 工具（仅创建声明层） */
export async function apiCreateAITool(body: {
  name: string;
  display_name: string;
  description: string;
  required_permission: string;
  candidate: boolean;
  parameters_schema: Record<string, unknown>;
}): Promise<AIToolDTO> {
  const res = await http.post<AIToolDTO>('/ai-tools', body);
  return res.data;
}

/** 编辑 AI 工具声明（乐观锁） */
export async function apiUpdateAITool(
  name: string,
  body: {
    display_name: string;
    description: string;
    required_permission: string;
    candidate: boolean;
    parameters_schema: Record<string, unknown>;
    lock_version: number;
  },
): Promise<AIToolDTO> {
  const res = await http.patch<AIToolDTO>(
    `/ai-tools/${encodeURIComponent(name)}`,
    body,
  );
  return res.data;
}

/** 启用/禁用 AI 工具（乐观锁） */
export async function apiToggleAITool(
  name: string,
  body: { enabled: boolean; lock_version: number },
): Promise<AIToolDTO> {
  const res = await http.patch<AIToolDTO>(
    `/ai-tools/${encodeURIComponent(name)}/enabled`,
    body,
  );
  return res.data;
}


// ---------------------------------------------------------------------------
// 组件预览 API（/api/v1/component-preview）
// ---------------------------------------------------------------------------

/** 提示词推荐 */
export async function apiRecommendPrompt(body: {
  artifact_id: string;
  filename: string;
}): Promise<{ prompt: string }> {
  const res = await http.post<{ prompt: string }>(
    '/component-preview/prompt-recommend',
    body,
  );
  return res.data;
}

/** 数据抽取预览 */
export async function apiExtractPreview(body: {
  artifact_id: string;
  filename: string;
  prompt: string;
  tool_type?: string;
}): Promise<{ result: string }> {
  const res = await http.post<{ result: string }>(
    '/component-preview/extract-preview',
    body,
  );
  return res.data;
}