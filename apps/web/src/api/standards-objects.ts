/**
 * V1 Standards + Objects + Templates + Methods + Packages + Ingestions API
 *
 * 从 client.ts 拆分，通过 re-export 保持兼容。
 */
import { http } from './client';
import type {
  CursorPage,
  VariableSummary,
  VariableDetail,
  VariableVersion,
  IndustrialObject,
  ObjectRelation,
  DescendantsResponse,
  TemplateSummary,
  MethodSummary,
  PackageSummary,
  SourcePreview,
  MappingRankResponse,
} from './types';

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
