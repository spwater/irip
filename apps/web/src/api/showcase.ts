/**
 * AI 助手分析橱窗 API：类型定义 + 请求函数。
 *
 * 橱窗卡片与每段对话一对一绑定，用于留存用户从对话消息中精选的内容块。
 * 所有端点前缀 /api/v1/assistant，复用现有 http 客户端。
 */
import { http } from './client';

// ============================================================
// 类型定义
// ============================================================

/** 橱窗卡片块类型 */
export type ShowcaseBlockType =
  | 'echarts'
  | 'plotly'
  | 'table'
  | 'conclusion'
  | 'formula'
  | 'text';

/** 数据来源信息（从 system_context 解析） */
export type DataSourceInfo = {
  /** 样品标签列表 */
  sample_labels: string[];
  /** 任务名称 */
  task_name: string;
  /** 字段/检测指标 */
  fields: string[];
  /** 数据来源标识 */
  source_tag: string;
  /** 数据范围摘要 */
  data_range: string;
};

/** 橱窗卡片（前端类型） */
export type ShowcaseItem = {
  id: string;
  conversation_id: string;
  sort_order: number;
  block_type: ShowcaseBlockType;
  title: string;
  content_snapshot: string;
  source_message_id: string;
  source_block_index: number;
  data_source: DataSourceInfo;
  created_at: string;
  updated_at: string;
};

/** 创建橱窗卡片请求 */
export type CreateShowcaseItemPayload = {
  block_type: ShowcaseBlockType;
  title: string;
  content_snapshot: string;
  source_message_id: string;
  source_block_index: number;
  data_source: DataSourceInfo;
};

/** 更新橱窗卡片请求 */
export type UpdateShowcaseItemPayload = {
  title?: string;
};

/** 重排序请求 */
export type ReorderShowcasePayload = {
  item_ids: string[];
};

/** 摘要响应 */
export type SummaryResponse = {
  markdown: string;
  item_count: number;
};

// ============================================================
// API 响应类型
// ============================================================

type ShowcaseItemApiResponse = {
  id: string;
  conversation_id: string;
  sort_order: number;
  block_type: string;
  title: string;
  content_snapshot: string;
  source_message_id: string;
  source_block_index: number;
  data_source: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

type ShowcaseListApiResponse = {
  items: ShowcaseItemApiResponse[];
};

// ============================================================
// API 函数
// ============================================================

/**
 * 将后端响应转为前端类型（确保 data_source 有默认值）
 */
function toShowcaseItem(r: ShowcaseItemApiResponse): ShowcaseItem {
  const ds = (r.data_source ?? {}) as Partial<DataSourceInfo>;
  return {
    id: r.id,
    conversation_id: r.conversation_id,
    sort_order: r.sort_order,
    block_type: r.block_type as ShowcaseBlockType,
    title: r.title,
    content_snapshot: r.content_snapshot,
    source_message_id: r.source_message_id,
    source_block_index: r.source_block_index,
    data_source: {
      sample_labels: ds.sample_labels ?? [],
      task_name: ds.task_name ?? '',
      fields: ds.fields ?? [],
      source_tag: ds.source_tag ?? '',
      data_range: ds.data_range ?? '',
    },
    created_at: r.created_at,
    updated_at: r.updated_at,
  };
}

/** 列出对话橱窗的卡片（按 sort_order 正序） */
export async function apiListShowcaseItems(
  conversationId: string,
): Promise<ShowcaseItem[]> {
  const res = await http.get<ShowcaseListApiResponse>(
    `/assistant/conversations/${conversationId}/showcase`,
  );
  return res.data.items.map(toShowcaseItem);
}

/** 向对话橱窗添加一个内容块卡片 */
export async function apiAddShowcaseItem(
  conversationId: string,
  payload: CreateShowcaseItemPayload,
): Promise<ShowcaseItem> {
  const res = await http.post<ShowcaseItemApiResponse>(
    `/assistant/conversations/${conversationId}/showcase`,
    payload,
  );
  return toShowcaseItem(res.data);
}

/** 更新橱窗卡片标题 */
export async function apiUpdateShowcaseItem(
  itemId: string,
  payload: UpdateShowcaseItemPayload,
): Promise<ShowcaseItem> {
  const res = await http.patch<ShowcaseItemApiResponse>(
    `/assistant/showcase/${itemId}`,
    payload,
  );
  return toShowcaseItem(res.data);
}

/** 删除橱窗卡片 */
export async function apiDeleteShowcaseItem(itemId: string): Promise<void> {
  await http.delete(`/assistant/showcase/${itemId}`);
}

/** 批量更新橱窗卡片排序 */
export async function apiReorderShowcaseItems(
  conversationId: string,
  itemIds: string[],
): Promise<void> {
  await http.patch(`/assistant/conversations/${conversationId}/showcase/reorder`, {
    item_ids: itemIds,
  });
}

/** 基于橱窗卡片生成 Markdown 分析摘要 */
export async function apiGenerateSummary(
  conversationId: string,
): Promise<SummaryResponse> {
  const res = await http.post<SummaryResponse>(
    `/assistant/conversations/${conversationId}/summary`,
  );
  return res.data;
}
