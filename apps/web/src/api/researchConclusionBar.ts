/**
 * 推送结论栏 API：类型定义 + 请求函数。
 *
 * 结论栏条目按 Workspace 聚合，来自不同轮次报告区块的推送快照。
 * 所有端点前缀 /api/v1/research，复用现有 http 客户端。
 */
import { http } from './client';

// ============================================================
// 类型定义
// ============================================================

/** 结论栏条目块类型 */
export type BarBlockType =
  | 'echarts'
  | 'chart_ref'
  | 'structured'
  | 'table'
  | 'text';

/** 溯源信息 */
export type BarSourceInfo = {
  turn_number?: number | null;
  snapshot_number?: number | null;
  question_text?: string | null;
  block_index?: number | null;
  preceding_text?: string | null;
};

/** 结论栏条目 */
export type BarItem = {
  id: string;
  workspace_id: string;
  turn_id: string;
  block_type: BarBlockType;
  title: string;
  /** 数据快照：echarts option / structured {metadata,points,series} / table {columns,rows} / text {text} */
  content_snapshot: Record<string, unknown>;
  source_info: BarSourceInfo;
  created_at: string;
};

/** 推送条目请求体 */
export type PushBarItemPayload = {
  block_type: BarBlockType;
  title: string;
  content_snapshot: Record<string, unknown>;
  block_index: number;
  source_info: BarSourceInfo;
};

/** 生成最终结论请求体 */
export type FinalizePayload = {
  item_ids: string[];
  title?: string;
  idempotency_key: string;
};

/** 生成最终结论响应 */
export type FinalizeResponse = {
  conclusion_id: string;
  statement: string;
  item_count: number;
};

// ============================================================
// API 响应类型
// ============================================================

type BarItemApiResponse = {
  id: string;
  workspace_id: string;
  turn_id: string;
  block_type: string;
  title: string;
  content_snapshot: Record<string, unknown>;
  source_info: Record<string, unknown>;
  created_at: string;
};

type BarItemListApiResponse = {
  items: BarItemApiResponse[];
};

// ============================================================
// 工具函数
// ============================================================

/** 将后端响应转为前端类型 */
function toBarItem(r: BarItemApiResponse): BarItem {
  return {
    id: r.id,
    workspace_id: r.workspace_id,
    turn_id: r.turn_id,
    block_type: r.block_type as BarBlockType,
    title: r.title,
    content_snapshot: r.content_snapshot ?? {},
    source_info: (r.source_info ?? {}) as BarSourceInfo,
    created_at: r.created_at,
  };
}

/** 生成幂等键 */
export function genBarIdempotencyKey(): string {
  return `web-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

// ============================================================
// API 函数
// ============================================================

const BASE = '/research';

/** 列出工作空间结论栏条目（按 created_at 倒序） */
export async function apiListBarItems(
  workspaceId: string,
): Promise<BarItem[]> {
  const res = await http.get<BarItemListApiResponse>(
    `${BASE}/workspaces/${workspaceId}/conclusion-bar/items`,
  );
  return (res.data.items ?? []).map(toBarItem);
}

/** 推送一个报告区块到结论栏 */
export async function apiPushBarItem(
  workspaceId: string,
  turnId: string,
  payload: PushBarItemPayload,
): Promise<BarItem> {
  const res = await http.post<BarItemApiResponse>(
    `${BASE}/workspaces/${workspaceId}/turns/${turnId}/conclusion-bar/items`,
    payload,
  );
  return toBarItem(res.data);
}

/** 从结论栏移除条目 */
export async function apiRemoveBarItem(
  workspaceId: string,
  itemId: string,
): Promise<void> {
  await http.delete(
    `${BASE}/workspaces/${workspaceId}/conclusion-bar/items/${itemId}`,
  );
}

/** 勾选条目 → 生成最终结论 */
export async function apiFinalizeConclusion(
  workspaceId: string,
  payload: FinalizePayload,
): Promise<FinalizeResponse> {
  const res = await http.post<FinalizeResponse>(
    `${BASE}/workspaces/${workspaceId}/conclusion-bar/finalize`,
    payload,
  );
  return res.data;
}
