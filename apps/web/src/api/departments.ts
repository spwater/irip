/**
 * 机构/实验室管理 API
 *
 * 从 client.ts 拆分而来，通过 client.ts 的 re-export 保持向后兼容。
 */
import { http } from './client';

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

/**
 * 原子添加用户到实验室（M-05）。
 *
 * 优先调用服务端原子 add API；若后端尚未提供该端点（404/405），
 * 调用方应回退到 apiSetUserDepartments 并配合并发冲突检测。
 */
export async function apiAddUserDepartment(
  userId: string,
  departmentId: string,
): Promise<{ ok: boolean }> {
  const res = await http.post<{ ok: boolean }>(
    `/users/${userId}/departments/${encodeURIComponent(departmentId)}`,
  );
  return res.data;
}

/**
 * 原子移除用户从实验室（M-05）。
 *
 * 优先调用服务端原子 remove API；若后端尚未提供该端点（404/405），
 * 调用方应回退到 apiSetUserDepartments 并配合并发冲突检测。
 */
export async function apiRemoveUserDepartment(
  userId: string,
  departmentId: string,
): Promise<{ ok: boolean }> {
  const res = await http.delete<{ ok: boolean }>(
    `/users/${userId}/departments/${encodeURIComponent(departmentId)}`,
  );
  return res.data;
}
