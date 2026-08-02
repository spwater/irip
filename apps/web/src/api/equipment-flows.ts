/**
 * V2 Equipment + Components + Flows API
 */
import { http } from './client';
import type { CursorPage } from './types';

// ============================================================
// Equipment API
// ============================================================

export type Equipment = {
  id: string; department_id: string; code: string; display_name: string;
  description: string | null; department_id: string; visible_departments: string[];
  status: string; sort_order: number; created_at: string; updated_at: string; lock_version: number;
};

export type EquipmentListItem = {
  id: string; code: string; display_name: string; description: string | null;
  department_id: string; department_name: string; visible_departments: string[];
  status: string; sort_order: number;
};

export type EquipmentListResponse = { items: EquipmentListItem[]; next_cursor: string | null; has_more: boolean; };

export async function apiListEquipment(params?: { department_id?: string; status?: string; cursor?: string; limit?: number; }): Promise<EquipmentListResponse> { const res = await http.get<EquipmentListResponse>('/equipment', { params }); return res.data; }
export async function apiGetEquipment(id: string): Promise<Equipment> { const res = await http.get<Equipment>(`/equipment/${id}`); return res.data; }
export async function apiCreateEquipment(body: { display_name: string; description?: string; department_id: string; visible_departments?: string[]; sort_order?: number; }): Promise<Equipment> { const res = await http.post<Equipment>('/equipment', body); return res.data; }
export async function apiUpdateEquipment(id: string, body: { display_name: string; description?: string; department_id?: string; visible_departments?: string[]; sort_order?: number; lock_version: number; }): Promise<Equipment> { const res = await http.patch<Equipment>(`/equipment/${id}`, body); return res.data; }
export async function apiUpdateEquipmentStatus(id: string, body: { status: string; lock_version: number; }): Promise<Equipment> { const res = await http.patch<Equipment>(`/equipment/${id}/status`, body); return res.data; }
export async function apiDeleteEquipment(id: string): Promise<void> { await http.delete(`/equipment/${id}`); }

// ============================================================
// V2 Components API
// ============================================================

export type ComponentSummary = { id: string; name: string; display_name: string; description: string; version: string; kind: string; runtime: string; experimental_object_code: string; equipment_id: string | null; status: string; manifest_sha256: string; published_at: string | null; created_at: string; prompt?: string | null; tool_type?: string | null; };
export type ComponentDetail = ComponentSummary & { manifest_yaml: string; active_version_id?: string | null; parameters?: Record<string, unknown>; inputs?: unknown[]; outputs?: unknown[]; };
export type ComponentVersionItem = { id: string; version: string; status: string; manifest_sha256: string; created_at: string; };
export type PersistFactResult = { fact_id: string; revision: number; subject_id: string; raw_count: number; artifact_id: string | null; };

export async function apiListComponents(params?: { kind?: string; status?: string; }): Promise<CursorPage<ComponentSummary>> { const res = await http.get<{ items: ComponentSummary[] }>('/components/', { params }); return { items: res.data.items, next_cursor: null, has_more: false }; }
export async function apiGetComponent(id: string): Promise<ComponentDetail> { const res = await http.get<ComponentDetail>(`/components/${id}`); return res.data; }
export async function apiPublishComponent(body: { manifest_yaml: string; experimental_object_code?: string | null; equipment_id?: string | null; }): Promise<ComponentSummary> { const res = await http.post<ComponentSummary>('/components/', body); return res.data; }
export async function apiListComponentVersions(componentId: string): Promise<ComponentVersionItem[]> { const res = await http.get<ComponentVersionItem[]>(`/components/${componentId}/versions`); return res.data; }
export async function apiArchiveComponent(componentId: string): Promise<void> { await http.patch(`/components/${componentId}/archive`); }
export async function apiRestoreComponent(componentId: string): Promise<void> { await http.patch(`/components/${componentId}/restore`); }
export async function apiActivateVersion(versionId: string): Promise<void> { await http.post(`/components/${versionId}/activate`); }
export async function apiDeleteComponent(componentId: string): Promise<void> { await http.delete(`/components/${componentId}`); }
export async function apiPersistRunAsFact(runId: string, body: { object_id: string; custom_data?: { metadata: Record<string, unknown>; points?: { name: string; value: unknown; unit: string | null }[]; series?: unknown[]; data?: Record<string, unknown>[] } | null; }): Promise<PersistFactResult> { const res = await http.post<PersistFactResult>(`/flows/runs/${runId}/persist-fact`, body); return res.data; }

// ============================================================
// V2 Flows API
// ============================================================

export type FlowSummary = { id: string; code: string; display_name: string; status: string; lock_version: number; department_id: string | null; project_name: string | null; operator: string | null; experimental_object_code: string | null; created_at: string; updated_at: string; latest_version: { id: string; version: number; digest: string; status: string; published_at: string | null; nodes?: Record<string, unknown>[]; edges?: Record<string, unknown>[]; random_seed?: number; } | null; };
export type FlowVersion = { id: string; flow_definition_id: string; version: number; digest: string; random_seed: number; status: string; published_at: string | null; created_at: string; nodes: unknown[]; edges: unknown[]; };
export type FlowRunSummary = { id: string; flow_version_id: string; status: string; job_id: string | null; output_digest: string | null; output_summary: Record<string, unknown> | null; error_message?: string | null; started_at: string | null; completed_at: string | null; created_at: string; persisted_as_fact?: boolean; operator?: string | null; };
export type FlowNodeExecution = { id: string; node_id: string; status: string; input_summary: Record<string, unknown> | null; output_summary: Record<string, unknown> | null; diagnostics: Record<string, unknown> | null; duration_ms: number | null; started_at: string | null; completed_at: string | null; };
export type FlowRunDetail = FlowRunSummary & { node_executions: FlowNodeExecution[]; nodes: FlowNodeExecution[]; };
export type FlowNodeSchema = { node_id: string; component_name: string; component_version: string; params?: Record<string, unknown>; input_bindings?: Record<string, string>; };
export type FlowEdgeSchema = { source_node: string; source_port: string; target_node: string; target_port: string; };

export async function apiCreateFlow(body: { display_name: string; department_id?: string | null; project_name?: string | null; operator: string; experimental_object_code?: string | null; nodes?: FlowNodeSchema[]; edges?: FlowEdgeSchema[]; }): Promise<FlowSummary> { const res = await http.post<FlowSummary>('/flows/', body); return res.data; }
export async function apiPublishFlow(flowId: string, body: { nodes: FlowNodeSchema[]; edges?: FlowEdgeSchema[]; random_seed?: number; }): Promise<FlowSummary> { await http.post(`/flows/${flowId}/publish`, body); return apiGetFlow(flowId); }
export async function apiListFlows(params?: { status?: string; }): Promise<CursorPage<FlowSummary>> { const res = await http.get<{ items: FlowSummary[] }>('/flows/', { params }); return { items: res.data.items, next_cursor: null, has_more: false }; }
export async function apiGetFlow(flowId: string): Promise<FlowSummary> { const res = await http.get<FlowSummary>(`/flows/${flowId}`); return { ...res.data, latest_version: res.data.latest_version ?? null }; }
export async function apiArchiveFlow(flowId: string): Promise<FlowSummary> { const res = await http.post<FlowSummary>(`/flows/${flowId}/archive`); return { ...res.data, latest_version: res.data.latest_version ?? null }; }
export async function apiRestoreFlow(flowId: string): Promise<FlowSummary> { const res = await http.post<FlowSummary>(`/flows/${flowId}/restore`); return { ...res.data, latest_version: res.data.latest_version ?? null }; }
export async function apiDeleteFlow(flowId: string): Promise<void> { await http.delete(`/flows/${flowId}`); }
export async function apiUpdateFlow(flowId: string, displayName: string, departmentId?: string | null, projectName?: string | null, operator?: string | null): Promise<FlowSummary> { const res = await http.patch<FlowSummary>(`/flows/${flowId}`, { display_name: displayName, department_id: departmentId ?? null, project_name: projectName ?? null, operator: operator ?? null }); return { ...res.data, latest_version: res.data.latest_version ?? null }; }
export async function apiCreateFlowRun(flowId: string, body: { inputs?: Record<string, unknown> }): Promise<FlowRunSummary> { const res = await http.post<FlowRunSummary>(`/flows/${flowId}/runs`, body); return res.data; }
export async function apiListFlowRuns(flowId: string): Promise<FlowRunSummary[]> { const res = await http.get<FlowRunSummary[]>(`/flows/${flowId}/runs`); return res.data; }
export async function apiResumeFlowRun(runId: string): Promise<FlowRunSummary> { const res = await http.post<FlowRunSummary>(`/flows/runs/${runId}/resume`); return res.data; }
export async function apiCancelFlowRun(runId: string): Promise<FlowRunSummary> { const res = await http.post<FlowRunSummary>(`/flows/runs/${runId}/cancel`); return res.data; }
export async function apiRetryFlowNode(runId: string, nodeId: string): Promise<FlowRunSummary> { await http.post(`/flows/runs/${runId}/retry/${encodeURIComponent(nodeId)}`); return apiGetFlowRun(runId); }
export async function apiDeleteFlowRun(runId: string): Promise<void> { await http.delete(`/flows/runs/${runId}`); }
export async function apiGetFlowRun(runId: string): Promise<FlowRunDetail> { const res = await http.get<{ id: string; flow_version_id: string; status: string; job_id: string | null; output_digest: string | null; started_at: string | null; completed_at: string | null; created_at: string; node_executions: FlowNodeExecution[]; }>(`/flows/runs/${runId}`); return { ...res.data, output_summary: null, nodes: res.data.node_executions }; }
