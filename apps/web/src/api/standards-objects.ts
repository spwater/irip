/**
 * V1 Objects + Object Types + Ingestions preview API
 *
 * 标准层空表清理（migration 0057）后仅保留 Objects / Object Types /
 * Ingestions preview 相关函数。Variables / Templates / Packages /
 * Mapping 相关函数已删除（对应后端表与路由已移除）。
 */
import { http } from './client';
import type {
  CursorPage,
  IndustrialObject,
  SourcePreview,
} from './types';

// ============================================================
// Objects API（/objects）
// ============================================================

export async function apiCreateObject(body: {
  display_name: string;
  object_type: string;
  description?: string;
  equipment_id?: string;
  component_id?: string;
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
  component_id?: string | null;
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
// Object Types API（/object-types）
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
