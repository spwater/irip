/**
 * V1 业务 API 类型定义 + 通用类型别名 + 工具函数
 *
 * 标准层空表清理（migration 0057）后删除了 VariableSummary /
 * VariableDetail / VariableVersion / TemplateSummary / PackageSummary /
 * MappingCandidate / MappingRankResponse 类型。
 */

// ============================================================
// V1 业务 API 类型定义
// ============================================================

/** 通用游标分页响应 */
export type CursorPage<T> = {
  items: T[];
  next_cursor: string | null;
  has_more: boolean;
};

// ---- Objects ----
export type IndustrialObject = {
  id: string;
  code: string;
  display_name: string;
  object_type: string;
  description: string | null;
  status: string;
  department_id: string | null;
  visible_departments: string[];
  visibility_scope: 'private' | 'tree' | 'explicit' | 'all';
  owner_user_id: string | null;
  created_at: string;
  updated_at: string;
  lock_version: number;
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

// ---- Facts ----
export type FactSummary = {
  fact_id: string;
  fact_type: string;
  subject_id: string;
  status: string;
  task_code: string | null;
  task_name: string | null;
  department_name: string | null;
  operator: string | null;
  run_operator: string | null;
  equipment_name: string | null;
  data_summary: string | null;
  created_at: string | null;
  /** 阶段2：可见范围 */
  visibility_scope?: 'private' | 'tree' | 'explicit' | 'all';
  /** 阶段2：所属部门 ID */
  department_id?: string | null;
  /** 阶段2：所有者用户 ID */
  owner_user_id?: string | null;
};

export type FactDetail = {
  fact_id: string;
  fact_type: string;
  subject_id: string;
  status: string;
  /** 阶段2：可见范围 */
  visibility_scope?: 'private' | 'tree' | 'explicit' | 'all';
  /** 阶段2：所属部门 ID */
  department_id?: string | null;
  /** 阶段2：所有者用户 ID */
  owner_user_id?: string | null;
};

// ---- Provenance ----
export type ProvenanceNode = {
  id: string;
  node_type: string;
  label: string;
  version: string;
  status: string;
  /** 是否有权限查看节点详情（后端 RLS 决定，默认 true） */
  accessible?: boolean;
  /** 归属部门名称（accessible=false 时可用于提示） */
  department_name?: string;
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
  /** 阶段2：可见范围 */
  visibility_scope?: 'private' | 'tree' | 'explicit' | 'all';
  /** 阶段2：所属部门 ID */
  department_id?: string | null;
  /** 阶段2：所有者用户 ID */
  owner_user_id?: string | null;
};

export type Recipe = {
  recipe_id: string;
  code: string;
  display_name: string;
  status: string;
  version: number;
  /** 阶段2：可见范围 */
  visibility_scope?: 'private' | 'tree' | 'explicit' | 'all';
  /** 阶段2：所属部门 ID */
  department_id?: string | null;
  /** 阶段2：所有者用户 ID */
  owner_user_id?: string | null;
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
  /** 阶段2：可见范围 */
  visibility_scope?: 'private' | 'tree' | 'explicit' | 'all';
  /** 阶段2：所属部门 ID */
  department_id?: string | null;
  /** 阶段2：所有者用户 ID */
  owner_user_id?: string | null;
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

/** 阶段2：可见范围类型 */
export type VisibilityScope = 'private' | 'tree' | 'explicit' | 'all';

/** 阶段2：A 类表通用租户字段 */
export type TenantFields = {
  department_id: string;
  visible_departments: string[];
  visibility_scope: VisibilityScope;
  owner_user_id: string;
};

/** 阶段2：B 类表通用租户字段 */
export type TenantFieldsB = {
  department_id: string;
};

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
