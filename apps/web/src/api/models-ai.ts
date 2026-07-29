/**
 * V2 Models + Files + AI Assistant API
 */
import { http } from './client';
import type { CursorPage } from './types';

// ============================================================
// Files API
// ============================================================

export type FileItem = { name: string; type: string; size: number | null; };
export type BrowseResponse = { current_path: string; parent_path: string | null; items: FileItem[]; };
export type UploadResponse = { artifact_id: string; filename: string; size: number; };

export async function apiBrowseFiles(path?: string): Promise<BrowseResponse> { const res = await http.get<BrowseResponse>('/files/browse', { params: path ? { path } : {} }); return res.data; }
export async function apiUploadFile(file: File): Promise<UploadResponse> { const formData = new FormData(); formData.append('file', file); const res = await http.post<UploadResponse>('/files/upload', formData); return res.data; }
export async function apiGetArtifactDownloadUrl(artifactId: string): Promise<string> { const res = await http.get<{ download_url: string }>(`/artifacts/${artifactId}/download`); return res.data.download_url; }

// ============================================================
// Models API
// ============================================================

export type ModelSummary = { id: string; code: string; display_name: string; status: string; current_version_id: string | null; lock_version: number; created_at: string; updated_at: string; };
export type ModelVersionSummary = { id: string; model_id: string; version: number; status: string; contract_sha256: string | null; model_artifact_id: string | null; metrics: Record<string, unknown>; applicability_domain: Record<string, unknown>; code_hash: string | null; dependency_hash: string | null; model_hash: string | null; created_at: string; published_at: string | null; };
export type PredictionResult = { model_id: string; model_version_id: string; version: number; predictions: Record<string, unknown>; metadata: Record<string, unknown>; fact_id: string | null; };

export async function apiCreateModel(body: { code: string; display_name: string; }): Promise<ModelSummary> { const res = await http.post<ModelSummary>('/models/', body); return res.data; }
export async function apiListModels(params?: { status?: string; }): Promise<CursorPage<ModelSummary>> { const res = await http.get<{ items: ModelSummary[] }>('/models/', { params }); return { items: res.data.items, next_cursor: null, has_more: false }; }
export async function apiGetModel(modelId: string): Promise<ModelSummary> { const res = await http.get<ModelSummary>(`/models/${modelId}`); return res.data; }
export async function apiGetModelVersions(modelId: string): Promise<ModelVersionSummary[]> { const res = await http.get<{ items: ModelVersionSummary[] }>(`/models/${modelId}/versions`); return res.data.items; }
export async function apiValidateModelVersion(modelId: string, versionId: string, body: { dataset_artifact_id?: string; metrics?: Record<string, unknown>; applicability_domain?: Record<string, unknown>; }): Promise<ModelVersionSummary> { const res = await http.post<ModelVersionSummary>(`/models/${modelId}/versions/${versionId}/validate`, body); return res.data; }
export async function apiPublishModelVersion(modelId: string, versionId: string): Promise<ModelSummary> { const res = await http.post<ModelSummary>(`/models/${modelId}/versions/${versionId}/publish`); return res.data; }
export async function apiRollbackModel(modelId: string, targetVersionId: string): Promise<ModelSummary> { const res = await http.post<ModelSummary>(`/models/${modelId}/rollback`, { target_version_id: targetVersionId }); return res.data; }
export async function apiPredictModel(modelId: string, body: { inputs: Record<string, unknown> }): Promise<PredictionResult> { const res = await http.post<PredictionResult>(`/models/${modelId}/predict`, body); return res.data; }
export async function apiDeprecateModel(modelId: string): Promise<ModelSummary> { const res = await http.post<ModelSummary>(`/models/${modelId}/deprecate`); return res.data; }

// ============================================================
// AI Assistant API
// ============================================================

export async function apiListIngestionTools(): Promise<UnifiedToolDTO[]> {
  const res = await http.get<UnifiedToolDTO[]>('/ai-tools/ingestion/list');
  return res.data;
}

export type ToolCallSummary = { tool: string; args: Record<string, unknown>; summary: string; status: string; };
export type Citation = { object_type: string; object_id: string; version: string; label: string; href: string; };
export type ConversationSummary = { id: string; title: string; provider_mode: string; pinned: boolean; archived: boolean; created_at: string; updated_at: string; system_context: string | null; };
export type AssistantMessage = { id: string; conversation_id: string; role: 'user' | 'assistant' | 'tool'; content: string; tool_calls: ToolCallSummary[]; citations: Citation[]; uncertainty: string | null; created_at: string; };
export type AskResponse = { conversation_id: string; answer: string; tool_calls: ToolCallSummary[]; citations: Citation[]; uncertainty: string | null; provider_mode: string; };
export type ToolInfo = { name: string; display_name: string; description: string; required_permission: string; candidate: boolean; };
export type ProviderStatus = { provider_mode: string; whitelist_tools: ToolInfo[]; candidate_tools: ToolInfo[]; };

type ConversationApiResponse = { id: string; title: string; provider_mode: string; pinned: boolean; archived: boolean; created_at: string; updated_at: string; system_context: string | null; };
type MessageListApiResponse = { items: Array<{ id: string; conversation_id: string; role: string; content: string; tool_calls: ToolCallSummary[]; citations: Citation[]; uncertainty: string | null; created_at: string; }>; };
type ConversationListApiResponse = { items: ConversationApiResponse[]; };
type ProviderStatusApiResponse = { provider_mode: string; whitelist_tools: ToolInfo[]; candidate_tools: ToolInfo[]; };
type AskApiResponse = { conversation_id: string; answer: string; tool_calls: ToolCallSummary[]; citations: Citation[]; uncertainty: string | null; provider_mode: string; };

export async function apiCreateConversation(body: { title?: string; provider_mode?: string; }): Promise<ConversationSummary> { const res = await http.post<ConversationApiResponse>('/assistant/conversations', { title: body.title ?? '', provider_mode: body.provider_mode ?? 'offline' }); return res.data; }
export async function apiListConversations(params?: { limit?: number; includeArchived?: boolean; archivedOnly?: boolean; }): Promise<ConversationSummary[]> { const res = await http.get<ConversationListApiResponse>('/assistant/conversations', { params: { limit: params?.limit ?? 50, include_archived: params?.includeArchived ?? false, archived_only: params?.archivedOnly ?? false } }); return res.data.items; }
export async function apiTogglePin(conversationId: string): Promise<ConversationSummary> { const res = await http.patch<ConversationSummary>(`/assistant/conversations/${conversationId}/pin`); return res.data; }
export async function apiToggleArchive(conversationId: string): Promise<ConversationSummary> { const res = await http.patch<ConversationSummary>(`/assistant/conversations/${conversationId}/archive`); return res.data; }
export async function apiDeleteConversation(conversationId: string): Promise<void> { await http.delete(`/assistant/conversations/${conversationId}`); }
export async function apiCancelRequest(conversationId: string): Promise<void> { await http.post(`/assistant/conversations/${conversationId}/cancel`); }
export async function apiSendMessage(conversationId: string, body: { question: string; provider_name?: string; thinking_enabled?: boolean; system_context?: string; }, signal?: AbortSignal): Promise<AskResponse> { const res = await http.post<AskApiResponse>(`/assistant/conversations/${conversationId}/messages`, { question: body.question, provider_name: body.provider_name ?? 'openai_compatible', thinking_enabled: body.thinking_enabled ?? false, system_context: body.system_context ?? null }, { signal }); return res.data; }
export async function apiListMessages(conversationId: string): Promise<AssistantMessage[]> { const res = await http.get<MessageListApiResponse>(`/assistant/conversations/${conversationId}/messages`); return res.data.items.map((m) => ({ ...m, role: (m.role as 'user' | 'assistant' | 'tool') ?? 'user' })); }
export async function apiGetProviderStatus(): Promise<ProviderStatus> { const res = await http.get<ProviderStatusApiResponse>('/assistant/provider-status'); return res.data; }

// ============================================================
// AI Tools + Component Preview API
// ============================================================

export type AIToolDTO = { name: string; display_name: string; description: string; required_permission: string; candidate: boolean; parameters_schema: Record<string, unknown>; enabled: boolean; lock_version: number; updated_at: string; updated_by: string | null; };

export async function apiListAITools(): Promise<AIToolDTO[]> { const res = await http.get<AIToolDTO[]>('/ai-tools'); return res.data; }
export async function apiGetAITool(name: string): Promise<AIToolDTO> { const res = await http.get<AIToolDTO>(`/ai-tools/${encodeURIComponent(name)}`); return res.data; }
export async function apiCreateAITool(body: { name: string; display_name: string; description: string; required_permission: string; candidate: boolean; parameters_schema: Record<string, unknown>; }): Promise<AIToolDTO> { const res = await http.post<AIToolDTO>('/ai-tools', body); return res.data; }
export async function apiUpdateAITool(name: string, body: { display_name: string; description: string; required_permission: string; candidate: boolean; parameters_schema: Record<string, unknown>; lock_version: number; }): Promise<AIToolDTO> { const res = await http.patch<AIToolDTO>(`/ai-tools/${encodeURIComponent(name)}`, body); return res.data; }
export async function apiToggleAITool(name: string, body: { enabled: boolean; lock_version: number; }): Promise<AIToolDTO> { const res = await http.patch<AIToolDTO>(`/ai-tools/${encodeURIComponent(name)}/enabled`, body); return res.data; }

// ============================================================
// Unified Tools API (AI 工具 + 组件插件统一视图)
// ============================================================

/** 统一工具/插件 DTO（AI 工具 + 组件插件汇总） */
export type UnifiedToolDTO = {
  name: string;
  display_name: string;
  description: string;
  /** 数据来源：ai_tool 或 component */
  source: 'ai_tool' | 'component';
  /** 是否启用（AI 工具为真实状态；组件为 status===published） */
  enabled: boolean;
  /** 状态：AI 工具 enabled/disabled；组件 published/deprecated */
  status: string;
  /** 类型：AI 工具 readonly/candidate；组件 ingestion 等 */
  kind: string;
  /** 是否候选（仅 AI 工具有意义） */
  candidate: boolean;
  /** 乐观锁版本号（仅 AI 工具有意义） */
  lock_version: number;
  /** 更新时间 ISO 字符串 */
  updated_at: string;
  /** 最后修改人（仅 AI 工具有意义） */
  updated_by: string | null;
  /** 所需权限（仅 AI 工具有意义） */
  required_permission: string;
  /** 参数 JSON Schema（仅 AI 工具有意义） */
  parameters_schema: Record<string, unknown>;
  /** 组件版本号（仅组件有意义） */
  version: string;
  /** 组件运行时类型（仅组件有意义） */
  runtime: string;
  /** 组件 UUID（仅组件有意义，用于归档/恢复操作） */
  component_id: string;
  /** 组件版本 UUID（仅组件有意义，用于编辑跳转） */
  version_id: string;
  /** 工具分类：ai_tool 或 ingestion */
  category: string;
};

/** 列出统一工具/插件（AI 工具 + 组件插件） */
export async function apiListUnifiedTools(): Promise<UnifiedToolDTO[]> {
  const res = await http.get<UnifiedToolDTO[]>('/ai-tools/unified');
  return res.data;
}

export async function apiRecommendPrompt(body: { artifact_id: string; filename: string; }): Promise<{ prompt: string }> { const res = await http.post<{ prompt: string }>('/component-preview/prompt-recommend', body); return res.data; }
export async function apiExtractPreview(body: { artifact_id: string; filename: string; prompt: string; tool_type?: string; }): Promise<{ result: string }> { const res = await http.post<{ result: string }>('/component-preview/extract-preview', body); return res.data; }
