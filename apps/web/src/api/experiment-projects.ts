/**
 * 实验项目管理 API 客户端
 *
 * 端点：
 *   POST   /api/v1/experiment-projects            — 创建项目
 *   GET    /api/v1/experiment-projects            — 分页列表
 *   GET    /api/v1/experiment-projects/{id}       — 详情（含任务统计）
 *   PATCH  /api/v1/experiment-projects/{id}       — 编辑名称/描述
 *   PATCH  /api/v1/experiment-projects/{id}/status — 归档/恢复
 *
 * 风格参考 apps/web/src/api/equipment-flows.ts：纯 async 函数 + http 实例。
 */
import { http } from './client';

// ============================================================
// 类型定义
// ============================================================

export type ExperimentProject = {
  id: string;
  department_id: string;
  code: string;
  display_name: string;
  description: string | null;
  status: string;
  visible_departments: string[];
  visibility_scope: string;
  owner_user_id: string;
  owner_display_name?: string | null;
  created_at: string;
  updated_at: string;
  lock_version: number;
};

export type ExperimentProjectListItem = {
  id: string;
  code: string;
  display_name: string;
  description: string | null;
  department_id: string;
  department_name: string;
  visible_departments: string[];
  status: string;
  task_count: number;
  owner_display_name: string | null;
  fact_count: number;
  created_at: string;
};

export type ExperimentProjectListResponse = {
  items: ExperimentProjectListItem[];
  next_cursor: string | null;
  has_more: boolean;
};

export type ExperimentProjectDetailResponse = ExperimentProject & {
  task_count: number;
  fact_count: number;
  owner_display_name?: string | null;
};

// ============================================================
// API 函数
// ============================================================

export async function apiListExperimentProjects(params?: {
  status?: string;
  department_id?: string;
  cursor?: string;
  limit?: number;
}): Promise<ExperimentProjectListResponse> {
  const res = await http.get<ExperimentProjectListResponse>(
    '/experiment-projects',
    { params },
  );
  return res.data;
}

export async function apiGetExperimentProject(
  id: string,
): Promise<ExperimentProjectDetailResponse> {
  const res = await http.get<ExperimentProjectDetailResponse>(
    `/experiment-projects/${id}`,
  );
  return res.data;
}

export async function apiCreateExperimentProject(body: {
  department_id: string;
  code: string;
  display_name: string;
  description?: string | null;
  visible_departments?: string[];
  owner_user_id: string;
}): Promise<ExperimentProject> {
  const res = await http.post<ExperimentProject>(
    '/experiment-projects',
    body,
  );
  return res.data;
}

export async function apiUpdateExperimentProject(
  id: string,
  body: {
    display_name: string;
    description?: string | null;
    visible_departments?: string[] | null;
    owner_user_id?: string | null;
    lock_version: number;
  },
): Promise<ExperimentProject> {
  const res = await http.patch<ExperimentProject>(
    `/experiment-projects/${id}`,
    body,
  );
  return res.data;
}

export async function apiUpdateExperimentProjectStatus(
  id: string,
  body: { status: string; lock_version: number },
): Promise<ExperimentProject> {
  const res = await http.patch<ExperimentProject>(
    `/experiment-projects/${id}/status`,
    body,
  );
  return res.data;
}

export async function apiDeleteExperimentProject(id: string): Promise<void> {
  await http.delete(`/experiment-projects/${id}`);
}
