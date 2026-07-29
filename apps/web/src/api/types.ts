/**
 * V1 业务 API 类型定义 + 通用类型别名 + 工具函数
 *
 * 从 client.ts 拆分而来，通过 client.ts 的 re-export 保持向后兼容。
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
